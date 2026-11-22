import json
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)
from flask_login import login_required, current_user
from sqlalchemy import func, and_, text
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
    """Première valeur non vide parmi une liste de clés candidates (majuscules, accents et
    espaces en trop ignorés : « Telephone_producteur » = « telephone_producteur »)."""
    from app.parcelles.enquete import norm
    pn = {}
    for k, v in props.items():
        pn.setdefault(norm(k), v)
    for c in cles:
        v = pn.get(norm(c))
        if v not in (None, "") and str(v).strip().lower() not in ("none", "null"):
            return v
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


@parcelles_bp.route("/<int:pid>/sol", methods=["GET", "POST"])
@login_required
def enregistrer_sol(pid):
    """Ancienne saisie du sol : remplacée par le module Sol (analyse complète + historique)."""
    return redirect(url_for("sol.parcelle", pid=pid, retour="fiche"))


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
    if request.form.get("automatique"):
        # retour au potentiel du référentiel (variété ou spéculation)
        try:
            from app.models.referentiel import PotentielParcelle
            from app.referentiel.service import appliquer_potentiels
            db.session.query(PotentielParcelle).filter_by(parcelle_id=pid).delete()
            s = appliquer_potentiels([pid])
            flash("Potentiel recalculé depuis le référentiel." if (s["variete"] or s["speculation"])
                  else "Pas de potentiel dans le référentiel pour cette culture.", "success")
        except Exception:
            db.session.rollback()
            flash("Référentiel des cultures non installé (python init_referentiel.py).", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid) + "#potentiel")
    try:
        val = float(str(request.form.get("rendement_potentiel", "")).replace(",", "."))
    except (TypeError, ValueError):
        flash("Valeur de rendement invalide.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid) + "#potentiel")
    p.rendement_potentiel = val
    annee = 2026
    ref = (RendementReference.query
           .filter_by(parcelle_id=pid, annee=annee)
           .filter(RendementReference.source.like("Potentiel%")).first())
    if ref:
        ref.rendement_kg_ha = val
        ref.source = "Potentiel (saisie manuelle)"
    else:
        db.session.add(RendementReference(
            parcelle_id=pid, annee=annee, culture=p.culture,
            rendement_kg_ha=val, source="Potentiel (saisie manuelle)"))
    # saisie manuelle : protégée de l'application automatique des potentiels
    try:
        from app.models.referentiel import PotentielParcelle
        with db.session.begin_nested():
            pp = db.session.get(PotentielParcelle, pid) or PotentielParcelle(parcelle_id=pid, origine="manuel")
            pp.origine, pp.variete_id = "manuel", None
            db.session.add(pp)
    except Exception:
        pass
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

        # Système de coordonnées déclaré dans le fichier : il prime sur le choix du formulaire.
        # Coordonnées en degrés alors qu'un système métrique est choisi : WGS 84.
        crs_nom = str(((data.get("crs") or {}).get("properties") or {}).get("name") or "")
        m_crs = re.search(r"EPSG:+(\d{4,5})", crs_nom)
        if m_crs:
            srid_src = m_crs.group(1)
        elif "CRS84" in crs_nom:
            srid_src = "4326"
        elif srid_src != "4326":
            try:
                xy = next(f["geometry"]["coordinates"] for f in features if f.get("geometry"))
                while isinstance(xy[0], list):
                    xy = xy[0]
                if abs(xy[0]) <= 180 and abs(xy[1]) <= 90:
                    srid_src = "4326"
            except (StopIteration, KeyError, IndexError, TypeError):
                pass

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
        culture_defaut = (request.form.get("culture_defaut") or "").strip() or None
        programme_id = None
        if prog_nom:
            prog = Programme.query.filter(func.lower(Programme.nom) == prog_nom.lower()).first()
            if not prog:
                prog = Programme(nom=prog_nom, actif=True)
                db.session.add(prog)
                db.session.flush()
            programme_id = prog.id
        programmes_cache = {pr.nom.strip().lower(): pr for pr in Programme.query.all()}

        def _programme_entite(props):
            """Programme indiqué dans le fichier (champ « programme »), sinon celui du formulaire."""
            nom = _premier(props, ["programme", "Programme", "projet"])
            if not nom:
                return programme_id
            k = str(nom).strip().lower()
            pr = programmes_cache.get(k)
            if pr is None:
                pr = Programme(nom=str(nom).strip()[:120], actif=True)
                db.session.add(pr)
                db.session.flush()
                programmes_cache[k] = pr
            return pr.id

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
        geom_par_pid = dict(existants)
        from app.models.enquete import SourceParcelle
        sources_exist = {sp.source_id: sp.parcelle_id for sp in db.session.query(SourceParcelle)}
        prod_exist = {pid_: _enq_norm0(n or "") for pid_, n in
                      db.session.query(Parcelle.id, Producteur.nom)
                      .outerjoin(Producteur, Producteur.id == Parcelle.producteur_id).all()}
        tree = None
        if mode == "maj" and geoms_exist:
            from shapely.strtree import STRtree
            tree = STRtree(geoms_exist)

        codes_existants = {c for (c,) in db.session.query(Parcelle.code)
                           .filter(Parcelle.code.isnot(None)).all()}
        from app.parcelles import enquete as _enq
        regions_par_nom = {_enq.norm(r.nom): r for r in Region.query.all()}
        camp_cache = {c.libelle: c for c in Campagne.query.all()}
        camp_active = (Campagne.query.filter_by(active=True)
                       .order_by(Campagne.annee.desc()).first())
        from datetime import date as _d
        annee_active = camp_active.annee if camp_active else _d.today().year
        n_prat = n_rdt = 0

        touchees, progs_fichier = set(), set()

        from app.models.indice import IndiceVegetation as _IV
        from datetime import datetime as _dtm
        n_indices = 0

        def _complements(p, props):
            """Champs de l'export complet : rendement potentiel, sol, indices à chaque passage."""
            nonlocal n_indices
            pot = _f(_premier(props, ["rendement_potentiel_kg_ha"]))
            if pot:
                p.rendement_potentiel = pot
            for col in ("sol_type", "sol_ph", "sol_mo", "sol_fertilite", "sol_source", "sol_note"):
                v = _premier(props, [col])
                if v is not None:
                    setattr(p, col, _f(v) if col in ("sol_ph", "sol_mo") else str(v)[:200])
            ind = props.get("indices")
            if isinstance(ind, list) and ind:
                deja = {iv.date: iv for iv in db.session.query(_IV).filter_by(parcelle_id=p.id)}
                for x in ind:
                    try:
                        d = _dtm.strptime(str(x.get("date"))[:10], "%Y-%m-%d").date()
                    except (TypeError, ValueError, AttributeError):
                        continue
                    iv = deja.get(d)
                    if iv is None:
                        iv = _IV(parcelle_id=p.id, date=d)
                        db.session.add(iv)
                        deja[d] = iv
                    for k in ("ndvi", "ndwi", "ndmi", "savi", "evi"):
                        setattr(iv, k, _f(x.get(k)))
                    iv.source = x.get("source") or "GEE Sentinel-2"
                    n_indices += 1

        def _enquete(p, props, culture):
            nonlocal n_prat, n_rdt
            db.session.flush()
            touchees.add(p.id)
            _complements(p, props)
            if p.programme_id:
                progs_fichier.add(p.programme_id)
            ok_p, ok_r = _enq.enregistrer(p, props, _enq.annee_campagne(props, annee_active), culture)
            n_prat += ok_p
            n_rdt += ok_r

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

        def _historique(p, props, culture=None):
            """Historique cultural : colonnes « culture … AAAA » et culture de la campagne.
            Une seule ligne par année ; une nouvelle valeur remplace l'ancienne."""
            db.session.flush()
            deja = {h.annee: h for h in db.session.query(HistoriqueCultural).filter_by(parcelle_id=p.id)}
            valeurs = {}
            for cle, val in props.items():
                if val in (None, "") or "culture" not in cle.lower() or str(val).strip().lower() in ("none", "null"):
                    continue
                m = _ANNEE_RE.search(cle)
                if m:
                    valeurs[int(m.group(1))] = str(val).strip()
            if culture:
                valeurs.setdefault(_enq.annee_campagne(props, annee_active), str(culture).strip())
            for annee, val in valeurs.items():
                if annee in deja:
                    if val and deja[annee].culture != val[:80]:
                        deja[annee].culture = val[:80]
                    continue
                lib = f"Hivernage {annee}"
                camp = camp_cache.get(lib)
                if not camp:
                    camp = (Campagne.query.filter_by(libelle=lib).first()
                            or Campagne(libelle=lib, annee=annee, active=False))
                    db.session.add(camp)
                    camp_cache[lib] = camp
                db.session.add(HistoriqueCultural(parcelle_id=p.id, campagne=camp,
                                                  annee=annee, culture=val[:80], source="GeoJSON"))

        cache_filieres = {}
        n_new = n_upd = n_redraw = n_dup = n_skip = n_dup_fichier = 0
        nouvelles = []   # parcelles créées par cet import : (géométrie, producteur normalisé, parcelle)
        cibles_vues = {}      # parcelle existante -> producteur de l'entité du fichier qui l'a déjà mise à jour
        cibles_geom = {}      # parcelle existante -> géométrie de l'entité du fichier qui l'a mise à jour

        def _meme_trace(g1, g2):
            """Deux tracés presque identiques (recouvrement ≥ seuil de la plus grande surface)."""
            try:
                return g1.intersection(g2).area / max(g1.area, g2.area) >= seuil
            except Exception:
                return False

        def _doublon_fichier(g, nom_prod):
            """Parcelle déjà créée par ce même fichier : même forme, ou même producteur et deux
            tracés presque identiques (parcelle saisie deux fois). Une petite parcelle tracée à
            l'intérieur d'une grande du même producteur reste une parcelle distincte."""
            np_ = _enq.norm(nom_prod or "")
            for (g2, np2, p2) in nouvelles:
                if not g.intersects(g2):
                    continue
                if g.equals_exact(g2, 1e-9):
                    return p2
                try:
                    r = g.intersection(g2).area / max(g.area, g2.area)
                except Exception:
                    continue
                if np_ and np_ == np2 and r >= seuil:
                    return p2
            return None

        # Parcelles existantes reconnues sans ambiguïté (même identifiant ou même tracé exact) :
        # réservées à leur entité, pour qu'une entité voisine ne les prenne pas par recouvrement.
        reservees = {}
        for i_feat, feat in enumerate(features):
            try:
                g0 = shape(feat.get("geometry"))
                if transformer:
                    g0 = shp_transform(transformer, g0)
                if g0.geom_type == "Polygon":
                    g0 = MultiPolygon([g0])
                sid0 = _enq.identifiant_source(feat.get("properties") or {})
                pid0 = sources_exist.get(sid0) if sid0 else None
                if pid0 is None:
                    pid0 = sigs_existantes.get(_sig(g0))
                if pid0 is not None:
                    reservees.setdefault(pid0, i_feat)
            except Exception:
                continue

        def _libre(pid, i_feat):
            return reservees.get(pid, i_feat) == i_feat and pid not in cibles_vues

        for i_feat, feat in enumerate(features):
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
                if g.is_empty:
                    raise ValueError("géométrie vide")
            except Exception:
                n_skip += 1
                continue

            # --- Attributs (mappage tolérant, y compris noms tronqués QGIS) ---
            code = _premier(props, ["id_parcelle", "code", "nom", "Nom"])
            sig = _sig(g)
            if not code:
                code = f"P-{sig[:8]}"
            code = str(code)[:50]
            # Superficie = aire réelle du polygone. La superficie déclarée du fichier (souvent arrondie,
            # parfois en m²) est conservée à part dans les informations d'enquête.
            surface = (_ha_depuis_wgs84(g)
                       or _f(_premier(props, ["superficie_ha", "superficie_declaree_ha", "surface_ha", "surface", "Superficie",
                                              "Superifie", "superficie", "Surface (ha)", "Surface"])))
            culture = _premier(props, ["culture", "Culture actuelle", "culture_campagne"])
            if not culture:
                an_cult, culture = _culture_annee(props)
                # culture non renseignée pour la campagne en cours : culture par défaut du programme ;
                # jamais la culture d'une année passée (c'est un précédent, pas la culture actuelle)
                if an_cult is None or an_cult < _enq.annee_campagne(props, annee_active):
                    culture = culture_defaut
            culture = culture or culture_defaut or _premier(props, ["Culture hi", "Culture _1"])
            dep = _premier(props, ["Departement", "Département", "Departemen", "departement"])
            com = _premier(props, ["Commune", "commune"])
            vil = _premier(props, ["Village", "village", "Localité", "Localite"])
            reg = _premier(props, ["Region", "Région", "region"])
            observations = _premier(props, ["observations", "Observations"])
            nom_prod = _premier(props, [
                "producteur", "Prenom et nom producteur", "Prenom_nom_producteur", "Nom & Prénom du producteur",
                "Prénom et nom producteur", "Nom et prenom producteur",
                "Prenom_nom", "producteur", "Producteur"])
            tel_prod = _premier(props, [
                "telephone_producteur", "Telephone producteur", "Telephone_producteur", "Telephone du producteur", "Numero de telephone Producteur",
                "Téléphone producteur", "Telephone_", "telephone"])
            nom_coop = _premier(props, [
                "op_cooperative", "cooperative_norm", "Nom cooperative", "Nom_cooperative", "Nom cooper",
                "Coopérative", "Cooperative", "cooperative"])
            animateur = _premier(props, [
                "relais", "Nom & Prénom de l'agent", "Nom & Prenom de l'agent",
                "Prenom_nom_relais", "Nom enquêteur", "Nom enqueteur",
                "Prenom et nom enqueteur", "Prénom et nom enquêteur",
                "Nom animateur", "animateur", "Animateur", "agent", "Agent",
                "enqueteur", "Enquêteur"])
            animateur = re.sub(r"\s+", " ", str(animateur)).strip()[:120] if animateur else None
            if nom_prod:
                nom_prod = re.sub(r"\s+", " ", str(nom_prod)).strip()
            if tel_prod:
                tel_prod = re.sub(r"\D", "", str(tel_prod))[-9:] or None
                if tel_prod and len(tel_prod) < 9:
                    tel_prod = None                     # « 77 », « 0 » : numéro incomplet
            sexe = _sexe(props)
            prog_ent = _programme_entite(props)
            fil_nom = _premier(props, ["filiere", "Filière"])

            # --- Détermination de la cible ---
            # 1) entité déjà importée (même couche + même fid) : correspondance certaine
            src_id = _enq.identifiant_source(props)
            pid_src = sources_exist.get(src_id) if src_id else None
            cible = pid_src if pid_src is not None else sigs_existantes.get(sig)   # 2) même forme
            is_redraw = False
            if pid_src is not None and geom_par_pid.get(pid_src) is not None:
                is_redraw = not g.equals_exact(geom_par_pid[pid_src], 1e-6)
            # 3) recouvrement géométrique
            if cible is None and mode == "maj" and tree is not None and g.area:
                best_pid, best_r, best_cle = None, 0.0, None
                for idx in tree.query(g):
                    if not _libre(existants[int(idx)][0], i_feat):
                        continue
                    cand = geoms_exist[int(idx)]
                    try:
                        r = g.intersection(cand).area / g.area
                        # à recouvrement égal, on préfère la parcelle de taille la plus proche
                        cle_tri = (round(r, 3), -abs(cand.area / g.area - 1))
                    except Exception:
                        continue
                    if best_cle is None or cle_tri > best_cle:
                        best_r, best_pid, best_cle = r, existants[int(idx)][0], cle_tri
                if best_pid is not None and best_r >= seuil:
                    cible, is_redraw = best_pid, True
                elif nom_prod:
                    # même producteur et forte inclusion (une forme contient l'autre)
                    np_ = _enq_norm0(nom_prod)
                    for idx in tree.query(g):
                        pid_c, cand = existants[int(idx)]
                        if prod_exist.get(pid_c) != np_ or not _libre(pid_c, i_feat):
                            continue
                        try:
                            r = g.intersection(cand).area / min(g.area, cand.area)
                        except Exception:
                            continue
                        if r >= seuil:
                            cible, is_redraw = pid_c, not g.equals_exact(cand, 1e-9)
                            break
            # parcelle existante déjà prise par une entité du fichier : même producteur et même
            # tracé → saisie en double (fusion) ; sinon l'entité sera une nouvelle parcelle
            if cible is None and mode == "maj" and tree is not None and nom_prod:
                np_f = _enq.norm(nom_prod)
                for idx in tree.query(g):
                    pid_c = existants[int(idx)][0]
                    if cibles_vues.get(pid_c) == np_f and _meme_trace(g, cibles_geom.get(pid_c, geoms_exist[int(idx)])):
                        cible = pid_c
                        break

            try:
                with db.session.begin_nested():
                    region_obj = regions_par_nom.get(_enq.norm(reg)) if reg else None

                    np_feat = _enq.norm(nom_prod or "")
                    if pid_src is not None and cible in cibles_vues:
                        # entité déjà fusionnée dans cette parcelle lors d'un import précédent
                        doublon = db.session.get(Parcelle, cible)
                        cible = -1
                    elif cible is not None and mode == "maj" and cible in cibles_vues:
                        g_cible = geom_par_pid.get(cible)
                        g_prem = cibles_geom.get(cible)
                        if (cibles_vues[cible] == np_feat and (g_prem is None or _meme_trace(g, g_prem))) or (
                                g_cible is not None and g.equals_exact(g_cible, 1e-6)):
                            # même producteur, même parcelle saisie deux fois : on ne redessine pas deux fois
                            doublon = db.session.get(Parcelle, cible)
                            cible = -1
                        else:
                            # autre producteur, ou même producteur avec un autre tracé : parcelle
                            # distincte (ex. une ancienne grande parcelle redécoupée). On cherche sa propre
                            # parcelle existante (même producteur), sinon on la crée.
                            cible = None
                            for idx in tree.query(g) if tree is not None else []:
                                pid_c, cand = existants[int(idx)]
                                if pid_c in cibles_vues or prod_exist.get(pid_c) != np_feat:
                                    continue
                                try:
                                    r = g.intersection(cand).area / min(g.area, cand.area)
                                except Exception:
                                    continue
                                if r >= seuil:
                                    cible, is_redraw = pid_c, not g.equals_exact(cand, 1e-9)
                                    cibles_vues[cible] = np_feat
                                    cibles_geom[cible] = g
                                    break
                    if cible is None:
                        doublon = _doublon_fichier(g, nom_prod)
                    elif cible != -1:
                        doublon = None
                        cibles_vues[cible] = np_feat
                        cibles_geom.setdefault(cible, g)
                    if doublon is not None:
                        # ----- même parcelle saisie deux fois dans le fichier -----
                        _historique(doublon, props, culture or doublon.culture)
                        _enquete(doublon, props, culture or doublon.culture)
                        n_dup_fichier += 1

                    elif cible is None:
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
                        fil = (_enq.filiere_nommee(fil_nom, cache_filieres) if fil_nom
                               else _enq.filiere_pour(culture, cache_filieres))
                        if fil:
                            p.filiere_id = fil.id
                        if prog_ent:
                            p.programme_id = prog_ent
                        if camp_active:
                            p.campagne_id = camp_active.id
                        if prod:
                            p.producteur = prod
                        db.session.add(p)
                        _historique(p, props, culture)
                        _enquete(p, props, culture)
                        nouvelles.append((g, _enq.norm(nom_prod or ""), p))
                        codes_existants.add(code)
                        n_new += 1

                    elif mode == "additif":
                        # ----- doublon (forme identique) : on complète seulement -----
                        p = db.session.get(Parcelle, cible)
                        if p:
                            # parcelle déjà connue : on complète ce qui manque, sans rien écraser
                            if animateur and not p.animateur:
                                p.animateur = animateur
                            if prog_ent and not p.programme_id:
                                p.programme_id = prog_ent
                            if not p.producteur_id and nom_prod:
                                prod, org = _prod_coop(nom_coop, nom_prod, tel_prod, sexe, region_obj)
                                if prod:
                                    p.producteur = prod
                            if culture and not p.culture:
                                p.culture = culture
                            if not p.surface_ha and surface:
                                p.surface_ha = surface
                            if not p.filiere_id:
                                fil = (_enq.filiere_nommee(fil_nom, cache_filieres) if fil_nom
                                       else _enq.filiere_pour(p.culture, cache_filieres))
                                if fil:
                                    p.filiere_id = fil.id
                            _historique(p, props, culture or p.culture)
                            _enquete(p, props, culture or p.culture)
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
                            fil = (_enq.filiere_nommee(fil_nom, cache_filieres) if fil_nom
                                   else _enq.filiere_pour(culture, cache_filieres))
                            if fil:
                                p.filiere_id = fil.id
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
                            if prog_ent:
                                p.programme_id = prog_ent
                            if animateur:
                                p.animateur = animateur
                            if prod:
                                p.producteur = prod
                            _historique(p, props, culture or p.culture)
                            _enquete(p, props, culture or p.culture)
                            if is_redraw:
                                n_redraw += 1
                            else:
                                n_upd += 1
            except Exception:
                n_skip += 1

        # Synchronisation : parcelles du ou des programmes du fichier qui n'y figurent plus
        n_suppr = n_gardees = 0
        if request.form.get("supprimer_absentes") and mode == "maj" and touchees and progs_fichier:
            db.session.flush()
            absentes = [pid for (pid,) in db.session.query(Parcelle.id).filter(
                Parcelle.programme_id.in_(progs_fichier), ~Parcelle.id.in_(touchees))]
            if absentes:
                avec_suivi = {pid for (pid,) in db.session.execute(text(
                    "SELECT DISTINCT parcelle_id FROM suivis_terrain WHERE parcelle_id = ANY(:ids)"), {"ids": absentes})}
                a_supprimer = [pid for pid in absentes if pid not in avec_suivi]
                n_gardees = len(avec_suivi)
                if a_supprimer:
                    db.session.execute(text("DELETE FROM parcelles WHERE id = ANY(:ids)"), {"ids": a_supprimer})
                n_suppr = len(a_supprimer)
        msg_sync = ""
        if request.form.get("supprimer_absentes") and mode == "maj":
            msg_sync = (f" Synchronisation : {n_suppr} parcelle(s) du programme absente(s) du fichier "
                        + ("à supprimer" if apercu else "supprimée(s)")
                        + (f", {n_gardees} conservée(s) car elles ont des visites de terrain" if n_gardees else "") + ".")

        if apercu:
            db.session.rollback()
            flash(f"APERÇU (aucune écriture) — mode « {mode} » : "
                  f"{n_new} à créer, {n_upd} attribut(s) à mettre à jour, "
                  f"{n_redraw} redessinée(s) détectée(s), {n_dup} inchangée(s) (doublon), "
                  f"{n_skip} en erreur, {n_dup_fichier} doublon(s) dans le fichier ; "
                  f"{n_prat} fiche(s) de pratiques, {n_rdt} rendement(s) mesuré(s).{msg_sync} Décochez « aperçu » pour appliquer.", "info")
            return redirect(url_for("parcelles.importer"))

        db.session.commit()

        # Rendement potentiel des nouvelles parcelles : valeur la plus fréquente du même
        # programme et de la même culture (sinon la valeur par défaut du module Rendement).
        n_pot = 0
        try:
            from app.referentiel.service import appliquer_potentiels
            sans_pot = [pid_ for (pid_,) in db.session.query(Parcelle.id)
                        .filter(Parcelle.rendement_potentiel.is_(None))]
            if sans_pot:
                s_pot = appliquer_potentiels(sans_pot)
                n_pot += s_pot["variete"] + s_pot["speculation"]
        except Exception:
            db.session.rollback()     # référentiel pas encore installé
        if programme_id:
            from sqlalchemy import text as _text
            n_pot += db.session.execute(_text("""
                UPDATE parcelles p SET rendement_potentiel = m.pot
                  FROM (SELECT culture, mode() WITHIN GROUP (ORDER BY rendement_potentiel) AS pot
                          FROM parcelles WHERE programme_id = :pg AND rendement_potentiel IS NOT NULL
                         GROUP BY culture) m
                 WHERE p.programme_id = :pg AND p.culture = m.culture AND p.rendement_potentiel IS NULL
            """), {"pg": programme_id}).rowcount
            db.session.commit()

        from flask import current_app
        msg_indices = ""
        if (n_new or n_redraw) and current_app.config.get("GEE_PROJECT") and not current_app.testing:
            try:
                from app.teledetection import series as _series
                if _series.lancer_en_arriere_plan():
                    msg_indices = (" Indices de végétation depuis le 15 mai en cours de calcul en arrière-plan "
                                   "pour les nouvelles parcelles (Télédétection → Calcul des indices).")
            except Exception:
                pass
        flash(f"Import « {mode} » : {n_new} créée(s), {n_upd} mise(s) à jour, "
              f"{n_redraw} redessinée(s), {n_dup} inchangée(s), {n_skip} en erreur, "
              f"{n_dup_fichier} doublon(s) dans le fichier fusionné(s) ; "
              f"{n_prat} fiche(s) de pratiques, {n_rdt} rendement(s) mesuré(s) ajoutés aux références."
              + (f" Programme « {prog_nom} » appliqué." if prog_nom else "")
              + (f" Rendement potentiel attribué à {n_pot} nouvelle(s) parcelle(s)." if n_pot else "")
              + (f" {n_indices} valeur(s) d'indices de végétation reprises du fichier." if n_indices else "")
              + msg_sync + msg_indices, "success")
        return redirect(url_for("parcelles.carte"))

    programmes = Programme.query.order_by(Programme.nom).all()
    from app.parcelles.modele import CHAMPS
    return render_template("parcelles/import.html", programmes=programmes, champs_modele=CHAMPS)


@parcelles_bp.route("/export/complet.geojson")
@login_required
def export_complet():
    """Toutes les parcelles du périmètre, avec toutes leurs données, au format du modèle ANCAR :
    le fichier se réimporte tel quel (mode « Mise à jour ») sur une autre plateforme."""
    from flask import Response
    from app.parcelles.export_complet import geojson_complet
    from app.scoping import parcelle_ids
    if current_user.niveau < 3:
        flash("Export réservé à l'encadrement.", "warning")
        return redirect(url_for("parcelles.liste"))
    avec_indices = request.args.get("indices", "1") != "0"
    data = geojson_complet(pids=parcelle_ids(current_user), avec_indices=avec_indices,
                           programme_id=request.args.get("programme", type=int))
    from datetime import date as _d
    nom = f"parcelles_ANCAR_complet_{_d.today():%Y%m%d}.geojson"
    return Response(json.dumps(data, ensure_ascii=False, default=str), mimetype="application/geo+json",
                    headers={"Content-Disposition": f"attachment; filename={nom}"})


@parcelles_bp.route("/import/modele.geojson")
@login_required
def modele_geojson():
    """Modèle GeoJSON des parcelles : tous les champs reconnus, deux parcelles d'exemple."""
    from flask import Response
    from app.parcelles.modele import geojson_modele
    return Response(json.dumps(geojson_modele(), ensure_ascii=False, indent=1),
                    mimetype="application/geo+json",
                    headers={"Content-Disposition": "attachment; filename=modele_parcelles_ANCAR.geojson"})


_ANNEE_RE = re.compile(r"(20\d{2})")


@parcelles_bp.app_context_processor
def _ctx_enquete():
    """Données d'enquête pour la fiche parcelle. Si les tables n'existent pas encore
    (init_enquete.py pas lancé), la fiche s'affiche sans ce bloc au lieu de planter."""
    def _sur(requete):
        try:
            with db.session.begin_nested():
                return requete()
        except Exception:
            return []

    def enquete_attributs(pid):
        from app.models.enquete import AttributParcelle
        return _sur(lambda: db.session.query(AttributParcelle).filter_by(parcelle_id=pid)
                    .order_by(AttributParcelle.cle).all())

    def enquete_pratiques(pid):
        from app.models.enquete import PratiqueCampagne
        return _sur(lambda: db.session.query(PratiqueCampagne).filter_by(parcelle_id=pid)
                    .order_by(PratiqueCampagne.annee.desc()).all())
    return {"enquete_attributs": enquete_attributs, "enquete_pratiques": enquete_pratiques}


def _enq_norm0(s):
    from app.parcelles.enquete import norm
    return norm(s)


def _culture_annee(props):
    """(année, culture) la plus récente parmi les propriétés 'culture ... AAAA' renseignées."""
    best_year, best = None, None
    for k, v in props.items():
        if v and "culture" in k.lower() and str(v).strip().lower() not in ("none", "null"):
            m = _ANNEE_RE.search(k)
            if m and (best_year is None or int(m.group(1)) > best_year):
                best_year, best = int(m.group(1)), str(v).strip()
    return best_year, best


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
    v = _premier(props, ["sexe_producteur", "Sexe", "Sexe_ producteur", "Genre (Homme/Femme)", "genre"])
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


@parcelles_bp.route("/localisation-villages", methods=["GET", "POST"])
@login_required
@niveau_minimum(4)
def localisation_villages():
    """Renseigne commune, département, région et organisation par village, pour un programme
    dont le fichier ne contient que le village (ex. AMPK maraîchage)."""
    from sqlalchemy import func as _func
    from app.parcelles import enquete as _enq
    from app.models.programme import Programme

    programmes = Programme.query.order_by(Programme.nom).all()
    prog_id = request.values.get("programme", type=int)
    if prog_id is None and programmes:
        # par défaut : le programme qui a le plus de parcelles sans commune
        row = (db.session.query(Parcelle.programme_id, _func.count())
               .filter(Parcelle.programme_id.isnot(None), Parcelle.commune.is_(None))
               .group_by(Parcelle.programme_id).order_by(_func.count().desc()).first())
        prog_id = row[0] if row else programmes[0].id
    regions = Region.query.order_by(Region.nom).all()
    reg_par_id = {r.id: r for r in regions}

    if request.method == "POST":
        seulement_vides = request.form.get("seulement_vides") == "on"
        n_parc = n_prod = 0
        orgs = {_enq.norm(o.nom): o for o in OrganisationProducteur.query.all()}
        for i in range(int(request.form.get("n", 0))):
            village = request.form.get(f"village_{i}")
            commune = (request.form.get(f"commune_{i}") or "").strip() or None
            dep = (request.form.get(f"departement_{i}") or "").strip() or None
            reg_id = request.form.get(f"region_{i}", type=int)
            org_nom = (request.form.get(f"organisation_{i}") or "").strip() or None
            if not any([commune, dep, reg_id, org_nom]):
                continue
            q = Parcelle.query.filter(Parcelle.programme_id == prog_id)
            q = q.filter(Parcelle.village.is_(None)) if village == "" else q.filter(Parcelle.village == village)
            if seulement_vides:
                q = q.filter(Parcelle.commune.is_(None))
            org = None
            if org_nom:
                org = orgs.get(_enq.norm(org_nom))
                if org is None:
                    org = OrganisationProducteur(nom=org_nom[:150], type="Organisation", region_id=reg_id)
                    db.session.add(org)
                    db.session.flush()
                    orgs[_enq.norm(org_nom)] = org
            for p in q.all():
                if commune:
                    p.commune = commune
                if dep:
                    p.departement = dep
                if reg_id:
                    p.region_id = reg_id
                n_parc += 1
                prod = p.producteur
                if prod is not None:
                    if org is not None and prod.organisation_id is None:
                        prod.organisation_id = org.id
                        n_prod += 1
                    if reg_id and getattr(prod, "region_id", None) is None:
                        prod.region_id = reg_id
        db.session.commit()
        flash(f"Localisation appliquée à {n_parc} parcelle(s) ; {n_prod} producteur(s) rattaché(s) "
              f"à une organisation. Pensez à relancer le rattachement Sol et la grille Climat si besoin.",
              "success")
        return redirect(url_for("parcelles.localisation_villages", programme=prog_id))

    # Villages du programme, avec la localisation déjà connue ailleurs pour un village du même nom
    villages = (db.session.query(_func.coalesce(Parcelle.village, ""), _func.count(),
                                 _func.count(Parcelle.commune))
                .filter(Parcelle.programme_id == prog_id)
                .group_by(_func.coalesce(Parcelle.village, ""))
                .order_by(_func.count().desc()).all()) if prog_id else []
    connus = {}
    for vil, com, dep, rid in (db.session.query(Parcelle.village, Parcelle.commune,
                                                Parcelle.departement, Parcelle.region_id)
                               .filter(Parcelle.village.isnot(None), Parcelle.commune.isnot(None))
                               .distinct().all()):
        connus.setdefault(_enq.norm(vil), (com, dep, rid))
    lignes = []
    for vil, n, n_loc in villages:
        com, dep, rid = connus.get(_enq.norm(vil), (None, None, None)) if vil else (None, None, None)
        org = (db.session.query(OrganisationProducteur.nom)
               .join(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
               .join(Parcelle, Parcelle.producteur_id == Producteur.id)
               .filter(Parcelle.programme_id == prog_id,
                       (Parcelle.village == vil) if vil else Parcelle.village.is_(None))
               .group_by(OrganisationProducteur.nom).order_by(_func.count().desc()).first())
        lignes.append({"village": vil, "n": n, "n_loc": n_loc, "commune": com, "departement": dep,
                       "region_id": rid, "organisation": org[0] if org else None})
    return render_template("parcelles/localisation_villages.html", programmes=programmes,
                           prog_id=prog_id, regions=regions, lignes=lignes,
                           organisations=[o.nom for o in OrganisationProducteur.query
                                          .order_by(OrganisationProducteur.nom).all()])


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
