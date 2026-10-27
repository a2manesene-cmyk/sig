import json

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.indice import IndiceVegetation
from app.models.rendement import RendementReference
from app.auth.decorators import niveau_minimum
from app.scoping import parcelle_ids, peut_voir_parcelle
from .calcul import (
    ndvi_max_par_parcelle, indice_potentiel, estimation, modeles, MIN_POINTS
)
from . import ia as IA

rendement_bp = Blueprint("rendement", __name__, url_prefix="/rendement")


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@rendement_bp.route("/")
@login_required
def liste():
    culture = request.args.get("culture") or None
    page = _i(request.args.get("page")) or 1

    pids = parcelle_ids(current_user)
    nvm = ndvi_max_par_parcelle(pids)
    mdl = modeles()
    ids = list(nvm.keys())

    q = Parcelle.query.filter(Parcelle.id.in_(ids)) if ids else Parcelle.query.filter(db.false())
    if culture:
        q = q.filter(Parcelle.culture == culture)
    q = q.order_by(Parcelle.code)
    pagination = q.paginate(page=page, per_page=25, error_out=False)

    vues = []
    for p in pagination.items:
        m = nvm.get(p.id, {})
        vmax = m.get("ndvi_max")
        vues.append({
            "p": p, "ndvi_max": vmax, "n": m.get("n"),
            "indice": indice_potentiel(vmax),
            "estimation": estimation(p.culture, vmax, mdl),
        })

    cultures = [c[0] for c in (
        db.session.query(Parcelle.culture).filter(Parcelle.id.in_(ids))
        .filter(Parcelle.culture.isnot(None)).distinct().order_by(Parcelle.culture).all())] if ids else []

    return render_template(
        "rendement/liste.html",
        vues=vues, pagination=pagination, culture=culture, cultures=cultures,
        modeles=mdl, min_points=MIN_POINTS, total=len(ids),
    )


@rendement_bp.route("/carte")
@login_required
def carte():
    return render_template("rendement/carte.html")


@rendement_bp.route("/geojson")
@login_required
def geojson():
    pids = parcelle_ids(current_user)
    nvm = ndvi_max_par_parcelle(pids)
    mdl = modeles()
    ids = list(nvm.keys())
    if not ids:
        return jsonify({"type": "FeatureCollection", "features": []})

    rows = (db.session.query(
                Parcelle.id, Parcelle.code, Parcelle.culture,
                func.ST_AsGeoJSON(Parcelle.geom))
            .filter(Parcelle.id.in_(ids), Parcelle.geom.isnot(None)).all())
    features = []
    for pid, code, culture, gj in rows:
        if not gj:
            continue
        vmax = nvm.get(pid, {}).get("ndvi_max")
        features.append({
            "type": "Feature",
            "geometry": json.loads(gj),
            "properties": {
                "id": pid, "code": code, "culture": culture,
                "ndvi_max": round(vmax, 3) if vmax is not None else None,
                "indice": indice_potentiel(vmax),
                "estimation": estimation(culture, vmax, mdl),
            },
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@rendement_bp.route("/ia")
@login_required
def ia():
    st = IA.statut()
    mdl = IA.modeles()
    pids = parcelle_ids(current_user)
    preds = IA.predictions(pids)

    lignes = []
    if preds:
        codes = dict(db.session.query(Parcelle.id, Parcelle.code)
                     .filter(Parcelle.id.in_(list(preds.keys()))).all())
        cults = dict(db.session.query(Parcelle.id, Parcelle.culture)
                     .filter(Parcelle.id.in_(list(preds.keys()))).all())
        for pid, val in sorted(preds.items(), key=lambda kv: -kv[1])[:60]:
            lignes.append({"id": pid, "code": codes.get(pid),
                           "culture": cults.get(pid), "estimation": val})
    return render_template("rendement/ia.html", statut=st, modeles=mdl,
                           lignes=lignes, nb_pred=len(preds))


@rendement_bp.route("/references", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def references():
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        rendement = _f(request.form.get("rendement"))
        annee = _i(request.form.get("annee"))
        p = Parcelle.query.filter_by(code=code).first()
        if not p:
            flash(f"Parcelle « {code} » introuvable.", "warning")
        elif not peut_voir_parcelle(current_user, p.id):
            flash("Cette parcelle est hors de votre périmètre.", "warning")
        elif rendement is None or rendement < 0:
            flash("Rendement invalide.", "warning")
        else:
            vmax = (db.session.query(func.max(IndiceVegetation.ndvi))
                    .filter(IndiceVegetation.parcelle_id == p.id).scalar())
            db.session.add(RendementReference(
                parcelle_id=p.id, annee=annee, culture=p.culture,
                rendement_kg_ha=rendement, ndvi_max=vmax))
            db.session.commit()
            note = "" if vmax is not None else " (aucun NDVI encore calculé pour cette parcelle : le point ne servira au modèle qu'après un calcul d'indices)"
            flash(f"Rendement de référence enregistré pour {code}{note}.", "success")
        return redirect(url_for("rendement.references"))

    refs = (RendementReference.query
            .order_by(RendementReference.date_saisie.desc()).limit(50).all())
    return render_template("rendement/references.html",
                           refs=refs, modeles=modeles(), min_points=MIN_POINTS)
