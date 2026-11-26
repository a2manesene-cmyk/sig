from flask import Blueprint, render_template, request, jsonify, url_for
from flask_login import login_required, current_user
from sqlalchemy import func, and_, text

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.organisation import OrganisationProducteur
from app.models.zone import Region
from app.models.campagne import Campagne
from app.models.user import User
from app.models.programme import Programme
from app.scoping import parcelle_ids, producteur_ids, organisation_ids

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def dashboard():
    _pids = parcelle_ids(current_user)
    _prod = producteur_ids(current_user)
    _org = organisation_ids(current_user)

    # --- Filtres du tableau de bord : programme et campagne ---
    f_prog = request.args.get("programme", type=int)
    f_camp = request.args.get("campagne", type=int)
    if f_prog or f_camp:
        q = db.session.query(Parcelle.id)
        if _pids is not None:
            q = q.filter(Parcelle.id.in_(_pids))
        if f_prog:
            q = q.filter(Parcelle.programme_id == f_prog)
        if f_camp:
            q = q.filter(Parcelle.campagne_id == f_camp)
        _pids = [r[0] for r in q.all()]
        # producteurs et OP comptés à partir des parcelles retenues
        _prod = [r[0] for r in db.session.query(Parcelle.producteur_id).filter(
            Parcelle.id.in_(_pids), Parcelle.producteur_id.isnot(None)).distinct()]
        _org = [r[0] for r in db.session.query(Producteur.organisation_id).filter(
            Producteur.id.in_(_prod), Producteur.organisation_id.isnot(None)).distinct()]
    filtres = {"programme": f_prog, "campagne": f_camp}
    qs = {k: v for k, v in filtres.items() if v}          # filtres repris dans les liens

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

    complement = _complement(_pids)
    kpis.update(complement.pop("kpis"))
    stockage = None
    try:
        from app.magasins.service import synthese
        with db.session.begin_nested():
            s = [l for l in synthese(_org, _pids) if l["magasins"]]
        if s:
            cap = sum(l["capacite"] for l in s); prod = sum(l["production"] or 0 for l in s)
            stockage = {"magasins": sum(l["magasins"] for l in s), "ops": len(s), "capacite": cap,
                        "production": prod, "couverture": round(100 * cap / prod) if prod else None}
    except Exception:                      # module magasins pas encore initialisé
        stockage = None

    return render_template(
        "main/dashboard.html",
        filtres=filtres,
        qs=qs,
        programmes=Programme.query.order_by(Programme.nom).all(),
        campagnes=Campagne.query.order_by(Campagne.annee.desc()).all(),
        complement=complement,
        stockage=stockage,
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


# --------------------------------------------------------------------------
SOURCE_COMPOSITE = "GEE Sentinel-2 (composite)"


def _filtre_ids(pids, alias="p"):
    """Clause SQL et paramètres pour limiter aux parcelles visibles / filtrées."""
    if pids is None:
        return "TRUE", {}
    return f"{alias}.id = ANY(:ids)", {"ids": list(pids) or [-1]}


def _complement(pids):
    """Indicateurs ajoutés au tableau de bord : producteurs (femmes), relais, villages,
    tableau par département, relais les plus actifs, évolution de la végétation par décade."""
    cond, params = _filtre_ids(pids)
    k = db.session.execute(text(f"""
        SELECT COUNT(DISTINCT p.producteur_id),
               COUNT(DISTINCT p.producteur_id) FILTER (WHERE UPPER(pd.sexe) = 'F'),
               COUNT(DISTINCT NULLIF(TRIM(p.animateur), '')),
               COUNT(DISTINCT LOWER(TRIM(p.village))),
               COUNT(DISTINCT p.producteur_id) FILTER (WHERE UPPER(pd.sexe) IN ('H', 'M')),
               COUNT(DISTINCT LOWER(TRIM(p.departement))),
               AVG(p.surface_ha)
          FROM parcelles p LEFT JOIN producteurs pd ON pd.id = p.producteur_id
         WHERE {cond}"""), params).one()
    kpis = {"prod_parcelles": k[0] or 0, "femmes": k[1] or 0,
            "femmes_pct": round(100.0 * k[1] / k[0]) if k[0] else None,
            "relais": k[2] or 0, "villages": k[3] or 0, "hommes": k[4] or 0,
            "departements": k[5] or 0, "surface_moy": round(k[6], 2) if k[6] else None}

    # dernier indice connu par parcelle (pour le tableau et la carte)
    derniers = f"""
        SELECT DISTINCT ON (iv.parcelle_id) iv.parcelle_id, iv.ndvi, iv.ndmi, iv.date
          FROM indices_vegetation iv JOIN parcelles p ON p.id = iv.parcelle_id
         WHERE {cond} ORDER BY iv.parcelle_id, iv.date DESC"""

    deps = db.session.execute(text(f"""
        WITH d AS ({derniers})
        SELECT COALESCE(INITCAP(TRIM(p.departement)), '—') AS dep,
               COUNT(*), SUM(p.surface_ha),
               COUNT(DISTINCT p.producteur_id),
               COUNT(DISTINCT p.producteur_id) FILTER (WHERE UPPER(pd.sexe) = 'F'),
               COUNT(DISTINCT NULLIF(TRIM(p.animateur), '')),
               AVG(d.ndvi), COUNT(d.ndvi) FILTER (WHERE d.ndvi < 0.20)
          FROM parcelles p
          LEFT JOIN producteurs pd ON pd.id = p.producteur_id
          LEFT JOIN d ON d.parcelle_id = p.id
         WHERE {cond}
         GROUP BY 1 ORDER BY SUM(p.surface_ha) DESC NULLS LAST"""), params).all()
    par_departement = [{"nom": r[0], "parcelles": r[1], "ha": round(r[2] or 0, 1), "producteurs": r[3],
                        "femmes": r[4], "relais": r[5],
                        "ndvi": round(r[6], 2) if r[6] is not None else None, "faibles": r[7]}
                       for r in deps]

    relais = db.session.execute(text(f"""
        SELECT TRIM(p.animateur), COUNT(*), SUM(p.surface_ha)
          FROM parcelles p
         WHERE {cond} AND NULLIF(TRIM(p.animateur), '') IS NOT NULL
         GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 12"""), params).all()
    top_relais = {"labels": [r[0] for r in relais], "parcelles": [r[1] for r in relais],
                  "ha": [round(r[2] or 0, 1) for r in relais]}

    # évolution par décade (10 jours) : moyenne par parcelle puis moyenne des parcelles,
    # pour que chaque parcelle compte une fois quel que soit le nombre de passages
    from app.teledetection.series import debut_campagne
    debut = debut_campagne()
    serie = db.session.execute(text(f"""
        WITH v AS (
            SELECT iv.parcelle_id,
                   (date_trunc('month', iv.date)::date
                      + (LEAST((EXTRACT(DAY FROM iv.date)::int - 1) / 10, 2) * 10)) AS dec,
                   AVG(iv.ndvi) AS ndvi, AVG(iv.ndmi) AS ndmi
              FROM indices_vegetation iv JOIN parcelles p ON p.id = iv.parcelle_id
             WHERE {cond} AND iv.date >= :d1 AND iv.source IS DISTINCT FROM :comp
             GROUP BY 1, 2)
        SELECT dec, AVG(ndvi), AVG(ndmi), COUNT(*),
               percentile_cont(0.25) WITHIN GROUP (ORDER BY ndvi),
               percentile_cont(0.75) WITHIN GROUP (ORDER BY ndvi)
          FROM v GROUP BY dec ORDER BY dec"""), dict(params, d1=debut, comp=SOURCE_COMPOSITE)).all()
    evolution = {"labels": [r[0].strftime("%d/%m") for r in serie],
                 "ndvi": [round(r[1], 3) if r[1] is not None else None for r in serie],
                 "ndmi": [round(r[2], 3) if r[2] is not None else None for r in serie],
                 "p25": [round(r[4], 3) if r[4] is not None else None for r in serie],
                 "p75": [round(r[5], 3) if r[5] is not None else None for r in serie],
                 "n": [r[3] for r in serie], "debut": debut}

    couv = db.session.execute(text(f"""
        SELECT COUNT(*) FILTER (WHERE n >= 3), COUNT(*) FILTER (WHERE n < 3)
          FROM (SELECT p.id, COUNT(iv.id) AS n FROM parcelles p
                  LEFT JOIN indices_vegetation iv ON iv.parcelle_id = p.id AND iv.date >= :d1
                       AND iv.source IS DISTINCT FROM :comp
                 WHERE {cond} AND p.geom IS NOT NULL GROUP BY p.id) x"""),
        dict(params, d1=debut, comp=SOURCE_COMPOSITE)).one()
    return {"kpis": kpis, "par_departement": par_departement, "top_relais": top_relais,
            "evolution": evolution, "couverture": {"avec": couv[0], "sans": couv[1]}}


@main_bp.route("/tableau/points.json")
@login_required
def points():
    """Centroïdes des parcelles (filtrées) avec leur dernier NDVI, pour la carte du tableau de bord."""
    pids = parcelle_ids(current_user)
    cond, params = _filtre_ids(pids)
    prog = request.args.get("programme", type=int)
    camp = request.args.get("campagne", type=int)
    if prog:
        cond += " AND p.programme_id = :prog"; params["prog"] = prog
    if camp:
        cond += " AND p.campagne_id = :camp"; params["camp"] = camp
    rows = db.session.execute(text(f"""
        WITH d AS (SELECT DISTINCT ON (iv.parcelle_id) iv.parcelle_id, iv.ndvi
                     FROM indices_vegetation iv ORDER BY iv.parcelle_id, iv.date DESC)
        SELECT p.id, ST_Y(ST_PointOnSurface(p.geom)), ST_X(ST_PointOnSurface(p.geom)), d.ndvi,
               TRIM(COALESCE(pd.prenom, '') || ' ' || COALESCE(pd.nom, '')), p.village, p.surface_ha, p.culture
          FROM parcelles p
          LEFT JOIN producteurs pd ON pd.id = p.producteur_id
          LEFT JOIN d ON d.parcelle_id = p.id
         WHERE {cond} AND p.geom IS NOT NULL AND NOT ST_IsEmpty(p.geom)"""), params).all()
    return jsonify([[r[0], round(r[1], 6), round(r[2], 6), round(r[3], 3) if r[3] is not None else None,
                     r[4] or "", r[5] or "", round(r[6] or 0, 2), r[7] or ""] for r in rows])
