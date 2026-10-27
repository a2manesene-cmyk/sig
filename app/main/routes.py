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
    campagne = Campagne.query.filter_by(active=True).first()

    # --- Superficie et parcelles par région (via producteur) ---
    region_rows = _sp(
        db.session.query(
            Region.nom,
            func.count(Parcelle.id),
            func.coalesce(func.sum(Parcelle.surface_ha), 0.0),
        )
        .select_from(Parcelle)
        .join(Producteur, Producteur.id == Parcelle.producteur_id)
        .join(Region, Region.id == Producteur.region_id)
        .group_by(Region.nom)
        .order_by(func.sum(Parcelle.surface_ha).desc())
    ).all()
    par_region = {
        "labels": [r[0] for r in region_rows],
        "parcelles": [int(r[1]) for r in region_rows],
        "superficie": [round(r[2], 1) for r in region_rows],
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
            OrganisationProducteur.nom,
            func.coalesce(func.sum(Parcelle.surface_ha), 0.0),
        )
        .select_from(OrganisationProducteur)
        .join(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
        .join(Parcelle, Parcelle.producteur_id == Producteur.id)
        .group_by(OrganisationProducteur.nom)
        .order_by(func.sum(Parcelle.surface_ha).desc())
    ).all()
    par_organisation = {
        "labels": [r[0] for r in op_rows],
        "superficie": [round(r[1], 1) for r in op_rows],
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
        vig = [0, 0, 0, 0, 0]   # sol nu, très faible, modérée, bonne, très vigoureuse
        for v in ndvis:
            if v < 0.15: vig[0] += 1
            elif v < 0.30: vig[1] += 1
            elif v < 0.50: vig[2] += 1
            elif v < 0.70: vig[3] += 1
            else: vig[4] += 1
        stress = [0, 0, 0, 0]   # élevé, modéré, bonne humidité, humidité élevée
        for r in latest:
            m = r[1]
            if m is None: continue
            if m < 0.0: stress[0] += 1
            elif m < 0.20: stress[1] += 1
            elif m < 0.40: stress[2] += 1
            else: stress[3] += 1
        teledetection = {
            "actif": True,
            "nb_suivies": len(latest),
            "derniere_date": db.session.query(func.max(IndiceVegetation.date)).scalar(),
            "ndvi_moyen": round(sum(ndvis) / len(ndvis), 3) if ndvis else None,
            "nb_stress": stress[0],
            "nb_faible": vig[0] + vig[1],
            "vigueur": {
                "labels": ["Sol nu", "Très faible", "Modérée", "Bonne", "Très vigoureuse"],
                "data": vig,
                "couleurs": ["#a50026", "#d73027", "#fdae61", "#66bb6a", "#1a9850"],
            },
            "stress": {
                "labels": ["Stress élevé", "Stress modéré", "Bonne humidité", "Humidité élevée"],
                "data": stress,
                "couleurs": ["#a50026", "#fdae61", "#66bb6a", "#1a9850"],
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
        teledetection=teledetection,
    )
