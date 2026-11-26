"""Magasins de stockage des OP : carte, synthèse capacité / production, saisie, import."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func, text

from app.extensions import db
from app.auth.decorators import niveau_minimum
from app.models.magasin import MagasinOP
from app.models.organisation import OrganisationProducteur
from app.scoping import organisation_ids, parcelle_ids
from app.magasins import service

magasins_bp = Blueprint("magasins", __name__, url_prefix="/magasins")

NIVEAU_SAISIE = 2    # CAR, CDCAR, conseiller et au-dessus
NIVEAU_IMPORT = 4    # DG, ADMIN


def _requete():
    q = MagasinOP.query
    oids = organisation_ids(current_user)
    if oids is not None:
        q = q.filter(MagasinOP.organisation_id.in_(oids or [-1]))
    return q


def _peut_modifier(m):
    if current_user.niveau < NIVEAU_SAISIE:
        return False
    oids = organisation_ids(current_user)
    return oids is None or m.organisation_id in oids


@magasins_bp.route("/")
@login_required
def liste():
    op_id = request.args.get("op", type=int)
    q = _requete()
    if op_id:
        q = q.filter(MagasinOP.organisation_id == op_id)
    magasins = q.outerjoin(OrganisationProducteur).order_by(OrganisationProducteur.nom, MagasinOP.code).all()
    coords = service.coordonnees()
    oids = organisation_ids(current_user)
    synth = service.synthese(oids, parcelle_ids(current_user))
    if op_id:
        synth = [s for s in synth if s["id"] == op_id]
    cap = sum(m.capacite_t or 0 for m in magasins)
    prod_ops = {s["id"] for s in synth if s["magasins"]}
    prod = sum(s["production"] or 0 for s in synth if s["id"] in prod_ops)
    kpis = {"magasins": len(magasins), "capacite": round(cap, 1),
            "ops": len({m.organisation_id for m in magasins if m.organisation_id}),
            "sans_position": sum(1 for m in magasins if m.id not in coords),
            "production": round(prod, 1), "couverture": round(100 * cap / prod) if prod else None,
            "ops_sans_magasin": [s["nom"] for s in synth if not s["magasins"]]}
    points = [{"id": m.id, "code": m.code, "op": m.organisation.nom if m.organisation else m.nom_op_source,
               "localite": m.localite, "capacite": m.capacite_t, "responsable": m.responsable,
               "lat": coords[m.id][0], "lon": coords[m.id][1]} for m in magasins if m.id in coords]
    ops = (OrganisationProducteur.query.filter(OrganisationProducteur.id.in_(oids or [-1]))
           if oids is not None else OrganisationProducteur.query).order_by(OrganisationProducteur.nom).all()
    return render_template("magasins/liste.html", magasins=magasins, coords=coords, synth=synth, kpis=kpis,
                           points=points, ops=ops, op_id=op_id, peut_saisir=current_user.niveau >= NIVEAU_SAISIE,
                           peut_importer=current_user.niveau >= NIVEAU_IMPORT)


@magasins_bp.route("/points.json")
@login_required
def points():
    """Magasins positionnés (pour les autres cartes : tableau de bord, carte SIG)."""
    coords = service.coordonnees()
    out = []
    for m in _requete().all():
        if m.id in coords:
            out.append({"id": m.id, "code": m.code, "op": m.organisation.nom if m.organisation else m.nom_op_source,
                        "localite": m.localite, "capacite": m.capacite_t,
                        "lat": round(coords[m.id][0], 6), "lon": round(coords[m.id][1], 6)})
    return jsonify(out)


@magasins_bp.route("/<int:mid>/modifier", methods=["GET", "POST"])
@login_required
def modifier(mid):
    m = MagasinOP.query.get_or_404(mid)
    if not _peut_modifier(m):
        flash("Vous ne pouvez pas modifier ce magasin.", "warning")
        return redirect(url_for("magasins.liste"))
    if request.method == "POST":
        f = request.form

        def num(k):
            v = (f.get(k) or "").replace(",", ".").strip()
            try:
                return float(v) if v else None
            except ValueError:
                return None
        m.organisation_id = f.get("organisation_id", type=int) or None
        for k in ("localite", "commune", "type", "responsable", "observations"):
            setattr(m, k, (f.get(k) or "").strip() or None)
        m.telephone = service._tel(f.get("telephone"))
        m.longueur_m, m.largeur_m, m.capacite_t = num("longueur_m"), num("largeur_m"), num("capacite_t")
        lat, lon = num("lat"), num("lon")
        if (lat is None) != (lon is None) or (lat is not None and not (12 <= lat <= 17 and -18 <= lon <= -11)):
            flash("Position non enregistrée : latitude entre 12 et 17, longitude entre -18 et -11 (Sénégal), "
                  "en degrés décimaux.", "warning")
        else:
            service.placer(m, lat, lon)
        db.session.commit()
        flash(f"Magasin {m.code} enregistré.", "success")
        return redirect(url_for("magasins.liste", op=m.organisation_id) if request.args.get("retour") else url_for("magasins.liste"))
    c = service.coordonnees().get(m.id)
    oids = organisation_ids(current_user)
    ops = (OrganisationProducteur.query.filter(OrganisationProducteur.id.in_(oids or [-1]))
           if oids is not None else OrganisationProducteur.query).order_by(OrganisationProducteur.nom).all()
    return render_template("magasins/modifier.html", m=m, lat=c[0] if c else None, lon=c[1] if c else None, ops=ops)


@magasins_bp.route("/ajouter", methods=["POST"])
@login_required
@niveau_minimum(NIVEAU_SAISIE)
def ajouter():
    n = db.session.query(func.count(MagasinOP.id)).scalar() or 0
    code = f"MAG-{n + 1:02d}"
    while MagasinOP.query.filter_by(code=code).first():
        n += 1; code = f"MAG-{n + 1:02d}"
    oids = organisation_ids(current_user)
    m = MagasinOP(code=code, organisation_id=(oids[0] if oids else None))
    db.session.add(m); db.session.commit()
    return redirect(url_for("magasins.modifier", mid=m.id))


@magasins_bp.route("/<int:mid>/supprimer", methods=["POST"])
@login_required
@niveau_minimum(NIVEAU_IMPORT)
def supprimer(mid):
    m = MagasinOP.query.get_or_404(mid)
    db.session.delete(m); db.session.commit()
    flash(f"Magasin {m.code} supprimé.", "success")
    return redirect(url_for("magasins.liste"))


@magasins_bp.route("/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(NIVEAU_IMPORT)
def importer():
    if request.method == "POST":
        f = request.files.get("fichier")
        if not f or not f.filename.lower().endswith((".geojson", ".json")):
            flash("Choisissez un fichier GeoJSON (.geojson).", "warning")
            return redirect(url_for("magasins.importer"))
        try:
            b = service.importer(f.read().decode("utf-8-sig"), remplacer=bool(request.form.get("remplacer")))
        except (ValueError, UnicodeDecodeError) as exc:
            db.session.rollback()
            flash(f"Import impossible : {exc}", "danger")
            return redirect(url_for("magasins.importer"))
        msg = f"{b['crees']} magasin(s) ajouté(s), {b['maj']} mis à jour."
        if b["sans_position"]:
            msg += f" {b['sans_position']} sans position GPS (à compléter)."
        flash(msg, "success")
        if b["sans_op"]:
            flash("OP non reconnue(s), à rattacher à la main : " + ", ".join(sorted(set(b["sans_op"]))), "warning")
        return redirect(url_for("magasins.liste"))
    return render_template("magasins/import.html", n=db.session.query(func.count(MagasinOP.id)).scalar())
