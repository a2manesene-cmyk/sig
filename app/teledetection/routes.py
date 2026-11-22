import json
from datetime import date, datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify,
    current_app
)
from flask_login import login_required, current_user
from sqlalchemy import func, and_

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.zone import Region
from app.models.indice import IndiceVegetation
from app.auth.decorators import niveau_minimum
from .gee import compute_indices, compute_timeseries, difference_tiles, index_tiles
from .interpretation import INDICES_INFO, ORDRE
from . import series

teledetection_bp = Blueprint("teledetection", __name__, url_prefix="/teledetection")

INDICES = ["ndvi", "ndwi", "ndmi", "savi", "evi"]


@teledetection_bp.route("/carte")
@login_required
def carte():
    indice = request.args.get("indice", "ndvi")
    if indice not in INDICES:
        indice = "ndvi"
    return render_template(
        "teledetection/carte.html",
        indice=indice, indices=ORDRE, infos=INDICES_INFO,
        aujourdhui=date.today().isoformat(),
    )


@teledetection_bp.route("/rgb")
@login_required
def rgb():
    """Tuiles en vraies couleurs (variabilité parcellaire) pour la période demandée."""
    from .gee import rgb_tiles
    from app.scoping import parcelle_ids as _pi
    d1 = request.args.get("d1") or "2026-05-15"
    d2 = request.args.get("d2") or date.today().isoformat()
    region_id = request.args.get("region") or None

    q = (db.session.query(Parcelle.id, func.ST_AsGeoJSON(Parcelle.geom))
         .filter(Parcelle.geom.isnot(None)))
    if region_id:
        from app.models.producteur import Producteur
        q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
               .filter(Producteur.region_id == int(region_id)))
    _pids = _pi(current_user)
    if _pids is not None:
        q = q.filter(Parcelle.id.in_(_pids))
    features = [{"id": pid, "geometry": json.loads(gj)} for pid, gj in q.all() if gj]

    project = current_app.config.get("GEE_PROJECT")
    if not features:
        return jsonify({"error": "Aucune parcelle géolocalisée dans votre périmètre."}), 400
    if not project:
        return jsonify({"error": "GEE_PROJECT non défini dans config.py."}), 400
    try:
        url = rgb_tiles(features, d1, d2, project)
    except Exception as exc:   # noqa
        return jsonify({"error": str(exc)}), 500
    return jsonify({"tile_url": url})


@teledetection_bp.route("/geojson")
@login_required
def geojson():
    indice = request.args.get("indice", "ndvi")
    if indice not in INDICES:
        indice = "ndvi"
    col = getattr(IndiceVegetation, indice)

    # Dernière date disponible par parcelle
    sub = (
        db.session.query(
            IndiceVegetation.parcelle_id.label("pid"),
            func.max(IndiceVegetation.date).label("d"),
        )
        .group_by(IndiceVegetation.parcelle_id)
        .subquery()
    )
    rows = (
        db.session.query(
            Parcelle.id, Parcelle.code, col, IndiceVegetation.date,
            func.ST_AsGeoJSON(Parcelle.geom),
        )
        .join(IndiceVegetation, IndiceVegetation.parcelle_id == Parcelle.id)
        .join(sub, and_(sub.c.pid == IndiceVegetation.parcelle_id,
                        sub.c.d == IndiceVegetation.date))
        .filter(Parcelle.geom.isnot(None))
    )
    from app.scoping import parcelle_ids as _pi
    _pids = _pi(current_user)
    if _pids is not None:
        rows = rows.filter(Parcelle.id.in_(_pids))
    rows = rows.all()
    features = []
    for pid, code, valeur, dte, geom_json in rows:
        if geom_json is None or valeur is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(geom_json),
            "properties": {
                "id": pid, "code": code,
                "valeur": round(valeur, 3),
                "date": dte.isoformat() if dte else None,
            },
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@teledetection_bp.route("/raster")
@login_required
def raster():
    indice = request.args.get("indice", "ndvi")
    if indice not in INDICES:
        indice = "ndvi"
    d1 = request.args.get("d1")
    d2 = request.args.get("d2")
    region_id = request.args.get("region") or None
    if not d1 or not d2:
        return jsonify({"error": "Période requise (début et fin)."}), 400

    q = (
        db.session.query(Parcelle.id, func.ST_AsGeoJSON(Parcelle.geom))
        .filter(Parcelle.geom.isnot(None))
    )
    if region_id:
        from app.models.producteur import Producteur
        q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
               .filter(Producteur.region_id == int(region_id)))
    features = [{"id": pid, "geometry": json.loads(gj)} for pid, gj in q.all() if gj]

    project = current_app.config.get("GEE_PROJECT")
    if not features:
        return jsonify({"error": "Aucune parcelle géolocalisée."}), 400
    if not project:
        return jsonify({"error": "GEE_PROJECT non défini dans config.py."}), 400
    try:
        url = index_tiles(features, d1, d2, project, indice=indice)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"tile_url": url})


@teledetection_bp.route("/comparaison", methods=["GET", "POST"])
@login_required
def comparaison():
    contexte = {
        "regions": Region.query.order_by(Region.nom).all(),
        "aujourdhui": date.today().isoformat(),
        "tile_url": None,
    }
    if request.method == "POST":
        da1 = request.form.get("a_debut")
        da2 = request.form.get("a_fin")
        db1 = request.form.get("b_debut")
        db2 = request.form.get("b_fin")
        indice = request.form.get("indice", "ndvi")
        region_id = request.form.get("region") or None

        q = (
            db.session.query(Parcelle.id, func.ST_AsGeoJSON(Parcelle.geom))
            .filter(Parcelle.geom.isnot(None))
        )
        if region_id:
            from app.models.producteur import Producteur
            q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
                   .filter(Producteur.region_id == int(region_id)))
        parcelles = q.all()
        features = [{"id": pid, "geometry": json.loads(gj)} for pid, gj in parcelles if gj]

        project = current_app.config.get("GEE_PROJECT")
        if not features:
            flash("Aucune parcelle géolocalisée pour ce périmètre.", "warning")
        elif not project:
            flash("GEE_PROJECT n'est pas défini dans config.py.", "danger")
        else:
            try:
                contexte["tile_url"] = difference_tiles(
                    features, da1, da2, db1, db2, project, indice=indice
                )
                contexte.update(a=f"{da1} → {da2}", b=f"{db1} → {db2}",
                                indice=indice.upper())
            except Exception as exc:
                flash(f"Erreur Earth Engine : {exc}", "danger")

    return render_template("teledetection/comparaison.html", **contexte)


@teledetection_bp.route("/calculer", methods=["GET", "POST"])
@login_required
@niveau_minimum(3)
def calculer():
    if request.method == "POST":
        d1 = request.form.get("date_debut") or series.debut_campagne().isoformat()
        d2 = request.form.get("date_fin")
        region_id = request.form.get("region") or None
        serie = request.form.get("serie")   # "on" -> évolution à chaque passage
        try:
            date_fin = datetime.strptime(d2, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            date_fin = date.today()

        # Parcelles concernées (avec géométrie)
        q = (
            db.session.query(Parcelle.id, func.ST_AsGeoJSON(Parcelle.geom))
            .filter(Parcelle.geom.isnot(None))
        )
        if region_id:
            from app.models.producteur import Producteur
            q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
                   .filter(Producteur.region_id == int(region_id)))
        parcelles = q.all()
        features = [{"id": pid, "geometry": json.loads(gj)} for pid, gj in parcelles if gj]

        if not features:
            flash("Aucune parcelle géolocalisée pour ce périmètre.", "warning")
            return redirect(url_for("teledetection.calculer"))

        project = current_app.config.get("GEE_PROJECT")
        if not project:
            flash("GEE_PROJECT n'est pas défini dans config.py.", "danger")
            return redirect(url_for("teledetection.calculer"))

        # ── Mode ÉVOLUTION : un point par passage du satellite, en arrière-plan ──
        # (des milliers de parcelles sur plusieurs mois dépassent le délai d'une page web)
        if serie:
            try:
                d1d = datetime.strptime(d1, "%Y-%m-%d").date()
            except (TypeError, ValueError):
                d1d = series.debut_campagne()
            tout = request.form.get("recalculer") == "on"
            if series.lancer_en_arriere_plan(tout=tout, debut=d1d):
                flash("Calcul des indices à chaque passage du satellite lancé en arrière-plan "
                      f"(depuis le {d1d:%d/%m/%Y}). Vous pouvez quitter la page : l'avancement s'affiche ci-dessous.",
                      "success")
            else:
                flash("Un calcul est déjà en cours.", "warning")
            return redirect(url_for("teledetection.calculer"))

        # ── Mode SNAPSHOT : une valeur (médiane) à la date de fin ──
        try:
            resultats = compute_indices(features, d1, d2, project)
        except Exception as exc:
            flash(f"Erreur Earth Engine : {exc}", "danger")
            return redirect(url_for("teledetection.calculer"))

        # Upsert des indices pour la date de fin
        existants = {
            iv.parcelle_id: iv
            for iv in IndiceVegetation.query.filter_by(date=date_fin).all()
        }
        n = 0
        for r in resultats:
            pid = r.get("pid")
            if pid is None:
                continue
            iv = existants.get(pid)
            if not iv:
                iv = IndiceVegetation(parcelle_id=pid, date=date_fin)
                db.session.add(iv)
                existants[pid] = iv
            iv.ndvi = r.get("ndvi")
            iv.ndwi = r.get("ndwi")
            iv.ndmi = r.get("ndmi")
            iv.savi = r.get("savi")
            iv.evi = r.get("evi")
            iv.source = series.SOURCE_INSTANTANE
            n += 1
        db.session.commit()
        flash(f"Calcul terminé : {n} parcelle(s) mises à jour pour le {date_fin} (composite de la période, "
              "pas une série par passage).", "success")
        return redirect(url_for("teledetection.carte"))

    calculs = (
        db.session.query(IndiceVegetation.date, func.count(IndiceVegetation.id))
        .group_by(IndiceVegetation.date)
        .order_by(IndiceVegetation.date.desc())
        .all()
    )
    st = series.statut()
    journal = series.lire_journal()
    return render_template(
        "teledetection/calculer.html",
        regions=Region.query.order_by(Region.nom).all(),
        aujourdhui=date.today().isoformat(),
        calculs=calculs[:40],
        statut=st, journal=journal, couverture=series.couverture(),
        debut_campagne=series.debut_campagne().isoformat(),
    )
