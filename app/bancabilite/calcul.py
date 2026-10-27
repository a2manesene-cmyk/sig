# -*- coding: utf-8 -*-
"""
Indice de bancabilité (usage INTERNE ANCAR — aide à la décision).
Score 0–100 par producteur, à partir des données déjà disponibles :
niveau et régularité des rendements, antériorité, stabilité des parcelles,
santé des cultures (NDVI), structuration (appartenance à une OP).

IMPORTANT : ce score n'est PAS un verdict. Une mauvaise campagne peut être
due à un aléa climatique. À utiliser pour ORIENTER, jamais pour exclure
automatiquement. Aucune donnée n'est transmise à l'extérieur ici.
"""
from statistics import mean, pstdev

from sqlalchemy import func

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.indice import IndiceVegetation
from app.models.rendement import RendementReference

REF_RDT = 2500.0   # rendement maïs de référence (kg/ha) pour le niveau "plein"

# Pondérations (somme = 100)
P_NIVEAU, P_REGUL, P_ANTER, P_STAB, P_SANTE, P_OP = 30, 25, 15, 10, 10, 10


def _classe(total):
    if total is None:
        return ("Non évaluable", "secondary")
    if total < 40:
        return ("À renforcer", "danger")
    if total < 70:
        return ("Correct", "warning")
    return ("Solide", "success")


def _ndvi_moyen_producteur(pids):
    """Moyenne du dernier NDVI de chaque parcelle du producteur."""
    if not pids:
        return None
    sub = (db.session.query(
                IndiceVegetation.parcelle_id.label("pid"),
                func.max(IndiceVegetation.date).label("d"))
           .filter(IndiceVegetation.parcelle_id.in_(pids))
           .group_by(IndiceVegetation.parcelle_id).subquery())
    rows = (db.session.query(IndiceVegetation.ndvi)
            .join(sub, (IndiceVegetation.parcelle_id == sub.c.pid) &
                       (IndiceVegetation.date == sub.c.d))
            .all())
    vals = [r[0] for r in rows if r[0] is not None]
    return mean(vals) if vals else None


def score_producteur(prod):
    """Retourne un dict : total, classe, couleur, composantes, indicateurs, evaluable."""
    parcelles = Parcelle.query.filter_by(producteur_id=prod.id).all()
    pids = [p.id for p in parcelles]
    n_parc = len(parcelles)
    n_parc_geo = sum(1 for p in parcelles if p.geom is not None)

    # Rendements par année (moyenne annuelle du producteur)
    par_annee = {}
    if pids:
        for r in (RendementReference.query
                  .filter(RendementReference.parcelle_id.in_(pids),
                          RendementReference.annee.isnot(None)).all()):
            par_annee.setdefault(r.annee, []).append(r.rendement_kg_ha)
    moy_annee = {a: mean(v) for a, v in par_annee.items()}
    n_ans = len(moy_annee)
    rdt_moyen = mean(list(moy_annee.values())) if moy_annee else None

    evaluable = n_parc >= 1 and n_ans >= 1
    if not evaluable:
        cl, col = _classe(None)
        return {
            "total": None, "classe": cl, "couleur": col, "evaluable": False,
            "composantes": [], "n_parcelles": n_parc, "n_campagnes": n_ans,
            "rdt_moyen": rdt_moyen, "surface": round(sum(p.surface_ha or 0 for p in parcelles), 2),
            "a_op": bool(prod.organisation_id),
            "message": "Données insuffisantes pour évaluer (au moins 1 rendement saisi requis).",
        }

    # 1. Niveau de rendement
    c_niveau = min(1.0, (rdt_moyen or 0) / REF_RDT) * P_NIVEAU
    # 2. Régularité (faible variabilité = bon) — nécessite >= 2 campagnes
    if n_ans >= 2:
        m = mean(list(moy_annee.values()))
        cv = (pstdev(list(moy_annee.values())) / m) if m else 1.0
        c_regul = max(0.0, 1.0 - min(1.0, cv)) * P_REGUL
    else:
        c_regul = 0.0
    # 3. Antériorité (nombre de campagnes documentées)
    c_anter = min(1.0, n_ans / 3.0) * P_ANTER
    # 4. Stabilité des parcelles (géolocalisées / suivies)
    c_stab = (n_parc_geo / n_parc) * P_STAB if n_parc else 0.0
    # 5. Santé des cultures (dernier NDVI moyen)
    ndvi = _ndvi_moyen_producteur(pids)
    if ndvi is None:
        c_sante = 0.0
    elif ndvi >= 0.25:
        c_sante = P_SANTE
    elif ndvi >= 0.08:
        c_sante = P_SANTE * 0.5
    else:
        c_sante = P_SANTE * 0.2
    # 6. Structuration (OP)
    c_op = P_OP if prod.organisation_id else 0.0

    total = round(c_niveau + c_regul + c_anter + c_stab + c_sante + c_op)
    cl, col = _classe(total)

    composantes = [
        {"libelle": "Niveau de rendement", "valeur": round(c_niveau), "max": P_NIVEAU},
        {"libelle": "Régularité", "valeur": round(c_regul), "max": P_REGUL},
        {"libelle": "Antériorité (campagnes)", "valeur": round(c_anter), "max": P_ANTER},
        {"libelle": "Stabilité des parcelles", "valeur": round(c_stab), "max": P_STAB},
        {"libelle": "Santé des cultures", "valeur": round(c_sante), "max": P_SANTE},
        {"libelle": "Structuration (OP)", "valeur": round(c_op), "max": P_OP},
    ]
    return {
        "total": total, "classe": cl, "couleur": col, "evaluable": True,
        "composantes": composantes, "n_parcelles": n_parc, "n_campagnes": n_ans,
        "rdt_moyen": round(rdt_moyen) if rdt_moyen else None,
        "surface": round(sum(p.surface_ha or 0 for p in parcelles), 2),
        "a_op": bool(prod.organisation_id),
        "message": None,
    }


# ---- Score de bancabilité par OP (organisation) ----
# Pondérations (total 100) : rendement 25, régularité 15, taille/structuration 15,
# stabilité parcelles 10, santé NDVI 10, financement LBA 25.
PO_NIVEAU, PO_REGUL, PO_TAILLE, PO_STAB, PO_SANTE, PO_FIN = 25, 15, 15, 10, 10, 25


def score_op(org):
    """Score de bancabilité (0-100) d'une OP, incluant l'historique de financement LBA."""
    from app.models.producteur import Producteur
    from app.models.financement import FinancementOP

    prod_ids = [p.id for p in Producteur.query.filter_by(organisation_id=org.id).all()]
    n_prod = len(prod_ids)
    parcelles = (Parcelle.query.filter(Parcelle.producteur_id.in_(prod_ids)).all()
                 if prod_ids else [])
    n_parc = len(parcelles)
    pids = [p.id for p in parcelles]
    n_geo = sum(1 for p in parcelles if p.geom is not None)

    # Rendements par année (moyenne annuelle de l'OP)
    par_annee = {}
    if pids:
        for r in (RendementReference.query
                  .filter(RendementReference.parcelle_id.in_(pids),
                          RendementReference.annee.isnot(None)).all()):
            if r.rendement_kg_ha is not None:
                par_annee.setdefault(r.annee, []).append(r.rendement_kg_ha)
    moy_annee = {a: mean(v) for a, v in par_annee.items()}
    rdt_moyen = mean(list(moy_annee.values())) if moy_annee else None

    # Financement LBA de l'OP
    fins = FinancementOP.query.filter_by(organisation_id=org.id).all()
    montant_accorde = sum((f.montant_accorde or 0) for f in fins)
    realisation = sum((f.realisation or 0) for f in fins)
    a_accord = any(f.etat and "ccord" in (f.etat or "") for f in fins)

    evaluable = n_prod >= 1 and (rdt_moyen is not None or montant_accorde > 0)
    if not evaluable:
        cl, col = _classe(None)
        return {"total": None, "classe": cl, "couleur": col, "evaluable": False,
                "composantes": [], "n_producteurs": n_prod, "n_parcelles": n_parc,
                "rdt_moyen": None, "montant_accorde": montant_accorde,
                "realisation": realisation,
                "message": "Non évaluable (données insuffisantes)."}

    # 1. Niveau de rendement
    c_niveau = min(1.0, (rdt_moyen or 0) / REF_RDT) * PO_NIVEAU
    # 2. Régularité (entre parcelles de la dernière année)
    derniere = max(moy_annee) if moy_annee else None
    vals = par_annee.get(derniere, []) if derniere else []
    if len(vals) >= 2 and mean(vals) > 0:
        cv = pstdev(vals) / mean(vals)
        c_regul = max(0.0, 1.0 - min(1.0, cv)) * PO_REGUL
    else:
        c_regul = PO_REGUL * 0.5
    # 3. Taille / structuration (nb de producteurs)
    c_taille = min(1.0, n_prod / 30.0) * PO_TAILLE
    # 4. Stabilité des parcelles (géolocalisées)
    c_stab = (n_geo / n_parc) * PO_STAB if n_parc else 0.0
    # 5. Santé des cultures (NDVI moyen)
    ndvi = _ndvi_moyen_producteur(pids)
    if ndvi is None:
        c_sante = 0.0
    elif ndvi >= 0.25:
        c_sante = PO_SANTE
    elif ndvi >= 0.08:
        c_sante = PO_SANTE * 0.5
    else:
        c_sante = PO_SANTE * 0.2
    # 6. Financement LBA (accès au crédit + taux de réalisation)
    c_fin = 0.0
    if montant_accorde > 0:
        c_fin = PO_FIN * 0.5                       # un financement a été accordé
        taux = min(1.0, realisation / montant_accorde) if montant_accorde else 0.0
        c_fin += PO_FIN * 0.5 * taux               # part réalisée
    elif a_accord:
        c_fin = PO_FIN * 0.4

    total = round(c_niveau + c_regul + c_taille + c_stab + c_sante + c_fin)
    cl, col = _classe(total)
    composantes = [
        {"libelle": "Niveau de rendement", "valeur": round(c_niveau), "max": PO_NIVEAU},
        {"libelle": "Régularité", "valeur": round(c_regul), "max": PO_REGUL},
        {"libelle": "Taille / structuration", "valeur": round(c_taille), "max": PO_TAILLE},
        {"libelle": "Stabilité des parcelles", "valeur": round(c_stab), "max": PO_STAB},
        {"libelle": "Santé des cultures", "valeur": round(c_sante), "max": PO_SANTE},
        {"libelle": "Financement LBA", "valeur": round(c_fin), "max": PO_FIN},
    ]
    return {"total": total, "classe": cl, "couleur": col, "evaluable": True,
            "composantes": composantes, "n_producteurs": n_prod, "n_parcelles": n_parc,
            "rdt_moyen": round(rdt_moyen) if rdt_moyen else None,
            "montant_accorde": montant_accorde, "realisation": realisation,
            "message": None}
