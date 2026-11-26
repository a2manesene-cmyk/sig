import json
from io import BytesIO

from flask import (
    Blueprint, render_template, request, jsonify, send_file
)
from flask_login import login_required, current_user
from sqlalchemy import func, and_
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.zone import Region
from app.models.indice import IndiceVegetation
from app.teledetection.interpretation import interpret
from app.scoping import parcelle_ids

alertes_bp = Blueprint("alertes", __name__, url_prefix="/alertes")

SEUIL_NDVI = 0.25
SEUIL_NDMI = 0.10


def _f(v, defaut):
    try:
        return float(v)
    except (TypeError, ValueError):
        return defaut


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dernier_indice_sub():
    return (
        db.session.query(
            IndiceVegetation.parcelle_id.label("pid"),
            func.max(IndiceVegetation.date).label("d"),
        )
        .group_by(IndiceVegetation.parcelle_id)
        .subquery()
    )


def _base_query(s_ndvi, s_ndmi, region_id):
    """Requête de base : parcelles en alerte (NDVI < seuil ET NDMI < seuil)."""
    sub = _dernier_indice_sub()
    q = (
        db.session.query(Parcelle, IndiceVegetation)
        .join(IndiceVegetation, IndiceVegetation.parcelle_id == Parcelle.id)
        .join(sub, and_(sub.c.pid == IndiceVegetation.parcelle_id,
                        sub.c.d == IndiceVegetation.date))
        .filter(
            IndiceVegetation.ndvi.isnot(None),
            IndiceVegetation.ndmi.isnot(None),
            IndiceVegetation.ndvi < s_ndvi,
            IndiceVegetation.ndmi < s_ndmi,
        )
    )
    if region_id:
        q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
               .filter(Producteur.region_id == region_id))
    from flask_login import current_user as _cu
    _pids = parcelle_ids(_cu)
    if _pids is not None:
        q = q.filter(Parcelle.id.in_(_pids))
    return q.order_by(IndiceVegetation.ndvi.asc())


def _params():
    return (
        _f(request.args.get("ndvi"), SEUIL_NDVI),
        _f(request.args.get("ndmi"), SEUIL_NDMI),
        _i(request.args.get("region")),
    )


@alertes_bp.route("/")
@login_required
def liste():
    s_ndvi, s_ndmi, region_id = _params()
    rows = _base_query(s_ndvi, s_ndmi, region_id).all()
    lignes = [{
        "p": parc, "iv": iv,
        "ndvi_i": interpret("ndvi", iv.ndvi),
        "ndmi_i": interpret("ndmi", iv.ndmi),
    } for parc, iv in rows]
    try:
        from app.notifications.service import apercu
        wa = apercu(current_user)
    except Exception:
        wa = {"producteurs": 0, "parcelles": 0}
    return render_template(
        "alertes/liste.html",
        lignes=lignes, s_ndvi=s_ndvi, s_ndmi=s_ndmi, region_id=region_id,
        regions=Region.query.order_by(Region.nom).all(),
        wa=wa,
    )


@alertes_bp.route("/whatsapp", methods=["POST"])
@login_required
def whatsapp():
    from flask import current_app, redirect, url_for, flash
    from app.auth.decorators import niveau_minimum  # cohérence de niveau
    if current_user.niveau < 2:
        flash("Action non autorisée.", "warning")
        return redirect(url_for("alertes.liste"))
    try:
        from app.notifications.service import envoyer
    except Exception:
        flash("Le module d'envoi WhatsApp n'est pas installé sur ce serveur.", "danger")
        return redirect(url_for("alertes.liste"))
    try:
        res = envoyer(current_user, current_app.static_folder)
        flash(f"Alertes WhatsApp : {res['envoyes']} envoyée(s), "
              f"{res['echecs']} échec(s).",
              "success" if res["envoyes"] else "warning")
    except Exception as exc:   # noqa
        flash(f"Échec de l'envoi WhatsApp : {exc}", "danger")
    from flask import redirect, url_for
    return redirect(url_for("alertes.liste"))


@alertes_bp.route("/geojson")
@login_required
def geojson():
    s_ndvi, s_ndmi, region_id = _params()
    sub = _dernier_indice_sub()
    q = (
        db.session.query(
            Parcelle.id, Parcelle.code,
            IndiceVegetation.ndvi, IndiceVegetation.ndmi, IndiceVegetation.date,
            func.ST_AsGeoJSON(Parcelle.geom),
        )
        .join(IndiceVegetation, IndiceVegetation.parcelle_id == Parcelle.id)
        .join(sub, and_(sub.c.pid == IndiceVegetation.parcelle_id,
                        sub.c.d == IndiceVegetation.date))
        .filter(
            Parcelle.geom.isnot(None),
            IndiceVegetation.ndvi < s_ndvi,
            IndiceVegetation.ndmi < s_ndmi,
        )
    )
    if region_id:
        q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
               .filter(Producteur.region_id == region_id))
    _pids = parcelle_ids(current_user)
    if _pids is not None:
        q = q.filter(Parcelle.id.in_(_pids))
    features = []
    for pid, code, ndvi, ndmi, dte, gj in q.all():
        if not gj:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(gj),
            "properties": {
                "id": pid, "code": code,
                "ndvi": round(ndvi, 3) if ndvi is not None else None,
                "ndmi": round(ndmi, 3) if ndmi is not None else None,
                "date": dte.isoformat() if dte else None,
            },
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@alertes_bp.route("/export.xlsx")
@login_required
def export_xlsx():
    s_ndvi, s_ndmi, region_id = _params()
    rows = _base_query(s_ndvi, s_ndmi, region_id).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Alertes stress hydrique"
    entetes = ["Code", "Producteur", "Téléphone", "Région", "Culture",
               "NDVI", "NDMI", "Date"]
    ws.append(entetes)
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="A50026")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center")

    for parc, iv in rows:
        ws.append([
            parc.code or "",
            parc.producteur.nom_complet if parc.producteur else "",
            parc.producteur.telephone if parc.producteur else "",
            parc.producteur.region.nom if parc.producteur and parc.producteur.region else "",
            parc.culture or "",
            round(iv.ndvi, 3) if iv.ndvi is not None else "",
            round(iv.ndmi, 3) if iv.ndmi is not None else "",
            iv.date.isoformat() if iv.date else "",
        ])
    for i, w in enumerate([14, 26, 16, 12, 14, 10, 10, 12], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio, as_attachment=True, download_name="alertes_stress_hydrique.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# --------------------------------------------------------------------------
# Alertes basées sur l'impact : danger -> impact selon le stade -> action
# --------------------------------------------------------------------------
def _date_ref():
    from datetime import date, datetime
    v = request.args.get("au")
    try:
        return datetime.strptime(v, "%Y-%m-%d").date() if v else date.today()
    except ValueError:
        return date.today()


def _filtrer_impact(alertes):
    danger = request.args.get("danger") or None
    niveau = _i(request.args.get("niveau"))
    commune = (request.args.get("commune") or "").strip() or None
    out = [a for a in alertes
           if (not danger or a["danger"] == danger) and (not niveau or a["niveau"] >= niveau)
           and (not commune or (a["parcelle"].commune or "") == commune)]
    return out, danger, niveau, commune


@alertes_bp.route("/impact")
@login_required
def impact():
    from datetime import date, timedelta
    from app.alertes import impact as I
    au = _date_ref()
    alertes, ctx = I.evaluer(au, parcelle_ids(current_user))
    communes = sorted({a["parcelle"].commune for a in alertes if a["parcelle"].commune})
    filtrees, danger, niveau, commune = _filtrer_impact(alertes)
    # synthèse danger x niveau, et parcelles distinctes
    synth = {}
    for a in alertes:
        s = synth.setdefault(a["danger"], {"lib": a["danger_lib"], "icone": a["icone"], 1: 0, 2: 0, 3: 0})
        s[a["niveau"]] += 1
    par_niveau = {n: len({a["parcelle"].id for a in alertes if a["niveau"] == n}) for n in (1, 2, 3)}
    # raccourcis : dates de la campagne
    an = ctx["annee"]
    raccourcis = [date(an, m, j) for m, j in ((7, 15), (8, 1), (8, 15), (9, 1), (9, 15), (10, 1), (10, 15))]
    raccourcis = [d for d in raccourcis if d <= date.today()]
    return render_template("alertes/impact.html", alertes=filtrees[:400], total=len(filtrees), ctx=ctx,
                           synth=synth, par_niveau=par_niveau, niveaux=I.NIVEAUX, dangers=I.DANGERS,
                           communes=communes, danger=danger, niveau=niveau, commune=commune,
                           raccourcis=raccourcis, au=au, n_parcelles=len({a["parcelle"].id for a in alertes}))


@alertes_bp.route("/impact.xlsx")
@login_required
def impact_xlsx():
    from app.alertes import impact as I
    au = _date_ref()
    alertes, ctx = I.evaluer(au, parcelle_ids(current_user))
    alertes, *_ = _filtrer_impact(alertes)
    wb = Workbook()
    ws = wb.active
    ws.title = f"Alertes {au:%d-%m-%Y}"
    entetes = ["Niveau", "Danger", "Constat", "Code parcelle", "Producteur", "Téléphone", "Village", "Commune",
               "Culture", "Stade", "Jours depuis semis", "Semis (origine)", "Impact", "Action conseillée", "Message SMS"]
    ws.append(entetes)
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="1C5D3F")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    couleurs = {1: "FFF3C4", 2: "FFE0B2", 3: "FFCDD2"}
    for a in alertes:
        p = a["parcelle"]
        ws.append([a["niveau_lib"], a["danger_lib"], a["constat"], p.code or f"P{p.id}",
                   p.producteur.nom_complet if p.producteur else "", p.producteur.telephone if p.producteur else "",
                   p.village or "", p.commune or "", a["culture"], a["stade_lib"], a["jours"],
                   f"{a['j0']:%d/%m/%Y} ({a['origine_j0']})", a["impact"], a["action"], I.message(a)])
        ws.cell(row=ws.max_row, column=1).fill = PatternFill("solid", fgColor=couleurs[a["niveau"]])
    for i, w in enumerate([12, 22, 34, 14, 24, 13, 16, 16, 10, 20, 10, 26, 48, 58, 80], start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    bio = BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"alertes_impact_{au:%Y-%m-%d}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
