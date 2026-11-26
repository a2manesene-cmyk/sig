import json
import unicodedata
from io import BytesIO

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
)
from flask_login import login_required, current_user
from sqlalchemy import func, distinct, or_, and_
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from app.extensions import db
from app.models.producteur import Producteur
from app.models.organisation import OrganisationProducteur
from app.models.zone import Region
from app.models.filiere import Filiere
from app.models.campagne import Campagne
from app.models.parcelle import Parcelle
from app.models.indice import IndiceVegetation
from app.models.rendement import RendementReference
from app.auth.decorators import niveau_minimum
from app.scoping import producteur_ids, organisation_ids, peut_voir_producteur

producteurs_bp = Blueprint("producteurs", __name__, url_prefix="/producteurs")

# Catalogue de langues : provient du module WhatsApp s'il est installé,
# sinon repli local (l'application démarre dans tous les cas).
try:
    from app.notifications.langue import LANGUES
except Exception:
    LANGUES = {"fr": "Français", "wo": "Wolof", "ff": "Pulaar"}


def _score_banca(prod):
    """Score de bancabilité (tolérant : renvoie None si le module est absent)."""
    try:
        from app.bancabilite.calcul import score_producteur
        return score_producteur(prod)
    except Exception:
        return None


# --- Helpers ----------------------------------------------------------------
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


def _norm(s):
    """minuscule + suppression des accents, pour les rapprochements."""
    s = (s or "").strip().lower()
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _txt(props, cle):
    v = props.get(cle)
    if isinstance(v, str):
        v = v.strip()
    return v or None


def _agg_parcelles():
    """Retourne {producteur_id: (nb_parcelles, superficie_ha)}."""
    rows = (
        db.session.query(
            Parcelle.producteur_id,
            func.count(Parcelle.id),
            func.coalesce(func.sum(Parcelle.surface_ha), 0.0),
        )
        .group_by(Parcelle.producteur_id)
        .all()
    )
    return {pid: (cnt, round(s or 0, 2)) for pid, cnt, s in rows if pid is not None}


def _filtrer_producteurs(q, region_id, op_id):
    query = Producteur.query
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Producteur.nom.ilike(like),
            Producteur.prenom.ilike(like),
            Producteur.telephone.ilike(like),
        ))
    if region_id:
        query = query.filter(Producteur.region_id == region_id)
    if op_id:
        query = query.filter(Producteur.organisation_id == op_id)
    return query.order_by(Producteur.nom)


# --- Listes -----------------------------------------------------------------
@producteurs_bp.route("/")
@login_required
def liste():
    q = (request.args.get("q") or "").strip()
    region_id = _i(request.args.get("region"))
    op_id = _i(request.args.get("organisation"))
    page = _i(request.args.get("page")) or 1

    base = _filtrer_producteurs(q, region_id, op_id)
    _prod_ids = producteur_ids(current_user)
    if _prod_ids is not None:
        base = base.filter(Producteur.id.in_(_prod_ids))
    pagination = base.paginate(
        page=page, per_page=24, error_out=False
    )
    ids = [p.id for p in pagination.items]

    # Nombre de parcelles en stress hydrique par producteur (page courante)
    alertes = {}
    if ids:
        subi = (db.session.query(
                    IndiceVegetation.parcelle_id.label("pid"),
                    func.max(IndiceVegetation.date).label("d"))
                .group_by(IndiceVegetation.parcelle_id).subquery())
        for pid, n in (
            db.session.query(Parcelle.producteur_id, func.count(distinct(Parcelle.id)))
            .join(IndiceVegetation, IndiceVegetation.parcelle_id == Parcelle.id)
            .join(subi, and_(subi.c.pid == IndiceVegetation.parcelle_id,
                             subi.c.d == IndiceVegetation.date))
            .filter(Parcelle.producteur_id.in_(ids),
                    IndiceVegetation.ndvi < 0.30, IndiceVegetation.ndmi < 0.0)
            .group_by(Parcelle.producteur_id).all()):
            alertes[pid] = n

    return render_template(
        "producteurs/liste.html",
        pagination=pagination,
        producteurs=pagination.items,
        agg=_agg_parcelles(),
        alertes=alertes,
        regions=Region.query.order_by(Region.nom).all(),
        organisations=OrganisationProducteur.query.order_by(OrganisationProducteur.nom).all(),
        q=q, region_id=region_id, op_id=op_id,
    )


@producteurs_bp.route("/export.xlsx")
@login_required
def export_xlsx():
    q = (request.args.get("q") or "").strip()
    region_id = _i(request.args.get("region"))
    op_id = _i(request.args.get("organisation"))
    producteurs = _filtrer_producteurs(q, region_id, op_id).all()
    agg = _agg_parcelles()

    wb = Workbook()
    ws = wb.active
    ws.title = "Producteurs"
    entetes = ["Nom", "Téléphone", "Sexe", "Organisation", "Région",
               "Nb parcelles", "Superficie (ha)"]
    ws.append(entetes)

    entete_fill = PatternFill("solid", fgColor="1E5631")
    entete_font = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.fill = entete_fill
        cell.font = entete_font
        cell.alignment = Alignment(horizontal="center")

    for pr in producteurs:
        nb, surface = agg.get(pr.id, (0, 0))
        ws.append([
            pr.nom_complet,
            pr.telephone or "",
            pr.sexe or "",
            pr.organisation.nom if pr.organisation else "",
            pr.region.nom if pr.region else "",
            nb,
            surface,
        ])

    largeurs = [28, 16, 8, 32, 12, 14, 16]
    for i, w in enumerate(largeurs, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio, as_attachment=True, download_name="producteurs_ancar.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@producteurs_bp.route("/<int:pid>")
@login_required
def fiche(pid):
    prod = db.session.get(Producteur, pid)
    if not prod:
        flash("Producteur introuvable.", "warning")
        return redirect(url_for("producteurs.liste"))
    if not peut_voir_producteur(current_user, pid):
        flash("Ce producteur est hors de votre périmètre.", "warning")
        return redirect(url_for("producteurs.liste"))
    parcelles = (
        Parcelle.query.filter_by(producteur_id=pid)
        .order_by(Parcelle.code)
        .all()
    )
    total_surface = round(sum(p.surface_ha or 0 for p in parcelles), 2)
    animateurs = sorted({p.animateur.strip() for p in parcelles if p.animateur})

    # Rendements par parcelle et par campagne (table rendements_reference)
    campagnes = Campagne.query.order_by(Campagne.annee).all()
    pids = [p.id for p in parcelles]
    rendements = {}   # (parcelle_id, annee) -> kg/ha
    if pids:
        for r in RendementReference.query.filter(
                RendementReference.parcelle_id.in_(pids)).all():
            if r.annee is not None:
                rendements[(r.parcelle_id, r.annee)] = r.rendement_kg_ha
    # Évolution : moyenne du producteur par année
    par_annee = {}
    for (pid_, annee), val in rendements.items():
        par_annee.setdefault(annee, []).append(val)
    evolution = [
        {"annee": a, "moyenne": round(sum(v) / len(v))}
        for a, v in sorted(par_annee.items())
    ]

    return render_template(
        "producteurs/fiche.html",
        prod=prod, parcelles=parcelles, total_surface=total_surface,
        langues=LANGUES, animateurs=animateurs,
        campagnes=campagnes, rendements=rendements, evolution=evolution,
        banca=_score_banca(prod),
    )


@producteurs_bp.route("/<int:pid>/rendement", methods=["POST"])
@login_required
@niveau_minimum(2)
def enregistrer_rendement(pid):
    """Saisie/mise à jour d'un rendement (kg/ha) pour une parcelle et une campagne.
    Alimente la table de référence, donc aussi la calibration du modèle."""
    prod = db.session.get(Producteur, pid)
    if not prod:
        flash("Producteur introuvable.", "warning")
        return redirect(url_for("producteurs.liste"))
    if not peut_voir_producteur(current_user, pid):
        flash("Ce producteur est hors de votre périmètre.", "warning")
        return redirect(url_for("producteurs.liste"))

    parcelle_id = _i(request.form.get("parcelle_id"))
    campagne_id = _i(request.form.get("campagne_id"))
    rendement = _f(request.form.get("rendement"))

    p = db.session.get(Parcelle, parcelle_id) if parcelle_id else None
    camp = db.session.get(Campagne, campagne_id) if campagne_id else None
    if not p or p.producteur_id != pid:
        flash("Parcelle invalide pour ce producteur.", "warning")
    elif not camp or camp.annee is None:
        flash("Campagne invalide.", "warning")
    elif rendement is None or rendement < 0:
        flash("Rendement invalide (kg/ha).", "warning")
    else:
        vmax = (db.session.query(func.max(IndiceVegetation.ndvi))
                .filter(IndiceVegetation.parcelle_id == p.id).scalar())
        ref = (RendementReference.query
               .filter_by(parcelle_id=p.id, annee=camp.annee).first())
        if ref:                       # mise à jour (upsert)
            ref.rendement_kg_ha = rendement
            ref.culture = p.culture
            ref.ndvi_max = vmax
        else:
            db.session.add(RendementReference(
                parcelle_id=p.id, annee=camp.annee, culture=p.culture,
                rendement_kg_ha=rendement, ndvi_max=vmax))
        db.session.commit()
        note = "" if vmax is not None else (
            " (NDVI pas encore calculé : ce point ne servira au modèle "
            "qu'après un calcul d'indices)")
        flash(f"Rendement enregistré : {p.code} — {camp.libelle} — "
              f"{rendement:.0f} kg/ha{note}.", "success")
    return redirect(url_for("producteurs.fiche", pid=pid))


@producteurs_bp.route("/<int:pid>/langue", methods=["POST"])
@login_required
@niveau_minimum(2)
def definir_langue(pid):
    prod = db.session.get(Producteur, pid)
    if not prod:
        flash("Producteur introuvable.", "warning")
        return redirect(url_for("producteurs.liste"))
    if not peut_voir_producteur(current_user, pid):
        flash("Ce producteur est hors de votre périmètre.", "warning")
        return redirect(url_for("producteurs.liste"))
    code = (request.form.get("langue") or "").strip()
    prod.langue = code if code in LANGUES else None
    db.session.commit()
    lib = LANGUES.get(prod.langue, "langue de la zone")
    flash(f"Langue de communication définie : {lib}.", "success")
    return redirect(url_for("producteurs.fiche", pid=pid))


@producteurs_bp.route("/<int:pid>/geojson")
@login_required
def fiche_geojson(pid):
    rows = (
        db.session.query(
            Parcelle.id, Parcelle.code, Parcelle.culture, Parcelle.surface_ha,
            func.ST_AsGeoJSON(Parcelle.geom),
        )
        .filter(Parcelle.producteur_id == pid, Parcelle.geom.isnot(None))
        .all()
    )
    features = []
    for pid_, code, culture, surface, geom_json in rows:
        if not geom_json:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(geom_json),
            "properties": {
                "id": pid_, "code": code, "culture": culture, "surface_ha": surface,
            },
        })
    return jsonify({"type": "FeatureCollection", "features": features})


@producteurs_bp.route("/organisations")
@login_required
def organisations():
    q = (request.args.get("q") or "").strip()
    region_id = _i(request.args.get("region"))
    filiere_id = _i(request.args.get("filiere"))

    # Filières présentes par organisation
    fq = (
        db.session.query(OrganisationProducteur.id, Filiere.nom)
        .join(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
        .join(Parcelle, Parcelle.producteur_id == Producteur.id)
        .join(Filiere, Filiere.id == Parcelle.filiere_id)
        .distinct()
    )
    filieres_par_op = {}
    for op_id, fnom in fq:
        filieres_par_op.setdefault(op_id, set()).add(fnom)

    # Agrégats par organisation
    agg = (
        db.session.query(
            OrganisationProducteur.id,
            OrganisationProducteur.nom,
            OrganisationProducteur.type,
            Region.nom.label("region"),
            func.count(distinct(Producteur.id)),
            func.count(distinct(Parcelle.id)),
            func.coalesce(func.sum(Parcelle.surface_ha), 0.0),
        )
        .select_from(OrganisationProducteur)
        .outerjoin(Region, Region.id == OrganisationProducteur.region_id)
        .outerjoin(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
        .outerjoin(Parcelle, Parcelle.producteur_id == Producteur.id)
        .group_by(
            OrganisationProducteur.id, OrganisationProducteur.nom,
            OrganisationProducteur.type, Region.nom,
        )
    )

    if q:
        agg = agg.filter(OrganisationProducteur.nom.ilike(f"%{q}%"))
    if region_id:
        agg = agg.filter(OrganisationProducteur.region_id == region_id)
    if filiere_id:
        sub = (
            db.session.query(Producteur.organisation_id)
            .join(Parcelle, Parcelle.producteur_id == Producteur.id)
            .filter(Parcelle.filiere_id == filiere_id)
            .distinct()
        )
        ids = [r[0] for r in sub if r[0] is not None]
        agg = agg.filter(OrganisationProducteur.id.in_(ids or [-1]))

    agg = agg.order_by(func.count(distinct(Parcelle.id)).desc())

    _org_ids = organisation_ids(current_user)
    if _org_ids is not None:
        agg = agg.filter(OrganisationProducteur.id.in_(_org_ids or [-1]))

    # Parcelles en stress hydrique par organisation
    subi = (db.session.query(
                IndiceVegetation.parcelle_id.label("pid"),
                func.max(IndiceVegetation.date).label("d"))
            .group_by(IndiceVegetation.parcelle_id).subquery())
    alertes = {}
    for oid, n in (
        db.session.query(OrganisationProducteur.id, func.count(distinct(Parcelle.id)))
        .join(Producteur, Producteur.organisation_id == OrganisationProducteur.id)
        .join(Parcelle, Parcelle.producteur_id == Producteur.id)
        .join(IndiceVegetation, IndiceVegetation.parcelle_id == Parcelle.id)
        .join(subi, and_(subi.c.pid == IndiceVegetation.parcelle_id,
                         subi.c.d == IndiceVegetation.date))
        .filter(IndiceVegetation.ndvi < 0.30, IndiceVegetation.ndmi < 0.0)
        .group_by(OrganisationProducteur.id).all()):
        alertes[oid] = n

    # Magasins de stockage par organisation
    stock = {}
    try:
        from app.models.magasin import MagasinOP
        with db.session.begin_nested():
            for oid, n, cap in (db.session.query(MagasinOP.organisation_id, func.count(MagasinOP.id),
                                                 func.sum(MagasinOP.capacite_t))
                                .group_by(MagasinOP.organisation_id).all()):
                stock[oid] = (n, cap or 0)
    except Exception:                      # table pas encore créée (init_magasins.py)
        pass

    lignes = []
    for oid, nom, typ, region, nb_prod, nb_parc, surface in agg:
        lignes.append({
            "id": oid, "nom": nom, "type": typ, "region": region,
            "nb_prod": nb_prod, "nb_parc": nb_parc,
            "surface": round(surface or 0, 2),
            "filieres": sorted(filieres_par_op.get(oid, [])),
            "alertes": alertes.get(oid, 0),
            "magasins": stock.get(oid, (0, 0))[0], "capacite": stock.get(oid, (0, 0))[1],
        })

    return render_template(
        "producteurs/organisations.html",
        lignes=lignes,
        regions=Region.query.order_by(Region.nom).all(),
        filieres=Filiere.query.order_by(Filiere.nom).all(),
        q=q, region_id=region_id, filiere_id=filiere_id,
    )


# --- Peuplement automatique depuis le GeoJSON -------------------------------
@producteurs_bp.route("/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(2)
def importer():
    if request.method == "POST":
        fichier = request.files.get("fichier")
        if not fichier or fichier.filename == "":
            flash("Aucun fichier sélectionné.", "warning")
            return redirect(url_for("producteurs.importer"))
        try:
            data = json.load(fichier.stream)
        except Exception:
            flash("Fichier GeoJSON invalide.", "danger")
            return redirect(url_for("producteurs.importer"))

        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]

        # Références préchargées
        regions = {_norm(r.nom): r for r in Region.query.all()}
        filieres = {_norm(f.nom): f for f in Filiere.query.all()}
        campagne = Campagne.query.filter_by(active=True).first()
        parcelles_par_code = {p.code: p for p in Parcelle.query.all() if p.code}

        cache_op = {}
        cache_prod = {}
        n_op = n_prod = n_liens = 0

        for feat in features:
            p = feat.get("properties", {}) or {}
            fid = _i(p.get("fid") or p.get("id"))
            code = f"P-{fid:05d}" if fid else None
            region = regions.get(_norm(_txt(p, "Region") or ""))

            # --- Organisation de producteurs (coopérative) ---
            op = None
            coop_nom = _txt(p, "Nom cooperative")
            if coop_nom:
                op = cache_op.get(coop_nom)
                if not op:
                    op = OrganisationProducteur.query.filter_by(nom=coop_nom).first()
                    if not op:
                        op = OrganisationProducteur(nom=coop_nom, type="Coopérative", region=region)
                        db.session.add(op)
                        n_op += 1
                    cache_op[coop_nom] = op

            # --- Producteur ---
            prod = None
            nom_prod = _txt(p, "Prenom et nom producteur")
            tel = _txt(p, "Telephone producteur")
            if tel is not None:
                tel = str(tel)
            if nom_prod:
                cle = (nom_prod, tel)
                prod = cache_prod.get(cle)
                if not prod:
                    prod = Producteur.query.filter_by(nom=nom_prod, telephone=tel).first()
                    if not prod:
                        sexe_raw = _norm(_txt(p, "Sexe") or "")
                        sexe = "F" if sexe_raw.startswith("f") else ("H" if sexe_raw else None)
                        prod = Producteur(
                            nom=nom_prod, telephone=tel, sexe=sexe,
                            organisation=op, region=region,
                        )
                        db.session.add(prod)
                        n_prod += 1
                    cache_prod[cle] = prod

            # --- Rattachement de la parcelle ---
            parc = parcelles_par_code.get(code) if code else None
            if parc:
                if prod:
                    parc.producteur = prod
                fil = filieres.get(_norm(parc.culture or ""))
                if fil:
                    parc.filiere = fil
                if campagne:
                    parc.campagne = campagne
                n_liens += 1

        db.session.commit()
        flash(
            f"Peuplement terminé : {n_prod} producteur(s), {n_op} organisation(s), "
            f"{n_liens} parcelle(s) rattachée(s).",
            "success",
        )
        return redirect(url_for("producteurs.liste"))

    return render_template("producteurs/import.html")
