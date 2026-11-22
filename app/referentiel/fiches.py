"""
Fiches techniques : dépôt, lecture (Word, PDF, OCR), extraction des rubriques, validation
et application au référentiel, puis suivi de l'itinéraire technique sur chaque parcelle.
"""
import json
import os
import uuid
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import text
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.referentiel import (CultureRef, VarieteRef, FicheTechnique, ItineraireRef, RavageurRef,
                                    IrrigationRef, OperationParcelle)
from app.referentiel import extraction, lecture

CATEGORIES = ["Semis", "Préparation", "Protection", "Fumure", "Installation", "Entretien", "Irrigation", "Récolte"]
TYPES_ENNEMI = ["Ravageur", "Maladie", "Adventice"]
SYSTEMES = ["Gravitaire", "Aspersion", "Goutte-à-goutte"]
# mode d'irrigation du calcul des besoins en eau → système des doses de la fiche
SYSTEME_DE_MODE = {"Goutte-à-goutte": "Goutte-à-goutte", "Aspersion": "Aspersion",
                   "Arrosage manuel / raie": "Gravitaire"}
TOLERANCE_J = 7          # une opération non réalisée 7 jours après sa date prévue est en retard
STADES_GENERIQUES = (0.15, 0.25, 0.40, 0.20)


def dossier():
    d = current_app.config.get("FICHES_DIR") or os.path.join(current_app.instance_path, "fiches_techniques")
    os.makedirs(d, exist_ok=True)
    return d


def chemin(fiche):
    return os.path.join(dossier(), fiche.fichier_chemin)


# --------------------------------------------------------------------------
# Dépôt et lecture
# --------------------------------------------------------------------------
def deviner_culture(texte, nom_fichier=""):
    """Culture du référentiel la plus citée dans la fiche (le nom du fichier compte double)."""
    t = " " + extraction.norm(texte) + " "
    f = " " + extraction.norm(os.path.splitext(nom_fichier or "")[0].replace("_", " ")) + " "
    meilleur, score_max = None, 0
    for c in db.session.query(CultureRef).all():
        noms = {extraction.norm(c.nom)} | {extraction.norm(x) for x in (c.synonymes or "").split(",") if x.strip()}
        noms = {n for n in noms if len(n) >= 3}
        score = sum(t.count(f" {n} ") for n in noms) + 10 * sum(1 for n in noms if f" {n} " in f)
        # « oignon vert » contient « oignon » : on privilégie le nom le plus long reconnu
        score += 0.1 * max((len(n) for n in noms if f" {n} " in t), default=0)
        if score > score_max:
            meilleur, score_max = c, score
    return meilleur if score_max >= 1 else None


def deposer(fichier, user_id):
    """Enregistre le fichier, lit son texte et propose les rubriques extraites."""
    nom = fichier.filename or "fiche"
    ext = os.path.splitext(nom)[1].lower()
    if ext not in lecture.FORMATS:
        raise lecture.LectureErreur(f"« {nom} » : format non pris en charge (Word .docx, PDF ou image).")
    stock = f"{uuid.uuid4().hex[:12]}_{secure_filename(nom) or 'fiche' + ext}"
    p = os.path.join(dossier(), stock)
    fichier.save(p)
    f = FicheTechnique(fichier_nom=nom[:255], fichier_chemin=stock, taille_ko=round(os.path.getsize(p) / 1024),
                       depose_par=user_id, statut="À valider", titre=os.path.splitext(nom)[0][:200])
    try:
        f.texte, f.methode, f.confiance_ocr = lecture.lire(p)
    except lecture.LectureErreur as exc:
        f.texte, f.methode, f.statut = "", "Illisible", "Illisible"
        f.extraction = json.dumps({"erreur": str(exc)}, ensure_ascii=False)
        db.session.add(f)
        db.session.commit()
        return f
    analyser(f)
    db.session.add(f)
    db.session.commit()
    return f


def analyser(f):
    """(Re)calcule les rubriques à partir du texte lu."""
    r = extraction.extraire(f.texte or "")
    c = deviner_culture(f.texte, f.fichier_nom)
    f.culture_id = c.id if c else None
    if c:
        f.titre = f"Fiche technique {c.nom}"
    r["itineraire"] = extraction.itineraire(r)
    r["culture_nom"] = c.nom if c else (r.get("titre") or "")
    if f.methode and f.methode.startswith("OCR") and f.confiance_ocr is not None and f.confiance_ocr < 75:
        r["conflits"].insert(0, f"Lecture OCR de qualité moyenne (confiance {f.confiance_ocr:.0f} %) : "
                                "relire chaque valeur avec le texte.")
    f.extraction = json.dumps(r, ensure_ascii=False, default=str)
    return r


def donnees(f):
    try:
        return json.loads(f.extraction or "{}")
    except ValueError:
        return {}


# --------------------------------------------------------------------------
# Formulaire de validation → données
# --------------------------------------------------------------------------
def _f(v):
    v = (v or "").strip().replace(",", ".").replace(" ", "")
    if not v:
        return None
    return float(v)


def _i(v):
    x = _f(v)
    return int(round(x)) if x is not None else None


def _lignes(form, prefixe, champs):
    """Lignes d'un tableau du formulaire (champs prefixe_<champ>_<n>), lignes vides ignorées."""
    idx = sorted({int(k.rsplit("_", 1)[1]) for k in form if k.startswith(prefixe + "_") and k.rsplit("_", 1)[1].isdigit()})
    out = []
    for n in idx:
        d = {c: (form.get(f"{prefixe}_{c}_{n}") or "").strip() for c in champs}
        d["_coche"] = form.get(f"{prefixe}_opt_{n}") == "on"
        if any(d[c] for c in champs[:1]):
            out.append(d)
    return out


def lire_formulaire(form, avant):
    """Données validées par l'administrateur (mêmes clés que l'extraction). Lève ValueError."""
    r = dict(avant)
    erreurs = []

    def nb(cle, conv=_f, bornes=(0, 100000)):
        try:
            v = conv(form.get(cle))
        except ValueError:
            erreurs.append(f"« {form.get(cle)} » n'est pas un nombre.")
            return None
        if v is not None and not (bornes[0] <= v <= bornes[1]):
            erreurs.append(f"Valeur hors limites : {v:g}.")
        return v

    r["culture_id"] = form.get("culture_id", type=int)
    r["nouvelle_culture"] = (form.get("nouvelle_culture") or "").strip()
    r["systeme"] = form.get("systeme") or None
    r["nom_scientifique"] = (form.get("nom_scientifique") or "").strip() or None
    r["reference"] = "repiquage" if form.get("reference") == "repiquage" else "semis"
    r["cycle_j"] = nb("cycle_j", _i, (1, 800))
    r["rendement"] = [nb("rdt_min", bornes=(0, 300)), nb("rdt_max", bornes=(0, 300))]
    r["potentiel"] = nb("potentiel", bornes=(0, 300))
    r["fumure_org"] = [nb("org_min", bornes=(0, 200)), nb("org_max", bornes=(0, 200))]
    fk = nb("fond_kg", bornes=(0, 5000))
    r["fond"] = {"kg": fk, "engrais": (form.get("fond_engrais") or "").strip() or None} if fk else None
    r["ecartements"] = (form.get("ecartements") or "").strip() or None
    r["periode"] = (form.get("periode") or "").strip() or None
    r["varietes"] = [{"nom": v.strip(), "saisons": []} for v in (form.get("varietes") or "").splitlines() if v.strip()]
    anciennes = {extraction.norm(v["nom"]): v for v in avant.get("varietes") or []}
    for v in r["varietes"]:
        v["saisons"] = (anciennes.get(extraction.norm(v["nom"])) or {}).get("saisons", [])

    r["irrigation"] = []
    for d in _lignes(form, "irr", ["debut", "systeme", "phase", "libelle", "fin"]):
        try:
            r["irrigation"].append({"systeme": d["systeme"], "phase": _i(d["phase"]) or 1, "libelle": d["libelle"],
                                    "debut": _f(d["debut"]), "fin": _f(d["fin"]) if d["fin"] else _f(d["debut"])})
        except ValueError:
            erreurs.append(f"Irrigation : dose invalide ({d['debut']} / {d['fin']}).")
    r["itineraire"] = []
    for d in _lignes(form, "it", ["libelle", "categorie", "jour", "dose", "produit", "details"]):
        try:
            j = _i(d["jour"])
            if j is None:
                raise ValueError
            r["itineraire"].append({"libelle": d["libelle"][:200], "categorie": d["categorie"] or "Entretien",
                                    "jour": j, "dose": _f(d["dose"]), "produit": d["produit"][:80] or None,
                                    "details": d["details"], "optionnel": d["_coche"]})
        except ValueError:
            erreurs.append(f"Itinéraire : « {d['libelle']} » sans jour valide.")
    r["itineraire"].sort(key=lambda o: o["jour"])
    r["ennemis"] = []
    for d in _lignes(form, "rv", ["nom", "type", "precisions", "ma", "traitement"]):
        r["ennemis"].append({"nom": d["nom"][:120], "type": d["type"] or extraction.type_ennemi(d["nom"]),
                             "precisions": d["precisions"][:255],
                             "matieres_actives": [x.strip() for x in d["ma"].split(",") if x.strip()],
                             "traitement": d["traitement"]})
    if erreurs:
        raise ValueError(" ".join(erreurs[:5]))
    return r


# --------------------------------------------------------------------------
# Application au référentiel
# --------------------------------------------------------------------------
def _stades(c, cycle):
    anciens = [c.l_ini, c.l_dev, c.l_mid, c.l_fin]
    prop = [x / sum(anciens) for x in anciens] if all(anciens) and sum(anciens) else list(STADES_GENERIQUES)
    s = [round(p * cycle) for p in prop]
    s[2] += cycle - sum(s)
    c.l_ini, c.l_dev, c.l_mid, c.l_fin = s


def appliquer(f, r, user_id):
    """Écrit les valeurs validées dans le référentiel. Retourne un résumé des changements."""
    c = db.session.get(CultureRef, r.get("culture_id")) if r.get("culture_id") else None
    nouvelle = False
    if c is None:
        nom = (r.get("nouvelle_culture") or "").strip()
        if not nom:
            raise ValueError("Choisissez la spéculation de la fiche, ou donnez le nom d'une nouvelle spéculation.")
        from app.referentiel.service import culture_ref
        c = culture_ref(nom)
        if c is None:
            c = CultureRef(nom=nom[:60], synonymes=extraction.sans_accents(nom).lower(),
                           systeme=r.get("systeme") or "Irrigué", famille=None)
            db.session.add(c)
            db.session.flush()
            nouvelle = True
    res = {"culture": c.nom, "nouvelle": nouvelle, "varietes": 0, "itineraire": 0, "ennemis": 0, "irrigation": 0}

    c.nom_scientifique = r.get("nom_scientifique") or c.nom_scientifique
    c.reference_j0 = r.get("reference") or c.reference_j0
    if r.get("cycle_j") and r["cycle_j"] != c.cycle_j:
        _stades(c, r["cycle_j"])
        c.cycle_j = r["cycle_j"]
    rdt = r.get("rendement") or [None, None]
    if rdt[0] is not None or rdt[1] is not None:
        c.rdt_min, c.rdt_max = rdt[0], rdt[1]
    pot_change = r.get("potentiel") is not None and r["potentiel"] != c.rdt_potentiel_t
    if r.get("potentiel") is not None:
        c.rdt_potentiel_t = r["potentiel"]
    org = r.get("fumure_org") or [None, None]
    if org[0] is not None or org[1] is not None:
        c.fumure_org_min_t, c.fumure_org_max_t = org[0], org[1] if org[1] is not None else org[0]
    if r.get("fond"):
        c.fond_kg, c.engrais_fond = r["fond"]["kg"], r["fond"]["engrais"]
    # fumure de couverture du référentiel = apports minéraux de l'itinéraire (hors urée)
    couv = [o for o in r.get("itineraire") or [] if o["categorie"] == "Fumure" and o["jour"] > 0 and o.get("dose")
            and o.get("produit") and "ur" not in extraction.norm(o["produit"]) and "fumier" not in extraction.norm(o["produit"])]
    if couv:
        c.engrais_couverture = couv[0]["produit"]
        c.couverture_kg = couv[0]["dose"]
        c.apports_j = ",".join(str(o["jour"]) for o in couv)
    if r.get("ecartements"):
        c.ecartements = r["ecartements"][:255]
    if r.get("periode"):
        c.periode = r["periode"][:255]

    # variétés : ajout de celles qui manquent (jamais de suppression)
    existantes = {extraction.norm(v.nom).replace(" ", "") for v in c.varietes}
    for v in r.get("varietes") or []:
        k = extraction.norm(v["nom"]).replace(" ", "")
        if k and k not in existantes:
            db.session.add(VarieteRef(culture_id=c.id, nom=v["nom"][:80], source=f"Fiche technique {c.nom}"[:255],
                                      notes=("Saisons : " + ", ".join(v.get("saisons") or [])) if v.get("saisons") else None))
            existantes.add(k)
            res["varietes"] += 1

    # itinéraire : mise à jour en place pour conserver le suivi déjà saisi sur les parcelles
    anciens = c.itineraire.all()
    libres = list(anciens)
    for n, o in enumerate(r.get("itineraire") or []):
        cible = next((a for a in libres if extraction.norm(a.libelle) == extraction.norm(o["libelle"]) and a.jour == o["jour"]), None) \
            or next((a for a in libres if extraction.norm(a.libelle) == extraction.norm(o["libelle"])), None)
        if cible is None:
            cible = ItineraireRef(culture_id=c.id)
            db.session.add(cible)
        else:
            libres.remove(cible)
        cible.fiche_id, cible.ordre, cible.categorie, cible.libelle = f.id, n, o["categorie"], o["libelle"]
        cible.jour, cible.dose_kg_ha, cible.produit = o["jour"], o.get("dose"), o.get("produit")
        cible.details, cible.optionnel = o.get("details"), bool(o.get("optionnel"))
        res["itineraire"] += 1
    for a in libres:
        db.session.delete(a)

    db.session.query(RavageurRef).filter_by(culture_id=c.id).delete()
    for e in r.get("ennemis") or []:
        db.session.add(RavageurRef(culture_id=c.id, fiche_id=f.id, nom=e["nom"], type=e.get("type"),
                                   precisions=(e.get("precisions") or "")[:255] or None,
                                   matieres_actives=", ".join(e.get("matieres_actives") or []) or None,
                                   traitement=e.get("traitement") or None))
        res["ennemis"] += 1
    db.session.query(IrrigationRef).filter_by(culture_id=c.id).delete()
    for x in r.get("irrigation") or []:
        if x.get("debut") is None:
            continue
        db.session.add(IrrigationRef(culture_id=c.id, fiche_id=f.id, systeme=x["systeme"], phase=x["phase"] or 1,
                                     libelle=x.get("libelle"), dose_debut=x["debut"],
                                     dose_fin=x["fin"] if x.get("fin") is not None else x["debut"]))
        res["irrigation"] += 1

    if c.fiche_id and c.fiche_id != f.id:
        ancienne = db.session.get(FicheTechnique, c.fiche_id)
        if ancienne is not None:
            ancienne.statut = "Remplacée"
    c.fiche_id = f.id
    c.a_valider = False
    titre = f.titre or f.fichier_nom
    c.source = (f"{titre} ({f.fichier_nom}), validée le {datetime.utcnow():%d/%m/%Y}. "
                f"Coefficients culturaux (Kc) et proportions des stades : FAO-56." +
                (f" Antérieurement : {c.source}" if c.source and "validée le" not in (c.source or "") else ""))[:4000]
    f.culture_id = c.id
    f.statut = "Appliquée"
    f.valide_par = user_id
    f.valide_le = datetime.utcnow()
    r["culture_id"] = c.id
    f.extraction = json.dumps(r, ensure_ascii=False, default=str)
    db.session.commit()
    if pot_change:
        from app.referentiel.service import appliquer_potentiels
        s = appliquer_potentiels()
        res["potentiels"] = s["modifiees"]
    return res


# --------------------------------------------------------------------------
# Suivi de l'itinéraire sur une parcelle
# --------------------------------------------------------------------------
def statut_operation(prevue, realisee, optionnel, aujourdhui=None):
    a = aujourdhui or date.today()
    if realisee:
        return "Réalisée"
    if prevue < a - timedelta(days=TOLERANCE_J):
        return "Au besoin" if optionnel else "En retard"
    if prevue <= a + timedelta(days=TOLERANCE_J):
        return "À faire"
    return "À venir"


def suivi(p, c, debut):
    """Opérations datées de l'itinéraire pour la parcelle, avec leur réalisation."""
    ops = c.itineraire.all() if c is not None else []
    if not ops:
        return None
    annee = debut.year
    faites = {o.itineraire_id: o for o in db.session.query(OperationParcelle)
              .filter_by(parcelle_id=p.id, annee=annee)}
    out = []
    for o in ops:
        d = debut + timedelta(days=o.jour)
        fait = faites.get(o.id)
        out.append({"op": o, "date": d, "fait": fait,
                    "statut": statut_operation(d, fait.date_realisee if fait else None, o.optionnel),
                    "quantite": (o.dose_kg_ha * p.surface_ha) if o.dose_kg_ha and p.surface_ha else None})
    n = {}
    for x in out:
        n[x["statut"]] = n.get(x["statut"], 0) + 1
    prochaine = next((x for x in out if x["statut"] in ("À faire", "En retard")), None) or \
        next((x for x in out if x["statut"] == "À venir"), None)
    return {"operations": out, "annee": annee, "comptes": n, "prochaine": prochaine}


def phases_irrigation(c, debut, systeme, surface_ha):
    """Doses d'irrigation de la fiche, datées sur le cycle de la parcelle."""
    doses = [x for x in c.irrigation.all() if x.systeme == systeme]
    if not doses or not c.cycle_j:
        return None
    nb = max(x.phase for x in doses)
    a, b, m, f = c.l_ini or 0, c.l_dev or 0, c.l_mid or 0, c.l_fin or 0
    if nb == 3 and a + b + m + f:
        bornes = [0, a + b, a + b + m, c.cycle_j]
    elif nb == 4 and a + b + m + f:
        bornes = [0, a, a + b, a + b + m, c.cycle_j]
    else:
        bornes = [round(c.cycle_j * k / nb) for k in range(nb + 1)]
    out, aujourd = [], date.today()
    jour_courant = (aujourd - debut).days
    for x in sorted(doses, key=lambda x: x.phase):
        k = min(x.phase, nb) - 1
        j0, j1 = bornes[k], bornes[k + 1]
        dose_auj = None
        if j0 <= jour_courant < j1:
            t = (jour_courant - j0) / max(j1 - j0, 1)
            dose_auj = x.dose_debut + (x.dose_fin - x.dose_debut) * t
        out.append({"phase": x.phase, "libelle": x.libelle, "debut": debut + timedelta(days=j0),
                    "fin": debut + timedelta(days=j1 - 1), "dose_debut": x.dose_debut, "dose_fin": x.dose_fin,
                    "m3_debut": x.dose_debut * 10 * (surface_ha or 0), "m3_fin": x.dose_fin * 10 * (surface_ha or 0),
                    "en_cours": dose_auj is not None, "dose_auj": dose_auj,
                    "m3_auj": dose_auj * 10 * (surface_ha or 0) if dose_auj is not None else None})
    return out


def operations_en_retard(pids=None):
    """Parcelles dont la date de semis / repiquage est connue et dont des opérations sont en retard
    ou à faire cette semaine : [(parcelle, culture, début, suivi)]."""
    from app.models.parcelle import Parcelle
    from app.referentiel.service import culture_ref
    cultures = {c.id: c for c in db.session.query(CultureRef).all() if c.itineraire.count()}
    if not cultures:
        return []
    try:
        with db.session.begin_nested():
            dates = dict(db.session.execute(text("""
                SELECT DISTINCT ON (parcelle_id) parcelle_id, date_semis FROM pratiques_campagne
                 WHERE date_semis IS NOT NULL ORDER BY parcelle_id, annee DESC""")).all())
    except Exception:
        return []
    q = db.session.query(Parcelle).filter(Parcelle.id.in_(list(dates)))
    if pids is not None:
        q = q.filter(Parcelle.id.in_(list(pids)))
    cache, out = {}, []
    for p in q.all():
        if p.culture not in cache:
            cache[p.culture] = culture_ref(p.culture)
        c = cache[p.culture]
        if c is None or c.id not in cultures:
            continue
        debut = dates[p.id]
        if debut + timedelta(days=(c.cycle_j or 120) + 30) < date.today():
            continue                     # culture terminée
        s = suivi(p, c, debut)
        if s and (s["comptes"].get("En retard") or s["comptes"].get("À faire")):
            out.append((p, c, debut, s))
    out.sort(key=lambda x: (-x[3]["comptes"].get("En retard", 0), x[0].id))
    return out
