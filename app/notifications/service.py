"""
Orchestration : regroupe les alertes par producteur, génère l'audio dans la
langue de la zone, et envoie sur WhatsApp (Meta Cloud API).
"""
from sqlalchemy import func, and_

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.zone import Region
from app.models.indice import IndiceVegetation
from app.models.notification import NotificationLog
from app.scoping import parcelle_ids

from .settings import WHATSAPP, INDICATIF_PAYS, ENVOYER_TEXTE
from .langue import langue_pour, langue_producteur, message, MESSAGES
from .tts import generer_audio
from .whatsapp import WhatsAppClient, normaliser_numero

SEUIL_NDVI = 0.25
SEUIL_NDMI = 0.10


def _parcelles_en_alerte(user):
    """[(producteur, region_nom, code)] pour les parcelles en alerte, cloisonné."""
    sub = (db.session.query(
                IndiceVegetation.parcelle_id.label("pid"),
                func.max(IndiceVegetation.date).label("d"))
           .group_by(IndiceVegetation.parcelle_id).subquery())
    q = (db.session.query(Producteur, Region.nom, Parcelle.code)
         .join(Parcelle, Parcelle.producteur_id == Producteur.id)
         .join(IndiceVegetation, IndiceVegetation.parcelle_id == Parcelle.id)
         .join(sub, and_(sub.c.pid == IndiceVegetation.parcelle_id,
                         sub.c.d == IndiceVegetation.date))
         .outerjoin(Region, Region.id == Producteur.region_id)
         .filter(IndiceVegetation.ndvi < SEUIL_NDVI,
                 IndiceVegetation.ndmi < SEUIL_NDMI,
                 Producteur.telephone.isnot(None)))
    pids = parcelle_ids(user)
    if pids is not None:
        q = q.filter(Parcelle.id.in_(pids))
    return q.all()


def _grouper(rows):
    """{producteur_id: {"prod":Producteur, "region":str, "codes":[...]}}"""
    g = {}
    for prod, region, code in rows:
        e = g.setdefault(prod.id, {"prod": prod, "region": region, "codes": []})
        e["codes"].append(code)
    return g


def apercu(user):
    """Résumé sans envoi : nb producteurs joignables et parcelles concernées."""
    groupes = _grouper(_parcelles_en_alerte(user))
    return {
        "producteurs": len(groupes),
        "parcelles": sum(len(v["codes"]) for v in groupes.values()),
    }


def envoyer(user, base_static, client=None, type_alerte="stress"):
    """Envoie les alertes audio. Retourne un résumé {envoyes, echecs, details:[...]}."""
    client = client or WhatsAppClient(
        WHATSAPP["token"], WHATSAPP["phone_number_id"], WHATSAPP["api_version"])
    groupes = _grouper(_parcelles_en_alerte(user))

    envoyes, echecs, details = 0, 0, []
    for e in groupes.values():
        prod, region, codes = e["prod"], e["region"], e["codes"]
        numero = normaliser_numero(prod.telephone, INDICATIF_PAYS)
        lang = langue_producteur(prod, region)
        statut, detail = "envoye", ""
        if not numero:
            statut, detail = "echec", "numéro invalide"
        else:
            try:
                texte_local = message(lang, type_alerte, prod.nom_complet, codes)
                texte_fr = message("fr", type_alerte, prod.nom_complet, codes)
                try:
                    chemin, tmp = generer_audio(texte_fr, texte_local, lang,
                                                type_alerte, base_static)
                    media_id = client.upload_media(chemin, "audio/mpeg")
                    client.send_audio(numero, media_id)
                    if ENVOYER_TEXTE:
                        client.send_text(numero, texte_local)
                except Exception as audio_exc:   # audio indisponible -> repli texte
                    client.send_text(numero, texte_local)
                    statut = "envoye"
                    detail = "audio indisponible, message texte envoyé"
            except Exception as exc:   # noqa
                statut, detail = "echec", str(exc)[:200]

        (details.append((prod.nom_complet, numero, lang, statut, detail)))
        db.session.add(NotificationLog(
            producteur_id=prod.id, numero=numero, langue=lang,
            type_alerte=type_alerte, statut=statut, detail=detail))
        if statut == "envoye":
            envoyes += 1
        else:
            echecs += 1

    db.session.commit()
    return {"envoyes": envoyes, "echecs": echecs, "details": details}
