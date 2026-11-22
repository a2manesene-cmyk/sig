import json

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
)
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.auth.decorators import niveau_minimum
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur
from app.models.organisation import OrganisationProducteur
from app.models.sol import AnalyseSol, PointSolIFDC, HistoriqueSol
from app.scoping import parcelle_ids, peut_voir_parcelle
from app.sol import referentiel as R
from app.sol import service

sol_bp = Blueprint("sol", __name__, url_prefix="/sol")

NIVEAU_SAISIE = 2    # CAR, CDCAR, animateur, conseiller et au-dessus (dans leur périmètre)
NIVEAU_IMPORT = 4    # DG, ADMIN


@sol_bp.app_context_processor
def _ctx_sol():
    def sol_analyse(pid):
        """Fiche sol de la parcelle (None si les tables du module ne sont pas encore créées)."""
        try:
            with db.session.begin_nested():
                return db.session.query(AnalyseSol).filter_by(parcelle_id=pid).first()
        except Exception:
            return None
    return {"SOL_REF": R, "sol_analyse": sol_analyse}


def _peut_saisir(pid):
    return current_user.niveau >= NIVEAU_SAISIE and peut_voir_parcelle(current_user, pid)


def _requete():
    q = (db.session.query(Parcelle, AnalyseSol, Producteur, OrganisationProducteur)
         .outerjoin(AnalyseSol, AnalyseSol.parcelle_id == Parcelle.id)
         .outerjoin(Producteur, Producteur.id == Parcelle.producteur_id)
         .outerjoin(OrganisationProducteur, OrganisationProducteur.id == Producteur.organisation_id))
    ids = parcelle_ids(current_user)
    if ids is not None:
        q = q.filter(Parcelle.id.in_(ids))
    return q


def _filtrer(q):
    op = request.args.get("op", type=int)
    commune = request.args.get("commune") or ""
    fiab = request.args.get("fiabilite") or ""
    fert = request.args.get("fertilite") or ""
    origine = request.args.get("origine") or ""
    texte = (request.args.get("q") or "").strip()
    if op:
        q = q.filter(Producteur.organisation_id == op)
    if commune:
        q = q.filter(func.lower(Parcelle.commune) == commune.lower())
    if fiab == "aucune":
        q = q.filter(AnalyseSol.id.is_(None))
    elif fiab:
        q = q.filter(AnalyseSol.fiabilite == fiab)
    if fert == "aucune":
        q = q.filter(AnalyseSol.fertilite.is_(None))
    elif fert:
        q = q.filter(AnalyseSol.fertilite == fert)
    if origine:
        q = q.filter(AnalyseSol.origine == origine)
    if texte:
        like = f"%{texte.lower()}%"
        q = q.filter(func.lower(Parcelle.code).like(like)
                     | func.lower(Producteur.nom).like(like)
                     | func.lower(Producteur.prenom).like(like)
                     | func.lower(Parcelle.village).like(like))
    return q


# --------------------------------------------------------------------------
@sol_bp.route("/")
@login_required
def liste():
    base = _requete()
    q = _filtrer(base)
    total = q.count()
    page = max(request.args.get("page", 1, type=int), 1)
    par_page = 50
    lignes = (q.order_by(OrganisationProducteur.nom, Parcelle.code)
               .offset((page - 1) * par_page).limit(par_page).all())

    # Indicateurs du périmètre (sans filtres)
    sub = base.with_entities(Parcelle.id).subquery()
    stats_q = (db.session.query(AnalyseSol)
               .filter(AnalyseSol.parcelle_id.in_(db.session.query(sub.c.id))))
    n_parc = db.session.query(func.count()).select_from(sub).scalar()
    n_donnees = stats_q.filter(AnalyseSol.ph.isnot(None) | AnalyseSol.p_olsen.isnot(None)
                               | AnalyseSol.texture.isnot(None)).count()
    n_car = stats_q.filter(AnalyseSol.origine == "CAR").count()
    moy = (stats_q.with_entities(func.avg(AnalyseSol.ph), func.avg(AnalyseSol.p_olsen),
                                 func.avg(AnalyseSol.k), func.avg(AnalyseSol.mo)).one())
    fert = dict(stats_q.with_entities(AnalyseSol.fertilite, func.count())
                .group_by(AnalyseSol.fertilite).all())

    ops = (db.session.query(OrganisationProducteur.id, OrganisationProducteur.nom)
           .filter(OrganisationProducteur.id.in_(
               base.with_entities(Producteur.organisation_id).distinct()))
           .order_by(OrganisationProducteur.nom).all())
    communes = sorted({c for (c,) in base.with_entities(Parcelle.commune).distinct() if c},
                      key=str.lower)
    n_points = db.session.query(func.count(PointSolIFDC.id)).scalar()
    return render_template(
        "sol/liste.html", lignes=lignes, total=total, page=page, par_page=par_page,
        n_parc=n_parc, n_donnees=n_donnees, n_car=n_car, moy=moy, fert=fert,
        ops=ops, communes=communes, n_points=n_points, args=request.args)


@sol_bp.route("/parcelle/<int:pid>", methods=["GET", "POST"])
@login_required
def parcelle(pid):
    p = db.session.get(Parcelle, pid)
    if not p or not peut_voir_parcelle(current_user, pid):
        flash("Parcelle introuvable ou hors de votre périmètre.", "warning")
        return redirect(url_for("sol.liste"))
    peut = _peut_saisir(pid)
    if request.method == "POST":
        if not peut:
            flash("Vous n'avez pas le droit de modifier le sol de cette parcelle.", "warning")
            return redirect(url_for("sol.parcelle", pid=pid))
        erreurs = service.enregistrer_saisie_car(p, request.form, current_user)
        if erreurs:
            for e in erreurs:
                flash(e, "danger")
            return render_template("sol/parcelle.html", p=p, a=p.analyse_sol, peut=peut,
                                   form=request.form, historique=_historique(pid))
        flash("Analyse de sol enregistrée. Elle remplace l'estimation IFDC pour cette parcelle.", "success")
        if request.form.get("retour") == "fiche":
            return redirect(url_for("parcelles.fiche", pid=pid) + "#sol")
        return redirect(url_for("sol.parcelle", pid=pid))
    return render_template("sol/parcelle.html", p=p, a=p.analyse_sol, peut=peut,
                           form=None, historique=_historique(pid))


def _historique(pid):
    return (db.session.query(HistoriqueSol).filter_by(parcelle_id=pid)
            .order_by(HistoriqueSol.date.desc()).limit(20).all())


@sol_bp.route("/parcelle/<int:pid>/ifdc", methods=["POST"])
@login_required
def parcelle_ifdc(pid):
    p = db.session.get(Parcelle, pid)
    if not p or not _peut_saisir(pid):
        flash("Action non autorisée.", "warning")
        return redirect(url_for("sol.liste"))
    if service.revenir_ifdc(p, current_user):
        flash("Valeurs IFDC rétablies pour cette parcelle.", "success")
    else:
        flash("Pas d'estimation IFDC disponible pour cette parcelle.", "warning")
    return redirect(url_for("sol.parcelle", pid=pid))


# --------------------------------------------------------------------------
@sol_bp.route("/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(NIVEAU_IMPORT)
def importer():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "import":
                f = request.files.get("fichier")
                if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
                    flash("Choisissez le fichier Excel de la base IFDC (.xlsx).", "warning")
                    return redirect(url_for("sol.importer"))
                r = service.importer_ifdc(f)
                flash(f"{r['points']} points IFDC importés "
                      f"({r['valeurs_ecartees']} valeurs hors plage écartées).", "success")
            s = service.rattacher()
            flash(f"Rattachement : {s['bonne']} parcelles fiabilité bonne, "
                  f"{s['indicative']} indicative, {s['hors']} hors couverture ; "
                  f"{s['car_conserve']} saisies CAR conservées.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("sol.importer"))
    n_points = db.session.query(func.count(PointSolIFDC.id)).scalar()
    dernier = db.session.query(func.max(PointSolIFDC.importe_le)).scalar()
    n_ecartes = (db.session.query(func.count(PointSolIFDC.id))
                 .filter(PointSolIFDC.valeurs_ecartees.isnot(None)).scalar())
    return render_template("sol/import.html", n_points=n_points, dernier=dernier,
                           n_ecartes=n_ecartes)


# --------------------------------------------------------------------------
@sol_bp.route("/carte")
@login_required
def carte():
    return render_template("sol/carte.html")


@sol_bp.route("/geojson")
@login_required
def geojson():
    q = (db.session.query(Parcelle.id, Parcelle.code, Parcelle.commune,
                          func.ST_AsGeoJSON(func.ST_SimplifyPreserveTopology(Parcelle.geom, 0.00002), 6),
                          AnalyseSol)
         .outerjoin(AnalyseSol, AnalyseSol.parcelle_id == Parcelle.id)
         .filter(Parcelle.geom.isnot(None)))
    ids = parcelle_ids(current_user)
    if ids is not None:
        q = q.filter(Parcelle.id.in_(ids))
    feats = []
    for pid, code, commune, g, a in q.all():
        props = {"id": pid, "code": code, "commune": commune}
        if a is not None:
            for k in ("ph", "p_olsen", "k", "mo", "n"):
                v = getattr(a, k)
                props[k] = round(v, 3) if v is not None else None
            props.update(texture=a.texture, fertilite=a.fertilite, origine=a.origine,
                         fiabilite=a.fiabilite, contraintes=a.contraintes)
        feats.append({"type": "Feature", "geometry": json.loads(g), "properties": props})
    return jsonify({"type": "FeatureCollection", "features": feats})


@sol_bp.route("/points.geojson")
@login_required
def points_geojson():
    rows = db.session.query(PointSolIFDC.code, func.ST_AsGeoJSON(PointSolIFDC.geom, 6),
                            PointSolIFDC.ph, PointSolIFDC.p_olsen, PointSolIFDC.c_total).all()
    feats = [{"type": "Feature", "geometry": json.loads(g),
              "properties": {"code": c, "ph": ph, "p_olsen": p,
                             "mo": round(ct * 1.724, 2) if ct is not None else None}}
             for c, g, ph, p, ct in rows if g]
    return jsonify({"type": "FeatureCollection", "features": feats})


# --------------------------------------------------------------------------
@sol_bp.route("/export")
@login_required
def export():
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "Sol par parcelle"
    entetes = (["Code", "Producteur", "Organisation", "Département", "Commune", "Village",
                "Superficie (ha)", "Origine", "Source", "Fiabilité IFDC", "Distance IFDC (m)"]
               + [f"{lib} ({u})" if u else lib for _k, lib, u, *_ in R.PARAMETRES]
               + ["Texture", "Fertilité", "Contraintes", "Date analyse", "Note"])
    ws.append(entetes)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2E7D32")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for p, a, pr, op in _filtrer(_requete()).order_by(OrganisationProducteur.nom, Parcelle.code):
        prod = f"{pr.prenom or ''} {pr.nom}".strip() if pr else None
        ligne = [p.code, prod, op.nom if op else None, p.departement, p.commune, p.village,
                 p.surface_ha]
        if a:
            ligne += [a.origine, a.source, a.fiabilite, a.distance_m]
            ligne += [round(getattr(a, k), 4) if getattr(a, k) is not None else None for k in R.CLES]
            ligne += [a.texture, a.fertilite, a.contraintes, a.date_analyse, a.note]
        else:
            ligne += [None] * (4 + len(R.CLES) + 5)
        ws.append(ligne)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="sol_parcelles.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
