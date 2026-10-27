import csv
import io
import json
from io import BytesIO

from flask import Blueprint, render_template, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import func, and_, distinct
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.organisation import OrganisationProducteur
from app.models.zone import Region
from app.models.filiere import Filiere
from app.models.campagne import Campagne
from app.models.indice import IndiceVegetation
from app.models.historique import HistoriqueCultural
from app.models.programme import Programme
from app.scoping import parcelle_ids, producteur_ids, organisation_ids

export_bp = Blueprint("export", __name__, url_prefix="/export")

# Seuils (alignés sur les alertes) : stress hydrique = NDMI faible ; problème végétation = NDVI faible
SEUIL_NDVI = 0.25
SEUIL_NDMI = 0.10


def _pids():
    return parcelle_ids(current_user)


# ---------------------------------------------------------------- constructeurs
def _parcelles():
    q = (db.session.query(
            Parcelle.code, Parcelle.culture, Parcelle.surface_ha,
            Producteur.prenom, Producteur.nom, Producteur.telephone,
            OrganisationProducteur.nom, Programme.nom, Region.nom, Filiere.nom,
            Campagne.libelle, Parcelle.geom.isnot(None))
         .outerjoin(Producteur, Producteur.id == Parcelle.producteur_id)
         .outerjoin(OrganisationProducteur, OrganisationProducteur.id == Producteur.organisation_id)
         .outerjoin(Programme, Programme.id == Parcelle.programme_id)
         .outerjoin(Region, Region.id == Producteur.region_id)
         .outerjoin(Filiere, Filiere.id == Parcelle.filiere_id)
         .outerjoin(Campagne, Campagne.id == Parcelle.campagne_id))
    _s = _pids()
    if _s is not None:
        q = q.filter(Parcelle.id.in_(_s))
    q = q.order_by(Parcelle.code)
    headers = ["Code", "Culture", "Surface (ha)", "Producteur", "Téléphone",
               "Organisation (OP)", "Programme", "Région", "Filière", "Campagne", "Géolocalisée"]
    rows = []
    for code, cult, surf, pre, nom, tel, org, prog, reg, fil, camp, geo in q.all():
        producteur = f"{pre or ''} {nom or ''}".strip() if (pre or nom) else ""
        rows.append([code, cult, surf, producteur, tel, org, prog, reg, fil, camp,
                     "Oui" if geo else "Non"])
    return headers, rows


def _producteurs():
    agg = {pid: (cnt, round(s or 0, 2)) for pid, cnt, s in (
        db.session.query(Parcelle.producteur_id, func.count(Parcelle.id),
                         func.coalesce(func.sum(Parcelle.surface_ha), 0.0))
        .group_by(Parcelle.producteur_id).all()) if pid is not None}
    _pr = producteur_ids(current_user)
    q = (db.session.query(Producteur, OrganisationProducteur.nom, Region.nom)
         .outerjoin(OrganisationProducteur, OrganisationProducteur.id == Producteur.organisation_id)
         .outerjoin(Region, Region.id == Producteur.region_id))
    if _pr is not None:
        q = q.filter(Producteur.id.in_(_pr))
    q = q.order_by(Producteur.nom)
    headers = ["Nom", "Téléphone", "Sexe", "Organisation", "Région",
               "Nb parcelles", "Superficie (ha)"]
    rows = []
    for pr, org, reg in q.all():
        nb, surf = agg.get(pr.id, (0, 0))
        rows.append([pr.nom_complet, pr.telephone, pr.sexe, org, reg, nb, surf])
    return headers, rows


def _organisations():
    fq = (db.session.query(OrganisationProducteur.id, Filiere.nom)
          .join(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
          .join(Parcelle, Parcelle.producteur_id == Producteur.id)
          .join(Filiere, Filiere.id == Parcelle.filiere_id).distinct())
    fil_par_op = {}
    for oid, fnom in fq:
        fil_par_op.setdefault(oid, set()).add(fnom)
    agg = (db.session.query(
                OrganisationProducteur.id, OrganisationProducteur.nom,
                OrganisationProducteur.type, Region.nom,
                func.count(distinct(Producteur.id)),
                func.count(distinct(Parcelle.id)),
                func.coalesce(func.sum(Parcelle.surface_ha), 0.0))
           .select_from(OrganisationProducteur)
           .outerjoin(Region, Region.id == OrganisationProducteur.region_id)
           .outerjoin(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
           .outerjoin(Parcelle, Parcelle.producteur_id == Producteur.id)
           .group_by(OrganisationProducteur.id, OrganisationProducteur.nom,
                     OrganisationProducteur.type, Region.nom))
    _og = organisation_ids(current_user)
    if _og is not None:
        agg = agg.filter(OrganisationProducteur.id.in_(_og or [-1]))
    headers = ["Nom", "Type", "Région", "Producteurs", "Parcelles",
               "Superficie (ha)", "Filières"]
    rows = []
    for oid, nom, typ, reg, nprod, nparc, surf in agg.all():
        rows.append([nom, typ, reg, nprod, nparc, round(surf or 0, 2),
                     ", ".join(sorted(fil_par_op.get(oid, [])))])
    return headers, rows


def _indices():
    q = (db.session.query(
            Parcelle.code, IndiceVegetation.date, IndiceVegetation.ndvi,
            IndiceVegetation.ndwi, IndiceVegetation.ndmi, IndiceVegetation.savi,
            IndiceVegetation.evi, IndiceVegetation.source)
         .join(Parcelle, Parcelle.id == IndiceVegetation.parcelle_id))
    _s = _pids()
    if _s is not None:
        q = q.filter(Parcelle.id.in_(_s))
    q = q.order_by(Parcelle.code, IndiceVegetation.date)
    headers = ["Parcelle", "Date", "NDVI", "NDWI", "NDMI", "SAVI", "EVI", "Source"]
    return headers, [list(r) for r in q.all()]


def _historique():
    q = (db.session.query(Parcelle.code, Campagne.libelle,
                          HistoriqueCultural.annee, HistoriqueCultural.culture)
         .join(Parcelle, Parcelle.id == HistoriqueCultural.parcelle_id)
         .join(Campagne, Campagne.id == HistoriqueCultural.campagne_id))
    _s = _pids()
    if _s is not None:
        q = q.filter(Parcelle.id.in_(_s))
    q = q.order_by(Parcelle.code, HistoriqueCultural.annee.desc())
    headers = ["Parcelle", "Campagne", "Année", "Culture"]
    return headers, [list(r) for r in q.all()]


def _alertes():
    from app.alertes.routes import _base_query, SEUIL_NDVI, SEUIL_NDMI
    headers = ["Code", "Producteur", "Téléphone", "Région", "Culture",
               "NDVI", "NDMI", "Date"]
    rows = []
    for parc, iv in _base_query(SEUIL_NDVI, SEUIL_NDMI, None).all():
        rows.append([
            parc.code,
            parc.producteur.nom_complet if parc.producteur else "",
            parc.producteur.telephone if parc.producteur else "",
            parc.producteur.region.nom if parc.producteur and parc.producteur.region else "",
            parc.culture,
            round(iv.ndvi, 3) if iv.ndvi is not None else "",
            round(iv.ndmi, 3) if iv.ndmi is not None else "",
            iv.date.isoformat() if iv.date else "",
        ])
    return headers, rows


def _parcelles_geojson():
    rows = (db.session.query(
                Parcelle.code, Parcelle.culture, Parcelle.surface_ha,
                Producteur.prenom, Producteur.nom, OrganisationProducteur.nom,
                Programme.nom, Region.nom, Filiere.nom,
                Campagne.libelle, func.ST_AsGeoJSON(Parcelle.geom))
            .outerjoin(Producteur, Producteur.id == Parcelle.producteur_id)
            .outerjoin(OrganisationProducteur, OrganisationProducteur.id == Producteur.organisation_id)
            .outerjoin(Programme, Programme.id == Parcelle.programme_id)
            .outerjoin(Region, Region.id == Producteur.region_id)
            .outerjoin(Filiere, Filiere.id == Parcelle.filiere_id)
            .outerjoin(Campagne, Campagne.id == Parcelle.campagne_id)
            .filter(Parcelle.geom.isnot(None)))
    _s = _pids()
    if _s is not None:
        rows = rows.filter(Parcelle.id.in_(_s))
    features = []
    for code, cult, surf, pre, nom, org, prog, reg, fil, camp, gj in rows.all():
        if not gj:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(gj),
            "properties": {
                "code": code, "culture": cult, "surface_ha": surf,
                "producteur": f"{pre or ''} {nom or ''}".strip(),
                "organisation": org, "programme": prog,
                "region": reg, "filiere": fil, "campagne": camp,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _sub_dernier_indice():
    return (db.session.query(
                IndiceVegetation.parcelle_id.label("pid"),
                func.max(IndiceVegetation.date).label("d"))
            .group_by(IndiceVegetation.parcelle_id).subquery())


def _stress_query(critere):
    sub = _sub_dernier_indice()
    q = (db.session.query(
            Parcelle.code, Parcelle.culture, Parcelle.surface_ha,
            Producteur.prenom, Producteur.nom, Producteur.telephone,
            OrganisationProducteur.nom, Programme.nom, Region.nom,
            Parcelle.departement, Parcelle.commune, Parcelle.village, Parcelle.animateur,
            IndiceVegetation.ndvi, IndiceVegetation.ndmi, IndiceVegetation.date,
            func.ST_AsGeoJSON(Parcelle.geom), Parcelle.geom.isnot(None))
         .outerjoin(Producteur, Producteur.id == Parcelle.producteur_id)
         .outerjoin(OrganisationProducteur, OrganisationProducteur.id == Producteur.organisation_id)
         .outerjoin(Programme, Programme.id == Parcelle.programme_id)
         .outerjoin(Region, Region.id == Producteur.region_id)
         .join(sub, sub.c.pid == Parcelle.id)
         .join(IndiceVegetation, and_(IndiceVegetation.parcelle_id == Parcelle.id,
                                      IndiceVegetation.date == sub.c.d)))
    _s = _pids()
    if _s is not None:
        q = q.filter(Parcelle.id.in_(_s))
    if critere == "hydrique":
        q = q.filter(IndiceVegetation.ndmi < SEUIL_NDMI)
    elif critere == "vegetation":
        q = q.filter(IndiceVegetation.ndvi < SEUIL_NDVI)
    else:  # combine
        q = q.filter(IndiceVegetation.ndvi < SEUIL_NDVI, IndiceVegetation.ndmi < SEUIL_NDMI)
    return q.order_by(Parcelle.code)


_H_STRESS = ["Code", "Culture", "Surface (ha)", "Producteur", "Téléphone",
             "Organisation (OP)", "Programme", "Région", "Département", "Commune",
             "Village", "Animateur", "NDVI", "NDMI", "Date", "Géolocalisée"]


def _stress_rows(critere):
    rows = []
    for (code, cult, surf, pre, nom, tel, org, prog, reg, dep, com, vil, anim,
         ndvi, ndmi, dt, gj, geo) in _stress_query(critere).all():
        producteur = f"{pre or ''} {nom or ''}".strip()
        rows.append([code, cult, surf, producteur, tel, org, prog, reg, dep, com, vil, anim,
                     round(ndvi, 3) if ndvi is not None else None,
                     round(ndmi, 3) if ndmi is not None else None,
                     dt.isoformat() if dt else None, "Oui" if geo else "Non"])
    return _H_STRESS, rows


def _stress_geojson(critere):
    features = []
    for (code, cult, surf, pre, nom, tel, org, prog, reg, dep, com, vil, anim,
         ndvi, ndmi, dt, gj, geo) in _stress_query(critere).all():
        if not gj:
            continue
        features.append({
            "type": "Feature", "geometry": json.loads(gj),
            "properties": {
                "code": code, "culture": cult, "surface_ha": surf,
                "producteur": f"{pre or ''} {nom or ''}".strip(), "telephone": tel,
                "organisation": org, "programme": prog, "region": reg,
                "departement": dep, "commune": com, "village": vil, "animateur": anim,
                "ndvi": ndvi, "ndmi": ndmi, "date": dt.isoformat() if dt else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


DATASETS = {
    "parcelles":     {"label": "Parcelles", "builder": _parcelles,
                      "geojson": _parcelles_geojson, "formats": ["xlsx", "csv", "geojson"]},
    "stress_hydrique":   {"label": "Parcelles en stress hydrique (NDMI faible)",
                          "builder": lambda: _stress_rows("hydrique"),
                          "geojson": lambda: _stress_geojson("hydrique"),
                          "formats": ["xlsx", "csv", "geojson"]},
    "stress_vegetation": {"label": "Parcelles à problème de végétation (NDVI faible)",
                          "builder": lambda: _stress_rows("vegetation"),
                          "geojson": lambda: _stress_geojson("vegetation"),
                          "formats": ["xlsx", "csv", "geojson"]},
    "stress_combine":    {"label": "Parcelles cumulant les deux (hydrique + végétation)",
                          "builder": lambda: _stress_rows("combine"),
                          "geojson": lambda: _stress_geojson("combine"),
                          "formats": ["xlsx", "csv", "geojson"]},
    "producteurs":   {"label": "Producteurs", "builder": _producteurs, "formats": ["xlsx", "csv"]},
    "organisations": {"label": "Organisations", "builder": _organisations, "formats": ["xlsx", "csv"]},
    "indices":       {"label": "Indices de végétation", "builder": _indices, "formats": ["xlsx", "csv"]},
    "historique":    {"label": "Historique cultural", "builder": _historique, "formats": ["xlsx", "csv"]},
    "alertes":       {"label": "Alertes (stress hydrique)", "builder": _alertes, "formats": ["xlsx", "csv"]},
}


# ---------------------------------------------------------------- sérialisation
def _to_xlsx(nom, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = nom[:31]
    ws.append(headers)
    for c in ws[1]:
        c.fill = PatternFill("solid", fgColor="1E5631")
        c.font = Font(bold=True, color="FFFFFF")
        c.alignment = Alignment(horizontal="center")
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"{nom}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _to_csv(nom, headers, rows):
    sio = io.StringIO()
    sio.write("\ufeff")  # BOM pour Excel
    w = csv.writer(sio, delimiter=";")
    w.writerow(headers)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    return send_file(BytesIO(sio.getvalue().encode("utf-8")), as_attachment=True,
                     download_name=f"{nom}.csv", mimetype="text/csv")


def _to_geojson(nom, fc):
    data = json.dumps(fc, ensure_ascii=False).encode("utf-8")
    return send_file(BytesIO(data), as_attachment=True,
                     download_name=f"{nom}.geojson", mimetype="application/geo+json")


# ---------------------------------------------------------------- routes
@export_bp.route("/")
@login_required
def index():
    return render_template("export/index.html", datasets=DATASETS)


@export_bp.route("/<dataset>/<fmt>")
@login_required
def telecharger(dataset, fmt):
    d = DATASETS.get(dataset)
    if not d or fmt not in d["formats"]:
        abort(404)
    nom = f"ancar_{dataset}"
    if fmt == "geojson":
        return _to_geojson(nom, d["geojson"]())
    headers, rows = d["builder"]()
    if fmt == "xlsx":
        return _to_xlsx(nom, headers, rows)
    if fmt == "csv":
        return _to_csv(nom, headers, rows)
    abort(404)
