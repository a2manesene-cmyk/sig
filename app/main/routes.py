from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import func, and_

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.organisation import OrganisationProducteur
from app.models.zone import Region
from app.models.campagne import Campagne
from app.models.user import User
from app.scoping import parcelle_ids, producteur_ids, organisation_ids

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def dashboard():
    _pids = parcelle_ids(current_user)
    _prod = producteur_ids(current_user)
    _org = organisation_ids(current_user)

    def _sp(q):
        return q if _pids is None else q.filter(Parcelle.id.in_(_pids))

    # --- Indicateurs clés ---
    nb_parcelles = _sp(db.session.query(func.count(Parcelle.id))).scalar() or 0
    superficie = _sp(db.session.query(func.coalesce(func.sum(Parcelle.surface_ha), 0.0))).scalar() or 0
    kpis = {
        "parcelles": nb_parcelles,
        "superficie": round(superficie, 1),
        "producteurs": Producteur.query.count() if _prod is None else len(_prod),
        "organisations": OrganisationProducteur.query.count() if _org is None else len(_org),
        "utilisateurs": User.query.count(),
    }
    campagne = (Campagne.query.filter_by(active=True)
                .order_by(Campagne.annee.desc()).first()
                or Campagne.query.order_by(Campagne.annee.desc()).first())

    # --- Rendement (valeurs de référence : DAPSA et/ou terrain) ---
    from app.models.rendement import RendementReference
    rdt_global = _sp(
        db.session.query(func.avg(RendementReference.rendement_kg_ha),
                         func.count(RendementReference.id))
        .select_from(RendementReference)
        .join(Parcelle, Parcelle.id == RendementReference.parcelle_id)
    ).first()
    rdt_moyen = round(rdt_global[0]) if rdt_global and rdt_global[0] else None
    rdt_nb = rdt_global[1] if rdt_global else 0
    from app.rendement.calcul import cle_lieu as _cle, belle_casse as _belle
    rdt_rows = _sp(
        db.session.query(Parcelle.departement,
                         RendementReference.rendement_kg_ha)
        .select_from(RendementReference)
        .join(Parcelle, Parcelle.id == RendementReference.parcelle_id)
        .filter(Parcelle.departement.isnot(None))
    ).all()
    _acc = {}
    for dep, val in rdt_rows:
        if val is None:
            continue
        k = _belle(_cle(dep))
        a = _acc.setdefault(k, [0.0, 0])
        a[0] += val; a[1] += 1
    _dep_moy = sorted(((k, v[0] / v[1]) for k, v in _acc.items()), key=lambda x: -x[1])
    par_rendement = {
        "labels": [k for k, _ in _dep_moy],
        "data": [round(v) for _, v in _dep_moy],
    }
    kpis["rendement_moyen"] = rdt_moyen
    kpis["rendement_nb"] = rdt_nb

    # Rendement ESTIMÉ (satellite) : nécessite un modèle calibré (références avec NDVI)
    from app.rendement.calcul import ndvi_max_par_parcelle, rendement_estime
    nvm = ndvi_max_par_parcelle(_pids)
    rdt_est_moyen, rdt_est_nb = None, 0
    if nvm:
        pot = dict(_sp(db.session.query(Parcelle.id, Parcelle.rendement_potentiel)).all())
        ests = []
        for pid, m in nvm.items():
            e = rendement_estime(m.get("ndvi_max"), pot.get(pid))
            if e is not None:
                ests.append(e)
        if ests:
            rdt_est_moyen = round(sum(ests) / len(ests))
            rdt_est_nb = len(ests)
    kpis["rendement_estime"] = rdt_est_moyen
    kpis["rendement_estime_nb"] = rdt_est_nb

    # Production attendue (tonnes) : rendement attendu × superficie
    from app.rendement.calcul import rendement_attendu_par_parcelle
    rdt_att = rendement_attendu_par_parcelle(_pids)
    prod_total_kg = 0.0
    prod_dep = {}
    if rdt_att:
        from app.rendement.calcul import cle_lieu, belle_casse
        rows_pp = _sp(db.session.query(Parcelle.id, Parcelle.surface_ha, Parcelle.departement)).all()
        for pid, surf, dep in rows_pp:
            r = rdt_att.get(pid)
            if r is None or not surf:
                continue
            pkg = r * surf
            prod_total_kg += pkg
            lbl = belle_casse(cle_lieu(dep)) if dep else "—"
            prod_dep[lbl] = prod_dep.get(lbl, 0.0) + pkg
    kpis["production_t"] = round(prod_total_kg / 1000.0, 1) if prod_total_kg else None
    _dep_sorted = sorted(prod_dep.items(), key=lambda kv: -kv[1])
    par_production = {
        "labels": [d for d, _ in _dep_sorted],
        "data": [round(v / 1000.0, 1) for _, v in _dep_sorted],
    }

    # --- Superficie et parcelles par région (via producteur) ---
    region_rows = _sp(
        db.session.query(
            Region.id,
            Region.nom,
            func.count(Parcelle.id),
            func.coalesce(func.sum(Parcelle.surface_ha), 0.0),
        )
        .select_from(Parcelle)
        .join(Producteur, Producteur.id == Parcelle.producteur_id)
        .join(Region, Region.id == Producteur.region_id)
        .group_by(Region.id, Region.nom)
        .order_by(func.sum(Parcelle.surface_ha).desc())
    ).all()
    par_region = {
        "ids": [r[0] for r in region_rows],
        "labels": [r[1] for r in region_rows],
        "parcelles": [int(r[2]) for r in region_rows],
        "superficie": [round(r[3], 1) for r in region_rows],
    }

    # --- Répartition par culture ---
    culture_rows = _sp(
        db.session.query(
            func.coalesce(Parcelle.culture, "Non renseignée"),
            func.count(Parcelle.id),
        )
        .group_by(Parcelle.culture)
        .order_by(func.count(Parcelle.id).desc())
    ).all()
    par_culture = {
        "labels": [r[0] for r in culture_rows],
        "data": [int(r[1]) for r in culture_rows],
    }

    # --- Superficie par organisation ---
    op_rows = _sp(
        db.session.query(
            OrganisationProducteur.id,
            OrganisationProducteur.nom,
            func.coalesce(func.sum(Parcelle.surface_ha), 0.0),
        )
        .select_from(OrganisationProducteur)
        .join(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
        .join(Parcelle, Parcelle.producteur_id == Producteur.id)
        .group_by(OrganisationProducteur.id, OrganisationProducteur.nom)
        .order_by(func.sum(Parcelle.surface_ha).desc())
    ).all()
    par_organisation = {
        "ids": [r[0] for r in op_rows],
        "labels": [r[1] for r in op_rows],
        "superficie": [round(r[2], 1) for r in op_rows],
    }

    # --- Suivi de la végétation (dernier relevé par parcelle) ---
    from app.models.indice import IndiceVegetation
    sub = (
        db.session.query(
            IndiceVegetation.parcelle_id.label("pid"),
            func.max(IndiceVegetation.date).label("d"),
        )
        .group_by(IndiceVegetation.parcelle_id)
        .subquery()
    )
    latest = _sp(
        db.session.query(IndiceVegetation.ndvi, IndiceVegetation.ndmi)
        .join(Parcelle, Parcelle.id == IndiceVegetation.parcelle_id)
        .join(sub, and_(sub.c.pid == IndiceVegetation.parcelle_id,
                        sub.c.d == IndiceVegetation.date))
    ).all()
    if latest:
        ndvis = [r[0] for r in latest if r[0] is not None]
        # Santé (NDVI) en 5 classes (échelle QGIS)
        vig = [0, 0, 0, 0, 0]
        for v in ndvis:
            if v < 0.10: vig[0] += 1
            elif v < 0.20: vig[1] += 1
            elif v < 0.35: vig[2] += 1
            elif v < 0.55: vig[3] += 1
            else: vig[4] += 1
        # Stress hydrique : NDMI < 0,10 = en stress
        n_stress = sum(1 for r in latest if r[1] is not None and r[1] < 0.10)
        n_ok = sum(1 for r in latest if r[1] is not None and r[1] >= 0.10)
        teledetection = {
            "actif": True,
            "nb_suivies": len(latest),
            "derniere_date": db.session.query(func.max(IndiceVegetation.date)).scalar(),
            "ndvi_moyen": round(sum(ndvis) / len(ndvis), 3) if ndvis else None,
            "nb_stress": n_stress,
            "nb_faible": vig[0] + vig[1],
            "vigueur": {
                "labels": ["Sol nu / Échec", "Végétation critique", "Retard / À surveiller",
                           "Bon comportement", "Excellent développement"],
                "data": vig,
                "couleurs": ["#d73027", "#F39C12", "#F4D03F", "#a6d96a", "#1a9850"],
                "filtres": ["solnu", "critique", "retard", "bon", "excellent"],
            },
            "stress": {
                "labels": ["En stress (NDMI<0,10)", "Correct"],
                "data": [n_stress, n_ok],
                "couleurs": ["#d73027", "#1a9850"],
                "filtres": ["1", ""],
            },
        }
    else:
        teledetection = {"actif": False}

    return render_template(
        "main/dashboard.html",
        user=current_user,
        kpis=kpis,
        campagne=campagne,
        par_region=par_region,
        par_culture=par_culture,
        par_organisation=par_organisation,
        par_rendement=par_rendement,
        par_production=par_production,
        teledetection=teledetection,
    )
