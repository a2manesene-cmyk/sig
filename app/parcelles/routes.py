import json
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)
from flask_login import login_required, current_user
from sqlalchemy import func, and_
from shapely.geometry import shape, MultiPolygon
from shapely.ops import transform as shp_transform
from pyproj import Transformer
from geoalchemy2.elements import WKTElement

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.organisation import OrganisationProducteur
from app.models.filiere import Filiere
from app.models.campagne import Campagne
from app.models.zone import Region
from app.models.indice import IndiceVegetation
from app.models.historique import HistoriqueCultural
from app.models.rendement import RendementReference
from app.models.suivi import SuiviTerrain
from app.teledetection.interpretation import interpret
from app.auth.decorators import niveau_minimum
from app.scoping import filtrer_parcelles, parcelle_ids, peut_voir_parcelle

parcelles_bp = Blueprint("parcelles", __name__, url_prefix="/parcelles")


# --- Helpers ----------------------------------------------------------------
def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _premier(props, cles):
    """Renvoie la première valeur non vide parmi une liste de clés candidates."""
    for c in cles:
        if c in props and props[c] not in (None, ""):
            return props[c]
    return None


def _ha_depuis_wgs84(geom_wgs84):
    """Surface approximative en hectares (reprojection en UTM 28N)."""
    try:
        vers_utm = Transformer.from_crs(
            "EPSG:4326", "EPSG:32628", always_xy=True
        ).transform
        return round(shp_transform(vers_utm, geom_wgs84).area / 10000.0, 4)
    except Exception:
        return None


def _refs_formulaire():
    return {
        "producteurs": Producteur.query.order_by(Producteur.nom).all(),
        "filieres": Filiere.query.order_by(Filiere.nom).all(),
        "campagnes": Campagne.query.order_by(Campagne.annee.desc()).all(),
    }


# --- Liste / carte / GeoJSON ------------------------------------------------
@parcelles_bp.route("/")
@login_required
def liste():
    q = (request.args.get("q") or "").strip()
    culture = request.args.get("culture") or None
    region_id = _i(request.args.get("region"))
    campagne_id = _i(request.args.get("campagne"))
    programme_id = _i(request.args.get("programme"))
    organisation_id = _i(request.args.get("organisation"))
    etat = request.args.get("etat") or None          # faible | modere | dense
    hydrique = request.args.get("hydrique") or None   # "1" -> NDMI < 0.10
    page = _i(request.args.get("page")) or 1

    query = Parcelle.query
    if q:
        query = query.filter(Parcelle.code.ilike(f"%{q}%"))
    if culture:
        query = query.filter(Parcelle.culture == culture)
    if programme_id:
        query = query.filter(Parcelle.programme_id == programme_id)
    if region_id or organisation_id:
        query = query.join(Producteur, Producteur.id == Parcelle.producteur_id)
        if region_id:
            query = query.filter(Producteur.region_id == region_id)
        if organisation_id:
            query = query.filter(Producteur.organisation_id == organisation_id)
    if campagne_id:
        query = query.filter(Parcelle.campagne_id == campagne_id)
    # Filtre par état de végétation / stress hydrique (dernier indice)
    if etat or hydrique:
        _sub = (db.session.query(
                    IndiceVegetation.parcelle_id.label("pid"),
                    func.max(IndiceVegetation.date).label("d"))
                .group_by(IndiceVegetation.parcelle_id).subquery())
        query = (query.join(_sub, _sub.c.pid == Parcelle.id)
                      .join(IndiceVegetation, and_(
                          IndiceVegetation.parcelle_id == Parcelle.id,
                          IndiceVegetation.date == _sub.c.d)))
        if etat == "solnu":
            query = query.filter(IndiceVegetation.ndvi < 0.10)
        elif etat == "critique":
            query = query.filter(IndiceVegetation.ndvi >= 0.10, IndiceVegetation.ndvi < 0.20)
        elif etat == "retard":
            query = query.filter(IndiceVegetation.ndvi >= 0.20, IndiceVegetation.ndvi < 0.35)
        elif etat == "bon":
            query = query.filter(IndiceVegetation.ndvi >= 0.35, IndiceVegetation.ndvi < 0.55)
        elif etat == "excellent":
            query = query.filter(IndiceVegetation.ndvi >= 0.55)
        elif etat == "faible":   # rétro-compat (ancien lien) : sol nu + critique + retard
            query = query.filter(IndiceVegetation.ndvi < 0.35)
        if hydrique == "1":
            query = query.filter(IndiceVegetation.ndmi < 0.10)
    query = filtrer_parcelles(query, current_user)
    query = query.order_by(Parcelle.id.desc())

    pagination = query.paginate(page=page, per_page=24, error_out=False)
    parcelles = pagination.items
    ids = [p.id for p in parcelles]

    # Derniers indices pour les parcelles affichées
    idx = {}
    if ids:
        sub = (db.session.query(
                    IndiceVegetation.parcelle_id.label("pid"),
                    func.max(IndiceVegetation.date).label("d"))
               .filter(IndiceVegetation.parcelle_id.in_(ids))
               .group_by(IndiceVegetation.parcelle_id).subquery())
        for pid, ndvi, ndmi, dte in (
            db.session.query(IndiceVegetation.parcelle_id, IndiceVegetation.ndvi,
                             IndiceVegetation.ndmi, IndiceVegetation.date)
            .join(sub, and_(sub.c.pid == IndiceVegetation.parcelle_id,
                            sub.c.d == IndiceVegetation.date)).all()):
            idx[pid] = (ndvi, ndmi, dte)

    # Dernière culture (historique) pour les parcelles affichées
    hist = {}
    if ids:
        subh = (db.session.query(
                    HistoriqueCultural.parcelle_id.label("pid"),
                    func.max(HistoriqueCultural.annee).label("a"))
                .filter(HistoriqueCultural.parcelle_id.in_(ids))
                .group_by(HistoriqueCultural.parcelle_id).subquery())
        for pid, cult, lib in (
            db.session.query(HistoriqueCultural.parcelle_id, HistoriqueCultural.culture,
                             Campagne.libelle)
            .join(subh, and_(subh.c.pid == HistoriqueCultural.parcelle_id,
                             subh.c.a == HistoriqueCultural.annee))
            .join(Campagne, Campagne.id == HistoriqueCultural.campagne_id).all()):
            hist[pid] = (lib, cult)

    vues = []
    for p in parcelles:
        v = idx.get(p.id)
        ndvi = v[0] if v else None
        ndmi = v[1] if v else None
        alerte = (ndvi is not None and ndmi is not None and ndvi < 0.30 and ndmi < 0.0)
        vues.append({
            "p": p,
            "ndvi": ndvi,
            "veg": interpret("ndvi", ndvi) if ndvi is not None else None,
            "alerte": alerte,
            "hist": hist.get(p.id),
        })

    cultures = [c[0] for c in (
        db.session.query(Parcelle.culture)
        .filter(Parcelle.culture.isnot(None))
        .distinct().order_by(Parcelle.culture).all())]

    from app.models.programme import Programme
    return render_template(
        "parcelles/liste.html",
        vues=vues, pagination=pagination,
        q=q, culture=culture, region_id=region_id, campagne_id=campagne_id,
        programme_id=programme_id,
        cultures=cultures,
        regions=Region.query.order_by(Region.nom).all(),
        campagnes=Campagne.query.order_by(Campagne.annee.desc()).all(),
        programmes=Programme.query.order_by(Programme.nom).all(),
    )


@parcelles_bp.route("/carte")
@login_required
def carte():
    return render_template("parcelles/carte.html")


@parcelles_bp.route("/geojson")
@login_required
def geojson():
    # Dernier NDVI par parcelle (pour colorer l'état)
    _sub = (db.session.query(
                IndiceVegetation.parcelle_id.label("pid"),
                func.max(IndiceVegetation.date).label("d"))
            .group_by(IndiceVegetation.parcelle_id).subquery())
    rows = (
        db.session.query(
            Parcelle.id, Parcelle.code, Parcelle.culture, Parcelle.surface_ha,
            Producteur.nom, Producteur.prenom,
            func.ST_AsGeoJSON(Parcelle.geom),
            IndiceVegetation.ndvi,
        )
        .outerjoin(Producteur, Producteur.id == Parcelle.producteur_id)
        .outerjoin(_sub, _sub.c.pid == Parcelle.id)
        .outerjoin(IndiceVegetation, and_(
            IndiceVegetation.parcelle_id == Parcelle.id,
            IndiceVegetation.date == _sub.c.d))
        .filter(Parcelle.geom.isnot(None))
    )
    _pids = parcelle_ids(current_user)
    if _pids is not None:
        rows = rows.filter(Parcelle.id.in_(_pids))
    rows = rows.all()
    features = []
    for pid, code, culture, surface, prod_nom, prod_prenom, geom_json, ndvi in rows:
        if not geom_json:
            continue
        producteur = None
        if prod_nom:
            producteur = f"{prod_prenom or ''} {prod_nom}".strip()
        features.append({
            "type": "Feature",
            "geometry": json.loads(geom_json),
            "properties": {
                "id": pid,
                "code": code,
                "culture": culture,
                "surface_ha": surface,
                "producteur": producteur,
                "ndvi": round(ndvi, 3) if ndvi is not None else None,
            },
        })
    return jsonify({"type": "FeatureCollection", "features": features})


# --- CRUD attributs ---------------------------------------------------------
@parcelles_bp.route("/<int:pid>/evolution")
@login_required
def evolution(pid):
    """Évolution en vraies couleurs : 4 étapes depuis le 15 mai (tuiles RGB GEE)."""
    from datetime import date, timedelta, datetime as _dt
    from flask import current_app
    from app.teledetection.gee import rgb_tiles, index_tiles

    p = db.session.get(Parcelle, pid)
    if not p or p.geom is None:
        return jsonify({"error": "Parcelle introuvable ou sans géométrie."}), 404
    if not peut_voir_parcelle(current_user, pid):
        return jsonify({"error": "Hors périmètre."}), 403

    project = current_app.config.get("GEE_PROJECT")
    if not project:
        return jsonify({"error": "GEE_PROJECT n'est pas défini."}), 400

    gj = db.session.query(func.ST_AsGeoJSON(Parcelle.geom)).filter(
        Parcelle.id == pid).scalar()
    if not gj:
        return jsonify({"error": "Géométrie indisponible."}), 404
    feature = {"geometry": json.loads(gj)}

    # Fenêtre 15 mai -> aujourd'hui, découpée en 4 étapes
    try:
        d1 = _dt.strptime(request.args.get("d1", ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        d1 = date(date.today().year, 5, 15)
    d2 = date.today()
    if d2 <= d1:
        d2 = d1 + timedelta(days=112)
    step = max(1, (d2 - d1).days // 4)

    stages = []
    for i in range(4):
        a = d1 + timedelta(days=i * step)
        b = d2 if i == 3 else d1 + timedelta(days=(i + 1) * step)
        url, ndmi_url, err = None, None, None
        try:
            url = rgb_tiles([feature], a.isoformat(), b.isoformat(), project)
        except Exception as exc:   # période nuageuse / sans image
            err = str(exc)[:120]
        try:
            ndmi_url = index_tiles([feature], a.isoformat(), b.isoformat(),
                                   project, indice="ndmi")
        except Exception:
            ndmi_url = None
        stages.append({
            "label": f"{a.strftime('%d/%m')} – {b.strftime('%d/%m')}",
            "d1": a.isoformat(), "d2": b.isoformat(),
            "url": url, "ndmi_url": ndmi_url, "erreur": err,
        })

    return jsonify({"geometry": json.loads(gj), "stages": stages})


@parcelles_bp.route("/<int:pid>/suivi", methods=["POST"])
@login_required
def enregistrer_suivi(pid):
    """Ajoute une observation de terrain (animateur / CAR, niveau >= 2)."""
    from datetime import datetime as _dt, date as _date
    from app.models.suivi import SuiviTerrain
    p = db.session.get(Parcelle, pid)
    if not p:
        flash("Parcelle introuvable.", "warning")
        return redirect(url_for("parcelles.liste"))
    if not peut_voir_parcelle(current_user, pid):
        flash("Cette parcelle est hors de votre périmètre.", "warning")
        return redirect(url_for("parcelles.liste"))
    if current_user.niveau < 2:
        flash("Vous n'avez pas le droit d'ajouter un suivi.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid))

    def _i(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    try:
        d = _dt.strptime(request.form.get("date_obs", ""), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        d = _date.today()

    obs = SuiviTerrain(
        parcelle_id=pid,
        campagne_id=_i(request.form.get("campagne_id")),
        date_obs=d,
        phase=(request.form.get("phase") or None),
        actions=(request.form.get("actions") or None),
        tendance=(request.form.get("tendance") or None),
        difficultes=(request.form.get("difficultes") or None),
        auteur_id=current_user.id,
    )
    db.session.add(obs)
    db.session.commit()
    flash("Observation de terrain enregistrée.", "success")
    return redirect(url_for("parcelles.fiche", pid=pid) + "#suivi")


@parcelles_bp.route("/<int:pid>/carte-image")
@login_required
def carte_image(pid):
    """Télécharge une carte annotée (PNG) : RGB / NDVI / NDMI, période au choix."""
    from datetime import date, datetime as _dt
    from flask import current_app, send_file
    from app.teledetection.gee import thumb_url
    from app.parcelles.carte_image import composer

    p = db.session.get(Parcelle, pid)
    if not p or p.geom is None:
        flash("Parcelle introuvable ou sans géométrie.", "warning")
        return redirect(url_for("parcelles.liste"))
    if not peut_voir_parcelle(current_user, pid):
        flash("Cette parcelle est hors de votre périmètre.", "warning")
        return redirect(url_for("parcelles.liste"))

    indice = request.args.get("indice", "rgb")
    if indice not in ("rgb", "ndvi", "ndmi"):
        indice = "rgb"
    d1 = request.args.get("d1") or f"{date.today().year}-05-15"
    d2 = request.args.get("d2") or date.today().isoformat()

    project = current_app.config.get("GEE_PROJECT")
    if not project:
        flash("GEE_PROJECT n'est pas défini dans config.py.", "danger")
        return redirect(url_for("parcelles.fiche", pid=pid))

    gj = db.session.query(func.ST_AsGeoJSON(Parcelle.geom)).filter(Parcelle.id == pid).scalar()
    feature = {"geometry": json.loads(gj)}

    # largeur approx (m) de la parcelle pour l'échelle
    largeur_m = None
    try:
        bb = db.session.query(func.ST_XMin(func.ST_Envelope(func.ST_Transform(Parcelle.geom, 32628))),
                              func.ST_XMax(func.ST_Envelope(func.ST_Transform(Parcelle.geom, 32628)))
                              ).filter(Parcelle.id == pid).first()
        if bb and bb[0] is not None:
            largeur_m = abs(bb[1] - bb[0])
    except Exception:
        largeur_m = None

    prod = p.producteur
    meta = {
        "producteur": prod.nom_complet if prod else "—",
        "culture": p.culture or "—",
        "superficie": f"{p.surface_ha:.2f} ha" if p.surface_ha else "—",
        "commune": p.commune or "—",
        "animateur": p.animateur or "—",
        "date": d2,
        "periode": f"Période : {d1} au {d2}",
        "largeur_m": largeur_m,
    }
    try:
        url = thumb_url([feature], d1, d2, project, indice=indice)
    except Exception as exc:  # noqa
        flash(f"Erreur Earth Engine : {exc}", "danger")
        return redirect(url_for("parcelles.fiche", pid=pid))

    import os
    logo = os.path.join(current_app.root_path, "static", "img", "logo_ancar.png")
    buf = composer(url, indice, meta, logo_path=logo)
    nom = f"carte_{indice}_{(p.code or pid)}_{d2}.png".replace(" ", "_")
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=nom)


@parcelles_bp.route("/<int:pid>/sol", methods=["POST"])
@login_required
def enregistrer_sol(pid):
    """Saisie/mise à jour de la qualité du sol d'une parcelle (niveau >= 2)."""
    p = db.session.get(Parcelle, pid)
    if not p:
        flash("Parcelle introuvable.", "warning")
        return redirect(url_for("parcelles.liste"))
    if not peut_voir_parcelle(current_user, pid):
        flash("Cette parcelle est hors de votre périmètre.", "warning")
        return redirect(url_for("parcelles.liste"))
    if current_user.niveau < 2:
        flash("Vous n'avez pas le droit de modifier le sol.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid))

    def _f(v):
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    p.sol_type = (request.form.get("sol_type") or None)
    p.sol_ph = _f(request.form.get("sol_ph"))
    p.sol_mo = _f(request.form.get("sol_mo"))
    p.sol_fertilite = (request.form.get("sol_fertilite") or None)
    p.sol_source = (request.form.get("sol_source") or None)
    p.sol_note = (request.form.get("sol_note") or None)
    db.session.commit()
    flash("Qualité du sol enregistrée.", "success")
    return redirect(url_for("parcelles.fiche", pid=pid) + "#sol")


@parcelles_bp.route("/<int:pid>/potentiel", methods=["POST"])
@login_required
def enregistrer_potentiel(pid):
    """Met à jour le rendement potentiel (kg/ha) d'une parcelle, et sa référence."""
    from app.models.rendement import RendementReference
    p = db.session.get(Parcelle, pid)
    if not p:
        flash("Parcelle introuvable.", "warning")
        return redirect(url_for("parcelles.liste"))
    if not peut_voir_parcelle(current_user, pid):
        flash("Cette parcelle est hors de votre périmètre.", "warning")
        return redirect(url_for("parcelles.liste"))
    if current_user.niveau < 2:
        flash("Vous n'avez pas le droit de modifier le rendement potentiel.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid))
    try:
        val = float(str(request.form.get("rendement_potentiel", "")).replace(",", "."))
    except (TypeError, ValueError):
        flash("Valeur de rendement invalide.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid) + "#potentiel")
    p.rendement_potentiel = val
    annee = 2026
    ref = (RendementReference.query
           .filter_by(parcelle_id=pid, annee=annee).first())
    if ref:
        ref.rendement_kg_ha = val
        ref.source = "Potentiel variété (Mastrop)"
    else:
        db.session.add(RendementReference(
            parcelle_id=pid, annee=annee, culture=(p.culture or "Mastrop"),
            rendement_kg_ha=val, source="Potentiel variété (Mastrop)"))
    db.session.commit()
    flash("Rendement potentiel mis à jour.", "success")
    return redirect(url_for("parcelles.fiche", pid=pid) + "#potentiel")


@parcelles_bp.route("/<int:pid>/fiche")
@login_required
def fiche(pid):
    p = db.session.get(Parcelle, pid)
    if not p:
        flash("Parcelle introuvable.", "warning")
        return redirect(url_for("parcelles.liste"))
    if not peut_voir_parcelle(current_user, pid):
        flash("Cette parcelle est hors de votre périmètre.", "warning")
        return redirect(url_for("parcelles.liste"))
    indices = (
        IndiceVegetation.query.filter_by(parcelle_id=pid)
        .order_by(IndiceVegetation.date)
        .all()
    )
    serie = {
        "dates": [iv.date.isoformat() for iv in indices],
        "ndvi": [iv.ndvi for iv in indices],
        "ndwi": [iv.ndwi for iv in indices],
        "ndmi": [iv.ndmi for iv in indices],
        "savi": [iv.savi for iv in indices],
        "evi": [iv.evi for iv in indices],
    }

    # Lecture agronomique du dernier relevé
    from app.teledetection.interpretation import interpret, conseil_fertilisation
    lecture = None
    if indices:
        d = indices[-1]
        lecture = {
            "date": d.date,
            "ndvi_val": d.ndvi, "ndmi_val": d.ndmi,
            "vegetation": interpret("ndvi", d.ndvi),
            "hydrique": interpret("ndmi", d.ndmi),
            "conseil": conseil_fertilisation(d.ndvi, d.ndmi),
        }

    # Historique cultural (culture par campagne)
    historique = (
        HistoriqueCultural.query.filter_by(parcelle_id=pid)
        .join(Campagne, HistoriqueCultural.campagne_id == Campagne.id)
        .order_by(Campagne.annee.desc(), Campagne.libelle.desc())
        .all()
    )
    cultures_distinctes = {h.culture for h in historique if h.culture}
    rotation = None
    if len(historique) >= 2:
        rotation = "Rotation" if len(cultures_distinctes) > 1 else "Monoculture"

    # Évolution du rendement de la parcelle (par année)
    rdt_rows = (RendementReference.query.filter_by(parcelle_id=pid)
                .filter(RendementReference.annee.isnot(None))
                .order_by(RendementReference.annee).all())
    rdt_evolution = [{"annee": r.annee, "rendement": round(r.rendement_kg_ha)}
                     for r in rdt_rows]

    # Suivi de terrain (observations animateur / CAR)
    suivis = (SuiviTerrain.query.filter_by(parcelle_id=pid)
              .order_by(SuiviTerrain.date_obs.desc(), SuiviTerrain.id.desc()).all())
    from app.models.campagne import Campagne as _Camp
    campagnes_suivi = _Camp.query.order_by(_Camp.annee.desc()).all()
    from datetime import date as _date, timedelta as _td
    aujourdhui = _date.today().isoformat()
    _d1 = _date(_date.today().year, 5, 15)
    _d2 = _date.today()
    _step = max(1, (_d2 - _d1).days // 4)
    dl_d1 = (_d1 + _td(days=3 * _step)).isoformat()
    dl_d2 = _d2.isoformat()

    return render_template("parcelles/fiche.html", p=p, serie=serie,
                           indices=indices, lecture=lecture,
                           historique=historique, rotation=rotation,
                           rdt_evolution=rdt_evolution,
                           suivis=suivis, campagnes_suivi=campagnes_suivi,
                           aujourdhui=aujourdhui, dl_d1=dl_d1, dl_d2=dl_d2)


@parcelles_bp.route("/<int:pid>/geojson")
@login_required
def parcelle_geojson(pid):
    row = (
        db.session.query(Parcelle.code, func.ST_AsGeoJSON(Parcelle.geom))
        .filter(Parcelle.id == pid, Parcelle.geom.isnot(None))
        .first()
    )
    features = []
    if row and row[1]:
        features.append({
            "type": "Feature",
            "geometry": json.loads(row[1]),
            "properties": {"code": row[0]},
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@parcelles_bp.route("/nouvelle", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def creer():
    if request.method == "POST":
        p = Parcelle(
            code=request.form.get("code") or None,
            culture=request.form.get("culture") or None,
            surface_ha=_f(request.form.get("surface_ha")),
            observations=request.form.get("observations") or None,
            producteur_id=_i(request.form.get("producteur_id")),
            filiere_id=_i(request.form.get("filiere_id")),
            campagne_id=_i(request.form.get("campagne_id")),
        )
        db.session.add(p)
        db.session.commit()
        flash("Parcelle créée.", "success")
        return redirect(url_for("parcelles.liste"))
    return render_template("parcelles/form.html", parcelle=None, **_refs_formulaire())


@parcelles_bp.route("/<int:pid>/modifier", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def modifier(pid):
    p = db.session.get(Parcelle, pid)
    if not p:
        flash("Parcelle introuvable.", "warning")
        return redirect(url_for("parcelles.liste"))
    if request.method == "POST":
        p.code = request.form.get("code") or None
        p.culture = request.form.get("culture") or None
        p.surface_ha = _f(request.form.get("surface_ha"))
        p.observations = request.form.get("observations") or None
        p.producteur_id = _i(request.form.get("producteur_id"))
        p.filiere_id = _i(request.form.get("filiere_id"))
        p.campagne_id = _i(request.form.get("campagne_id"))
        db.session.commit()
        flash("Parcelle mise à jour.", "success")
        return redirect(url_for("parcelles.liste"))
    return render_template("parcelles/form.html", parcelle=p, **_refs_formulaire())


@parcelles_bp.route("/<int:pid>/supprimer", methods=["POST"])
@login_required
@niveau_minimum(2)
def supprimer(pid):
    p = db.session.get(Parcelle, pid)
    if p:
        db.session.delete(p)
        db.session.commit()
        flash("Parcelle supprimée.", "info")
    return redirect(url_for("parcelles.liste"))


# --- Import GeoJSON (secours) ----------------------------------------------
@parcelles_bp.route("/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def importer():
    from app.models.programme import Programme
    if request.method == "POST":
        fichier = request.files.get("fichier")
        srid_src = request.form.get("srid_source", "4326")
        mode = request.form.get("mode", "additif")          # additif | maj
        apercu = bool(request.form.get("apercu"))            # simulation
        try:
            seuil = float(request.form.get("seuil_overlap", "60")) / 100.0
        except (TypeError, ValueError):
            seuil = 0.60
        seuil = min(max(seuil, 0.1), 1.0)

        if not fichier or fichier.filename == "":
            flash("Aucun fichier sélectionné.", "warning")
            return redirect(url_for("parcelles.importer"))
        try:
            data = json.load(fichier.stream)
        except Exception:
            flash("Fichier GeoJSON invalide.", "danger")
            return redirect(url_for("parcelles.importer"))

        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]

        transformer = None
        if srid_src != "4326":
            transformer = Transformer.from_crs(
                f"EPSG:{srid_src}", "EPSG:4326", always_xy=True).transform

        import hashlib
        from shapely.wkt import loads as _wl, dumps as _wd

        def _sig(gg):
            return hashlib.md5(_wd(gg, rounding_precision=6).encode()).hexdigest()

        # Programme (créé à la volée si le nom saisi n'existe pas)
        prog_nom = (request.form.get("programme") or "").strip()
        programme_id = None
        if prog_nom:
            prog = Programme.query.filter(func.lower(Programme.nom) == prog_nom.lower()).first()
            if not prog:
                prog = Programme(nom=prog_nom, actif=True)
                db.session.add(prog)
                db.session.flush()
            programme_id = prog.id

        # Géométries existantes : signatures (exact) + index spatial (recouvrement)
        existants = []   # (pid, shapely_geom)
        sigs_existantes = {}
        for (pid_ex, wktxt) in (db.session.query(Parcelle.id, func.ST_AsText(Parcelle.geom))
                                .filter(Parcelle.geom.isnot(None)).all()):
            try:
                gg = _wl(wktxt)
                existants.append((pid_ex, gg))
                sigs_existantes[_sig(gg)] = pid_ex
            except Exception:
                pass
        geoms_exist = [g for (_, g) in existants]
        tree = None
        if mode == "maj" and geoms_exist:
            from shapely.strtree import STRtree
            tree = STRtree(geoms_exist)

        codes_existants = {c for (c,) in db.session.query(Parcelle.code)
                           .filter(Parcelle.code.isnot(None)).all()}
        regions_par_nom = {r.nom.strip().lower(): r for r in Region.query.all()}
        camp_cache = {c.libelle: c for c in Campagne.query.all()}
        camp_active = (Campagne.query.filter_by(active=True)
                       .order_by(Campagne.annee.desc()).first())
        orgs_cache = {o.nom.strip().lower(): o for o in OrganisationProducteur.query.all()}
        prods_cache = {}
        for pr in Producteur.query.all():
            prods_cache[(pr.nom_complet.strip().lower(), (pr.telephone or "").strip())] = pr

        def _prod_coop(nom_coop, nom_prod, tel_prod, sexe, region_obj):
            org = orgs_cache.get(str(nom_coop).strip().lower()) if nom_coop else None
            if nom_coop and not org:
                org = OrganisationProducteur(nom=str(nom_coop).strip()[:150], type="Coopérative")
                if region_obj:
                    org.region_id = region_obj.id
                db.session.add(org)
                orgs_cache[str(nom_coop).strip().lower()] = org
            prod = None
            if nom_prod:
                pk = (str(nom_prod).strip().lower(), (str(tel_prod).strip() if tel_prod else ""))
                prod = prods_cache.get(pk)
                if not prod:
                    prod = Producteur(nom=str(nom_prod).strip()[:80],
                                      telephone=(str(tel_prod).strip()[:30] if tel_prod else None),
                                      sexe=sexe)
                    if region_obj:
                        prod.region_id = region_obj.id
                    if org:
                        prod.organisation = org
                    db.session.add(prod)
                    prods_cache[pk] = prod
                elif org and not prod.organisation_id:
                    prod.organisation = org
            return prod, org

        def _historique(p, props):
            for cle, val in props.items():
                if not val or "culture" not in cle.lower():
                    continue
                m = _ANNEE_RE.search(cle)
                if not m:
                    continue
                annee = int(m.group(1))
                lib = f"Hivernage {annee}"
                camp = camp_cache.get(lib)
                if not camp:
                    camp = (Campagne.query.filter_by(libelle=lib).first()
                            or Campagne(libelle=lib, annee=annee, active=False))
                    db.session.add(camp)
                    camp_cache[lib] = camp
                db.session.add(HistoriqueCultural(parcelle=p, campagne=camp,
                                                  annee=annee, culture=str(val)[:80], source="GeoJSON"))

        n_new = n_upd = n_redraw = n_dup = n_skip = 0
        for feat in features:
            geom = feat.get("geometry")
            props = feat.get("properties", {}) or {}
            if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
                n_skip += 1
                continue
            try:
                g = shape(geom)
                if transformer:
                    g = shp_transform(transformer, g)
                if g.geom_type == "Polygon":
                    g = MultiPolygon([g])
            except Exception:
                n_skip += 1
                continue

            # --- Attributs (mappage tolérant, y compris noms tronqués QGIS) ---
            code = _premier(props, ["code", "nom", "Nom"])
            sig = _sig(g)
            if not code:
                code = f"P-{sig[:8]}"
            code = str(code)[:50]
            surface = (_f(_premier(props, ["surface_ha", "surface", "Superficie",
                        "Superifie", "superficie", "Surface (ha)", "Surface"]))
                       or _ha_depuis_wgs84(g))
            culture = _culture_courante(props) or _premier(props, [
                "Culture hi", "Culture actuelle", "culture", "Culture", "Culture _1"])
            dep = _premier(props, ["Departement", "Département", "Departemen", "departement"])
            com = _premier(props, ["Commune", "commune"])
            vil = _premier(props, ["Village", "village", "Localité", "Localite"])
            reg = _premier(props, ["Region", "Région", "region"])
            observations = _premier(props, ["observations", "Observations"])
            nom_prod = _premier(props, [
                "Prenom et nom producteur", "Nom & Prénom du producteur",
                "Prénom et nom producteur", "Nom et prenom producteur",
                "Prenom_nom", "producteur", "Producteur"])
            tel_prod = _premier(props, [
                "Telephone producteur", "Numero de telephone Producteur",
                "Téléphone producteur", "Telephone_", "telephone"])
            nom_coop = _premier(props, [
                "cooperative_norm", "Nom cooperative", "Nom cooper",
                "Coopérative", "Cooperative", "cooperative"])
            animateur = _premier(props, [
                "Nom & Prénom de l'agent", "Nom & Prenom de l'agent",
                "Prenom et nom enqueteur", "Prénom et nom enquêteur",
                "Nom animateur", "animateur", "Animateur", "agent", "Agent",
                "enqueteur", "Enquêteur"])
            animateur = str(animateur).strip()[:120] if animateur else None
            sexe = _sexe(props)

            # --- Détermination de la cible ---
            cible = sigs_existantes.get(sig)     # correspondance exacte (même forme)
            is_redraw = False
            if cible is None and mode == "maj" and tree is not None and g.area:
                best_pid, best_r = None, 0.0
                for idx in tree.query(g):
                    cand = geoms_exist[int(idx)]
                    try:
                        r = g.intersection(cand).area / g.area
                    except Exception:
                        continue
                    if r > best_r:
                        best_r, best_pid = r, existants[int(idx)][0]
                if best_pid is not None and best_r >= seuil:
                    cible, is_redraw = best_pid, True

            try:
                with db.session.begin_nested():
                    region_obj = regions_par_nom.get(reg.strip().lower()) if reg else None

                    if cible is None:
                        # ----- CRÉATION -----
                        if code in codes_existants:
                            base, k = code, 1
                            while code in codes_existants:
                                code = f"{base}-{k}"; k += 1
                        prod, org = _prod_coop(nom_coop, nom_prod, tel_prod, sexe, region_obj)
                        p = Parcelle(code=code, culture=culture, surface_ha=surface,
                                     observations=observations,
                                     departement=(dep.strip() if dep else None),
                                     commune=(com.strip() if com else None),
                                     village=(vil.strip() if vil else None),
                                     animateur=animateur,
                                     geom=WKTElement(g.wkt, srid=4326))
                        if region_obj:
                            p.region_id = region_obj.id
                        if programme_id:
                            p.programme_id = programme_id
                        if camp_active:
                            p.campagne_id = camp_active.id
                        if prod:
                            p.producteur = prod
                        db.session.add(p)
                        _historique(p, props)
                        sigs_existantes[sig] = None
                        codes_existants.add(code)
                        n_new += 1

                    elif mode == "additif":
                        # ----- doublon (forme identique) : on complète seulement -----
                        p = db.session.get(Parcelle, cible)
                        if p:
                            if animateur and not p.animateur:
                                p.animateur = animateur
                            if programme_id and not p.programme_id:
                                p.programme_id = programme_id
                        n_dup += 1

                    else:
                        # ----- MISE À JOUR (mode maj) -----
                        p = db.session.get(Parcelle, cible)
                        if p:
                            prod, org = _prod_coop(nom_coop, nom_prod, tel_prod, sexe, region_obj)
                            if is_redraw:
                                p.geom = WKTElement(g.wkt, srid=4326)
                            if culture:
                                p.culture = culture
                            if surface:
                                p.surface_ha = surface
                            if dep:
                                p.departement = dep.strip()
                            if com:
                                p.commune = com.strip()
                            if vil:
                                p.village = vil.strip()
                            if region_obj:
                                p.region_id = region_obj.id
                            if programme_id:
                                p.programme_id = programme_id
                            if animateur:
                                p.animateur = animateur
                            if prod:
                                p.producteur = prod
                            if is_redraw:
                                n_redraw += 1
                            else:
                                n_upd += 1
            except Exception:
                n_skip += 1

        if apercu:
            db.session.rollback()
            flash(f"APERÇU (aucune écriture) — mode « {mode} » : "
                  f"{n_new} à créer, {n_upd} attribut(s) à mettre à jour, "
                  f"{n_redraw} redessinée(s) détectée(s), {n_dup} inchangée(s) (doublon), "
                  f"{n_skip} en erreur. Décochez « aperçu » pour appliquer.", "info")
            return redirect(url_for("parcelles.importer"))

        db.session.commit()
        flash(f"Import « {mode} » : {n_new} créée(s), {n_upd} mise(s) à jour, "
              f"{n_redraw} redessinée(s), {n_dup} inchangée(s), {n_skip} en erreur."
              + (f" Programme « {prog_nom} » appliqué." if prog_nom else ""), "success")
        return redirect(url_for("parcelles.carte"))

    programmes = Programme.query.order_by(Programme.nom).all()
    return render_template("parcelles/import.html", programmes=programmes)


_ANNEE_RE = re.compile(r"(20\d{2})")


def _culture_courante(props):
    """Culture la plus récente parmi les propriétés 'culture ... AAAA'."""
    best_year, best = -1, None
    for k, v in props.items():
        if v and "culture" in k.lower():
            m = _ANNEE_RE.search(k)
            if m and int(m.group(1)) > best_year:
                best_year, best = int(m.group(1)), str(v).strip()
    return best or _premier(props, ["culture", "Culture"])


def _sexe(props):
    v = _premier(props, ["Sexe", "Genre (Homme/Femme)", "sexe", "genre"])
    if not v:
        return None
    v = str(v).strip().upper()
    if v.startswith("H") or v.startswith("M"):
        return "H"
    if v.startswith("F"):
        return "F"
    return None


@parcelles_bp.route("/historique/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def import_historique():
    if request.method == "POST":
        fichier = request.files.get("fichier")
        if not fichier or fichier.filename == "":
            flash("Aucun fichier sélectionné.", "warning")
            return redirect(url_for("parcelles.import_historique"))
        try:
            data = json.load(fichier.stream)
        except Exception:
            flash("Fichier GeoJSON invalide.", "danger")
            return redirect(url_for("parcelles.import_historique"))

        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
        parcelles_par_code = {p.code: p for p in Parcelle.query.all() if p.code}
        camp_cache = {c.libelle: c for c in Campagne.query.all()}
        existants = {
            (h.parcelle_id, h.campagne_id): h for h in HistoriqueCultural.query.all()
        }

        n = 0
        for feat in features:
            props = feat.get("properties", {}) or {}
            fid = None
            try:
                fid = int(props.get("fid") or props.get("id"))
            except (TypeError, ValueError):
                pass
            code = props.get("code") or props.get("nom")
            if not code and fid:
                code = f"P-{fid:05d}"
            parc = parcelles_par_code.get(code) if code else None
            if not parc:
                continue

            for cle, val in props.items():
                if not val or "culture" not in cle.lower():
                    continue
                m = _ANNEE_RE.search(cle)
                if not m:
                    continue
                annee = int(m.group(1))
                libelle = f"Hivernage {annee}"

                # Campagne (créée si absente)
                camp = camp_cache.get(libelle)
                if not camp:
                    camp = Campagne.query.filter_by(libelle=libelle).first()
                    if not camp:
                        camp = Campagne(libelle=libelle, annee=annee, active=False)
                        db.session.add(camp)
                        db.session.flush()
                    camp_cache[libelle] = camp

                cle_hist = (parc.id, camp.id)
                h = existants.get(cle_hist)
                if not h:
                    h = HistoriqueCultural(parcelle_id=parc.id, campagne_id=camp.id, annee=annee)
                    db.session.add(h)
                    existants[cle_hist] = h
                h.culture = str(val)[:80]
                h.source = "GeoJSON"
                n += 1
        db.session.commit()
        flash(f"Historique importé : {n} enregistrement(s) culture-campagne.", "success")
        return redirect(url_for("parcelles.liste"))

    return render_template("parcelles/import_historique.html")


@parcelles_bp.route("/geo/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def import_geo():
    if request.method == "POST":
        fichier = request.files.get("fichier")
        if not fichier or fichier.filename == "":
            flash("Aucun fichier sélectionné.", "warning")
            return redirect(url_for("parcelles.import_geo"))
        try:
            data = json.load(fichier.stream)
        except Exception:
            flash("Fichier GeoJSON invalide.", "danger")
            return redirect(url_for("parcelles.import_geo"))

        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
        parcelles_par_code = {p.code: p for p in Parcelle.query.all() if p.code}
        regions_par_nom = {r.nom.strip().lower(): r for r in Region.query.all()}

        n = 0
        for feat in features:
            props = feat.get("properties", {}) or {}
            fid = None
            try:
                fid = int(props.get("fid") or props.get("id"))
            except (TypeError, ValueError):
                pass
            code = props.get("code") or props.get("nom")
            if not code and fid:
                code = f"P-{fid:05d}"
            parc = parcelles_par_code.get(code) if code else None
            if not parc:
                continue

            dep = (props.get("Departement") or props.get("Département") or "").strip() or None
            com = (props.get("Commune") or "").strip() or None
            vil = (props.get("Village") or "").strip() or None
            reg = (props.get("Region") or props.get("Région") or "").strip() or None

            parc.departement = dep
            parc.commune = com
            parc.village = vil
            if reg:
                r = regions_par_nom.get(reg.lower())
                if r:
                    parc.region_id = r.id
            n += 1
        db.session.commit()
        flash(f"Localisation mise à jour pour {n} parcelle(s).", "success")
        return redirect(url_for("parcelles.liste"))

    return render_template("parcelles/import_geo.html")
