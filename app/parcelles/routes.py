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
    page = _i(request.args.get("page")) or 1

    query = Parcelle.query
    if q:
        query = query.filter(Parcelle.code.ilike(f"%{q}%"))
    if culture:
        query = query.filter(Parcelle.culture == culture)
    if region_id:
        query = (query.join(Producteur, Producteur.id == Parcelle.producteur_id)
                      .filter(Producteur.region_id == region_id))
    if campagne_id:
        query = query.filter(Parcelle.campagne_id == campagne_id)
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

    return render_template(
        "parcelles/liste.html",
        vues=vues, pagination=pagination,
        q=q, culture=culture, region_id=region_id, campagne_id=campagne_id,
        cultures=cultures,
        regions=Region.query.order_by(Region.nom).all(),
        campagnes=Campagne.query.order_by(Campagne.annee.desc()).all(),
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

    return render_template("parcelles/fiche.html", p=p, serie=serie,
                           indices=indices, lecture=lecture,
                           historique=historique, rotation=rotation)


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
    if request.method == "POST":
        fichier = request.files.get("fichier")
        srid_src = request.form.get("srid_source", "4326")
        if not fichier or fichier.filename == "":
            flash("Aucun fichier sélectionné.", "warning")
            return redirect(url_for("parcelles.importer"))
        try:
            data = json.load(fichier.stream)
        except Exception:
            flash("Fichier GeoJSON invalide.", "danger")
            return redirect(url_for("parcelles.importer"))

        if data.get("type") == "FeatureCollection":
            features = data.get("features", [])
        else:
            features = [data]

        transformer = None
        if srid_src != "4326":
            transformer = Transformer.from_crs(
                f"EPSG:{srid_src}", "EPSG:4326", always_xy=True
            ).transform

        # Signatures géométriques déjà présentes -> dédoublonnage par la FORME
        # (indépendant du fid, qui recommence à 1 dans chaque fichier).
        import hashlib
        from shapely.wkt import loads as _wl, dumps as _wd

        def _sig(gg):
            return hashlib.md5(_wd(gg, rounding_precision=6).encode()).hexdigest()

        sigs_existantes = {}
        for (pid_ex, wktxt) in (db.session.query(Parcelle.id, func.ST_AsText(Parcelle.geom))
                                .filter(Parcelle.geom.isnot(None)).all()):
            try:
                sigs_existantes[_sig(_wl(wktxt))] = pid_ex
            except Exception:
                pass

        codes_existants = {
            c for (c,) in db.session.query(Parcelle.code)
            .filter(Parcelle.code.isnot(None)).all()
        }
        regions_par_nom = {r.nom.strip().lower(): r for r in Region.query.all()}
        camp_cache = {c.libelle: c for c in Campagne.query.all()}
        orgs_cache = {o.nom.strip().lower(): o for o in OrganisationProducteur.query.all()}
        prods_cache = {}
        for pr in Producteur.query.all():
            prods_cache[(pr.nom_complet.strip().lower(), (pr.telephone or "").strip())] = pr

        n_ok, n_skip, n_doublon, n_maj = 0, 0, 0, 0
        maj_animateur = {}
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

            animateur = _premier(props, [
                "Nom & Prénom de l'agent", "Nom & Prenom de l'agent",
                "Prenom et nom enqueteur", "Prénom et nom enquêteur",
                "Nom animateur", "animateur", "Animateur", "agent", "Agent",
                "enqueteur", "Enquêteur"])
            animateur = str(animateur).strip()[:120] if animateur else None

            sig = _sig(g)
            if sig in sigs_existantes:
                eid = sigs_existantes[sig]
                if animateur and eid:
                    maj_animateur[eid] = animateur   # renseigner l'animateur existant
                n_doublon += 1
                continue

            # Code : propriété naturelle si présente, sinon dérivé de la forme (stable)
            code = _premier(props, ["code", "nom", "Nom"])
            if not code:
                code = f"P-{sig[:8]}"
            code = str(code)[:50]
            if code in codes_existants:
                base, k = code, 1
                while code in codes_existants:
                    code = f"{base}-{k}"
                    k += 1

            surface = (_f(_premier(props, ["surface_ha", "surface", "Superficie",
                                            "superficie", "Surface (ha)", "Surface"]))
                       or _ha_depuis_wgs84(g))
            culture = _culture_courante(props)
            dep = _premier(props, ["Departement", "Département", "departement"])
            com = _premier(props, ["Commune", "commune"])
            vil = _premier(props, ["Village", "village", "Localité", "Localite"])
            reg = _premier(props, ["Region", "Région", "region"])
            observations = _premier(props, ["observations", "Observations"])

            # Producteur & coopérative (créés/liés au vol)
            nom_prod = _premier(props, [
                "Prenom et nom producteur", "Nom & Prénom du producteur",
                "Prénom et nom producteur", "Nom et prenom producteur",
                "producteur", "Producteur"])
            tel_prod = _premier(props, [
                "Telephone producteur", "Numero de telephone Producteur",
                "Téléphone producteur", "telephone"])
            nom_coop = _premier(props, [
                "cooperative_norm", "Nom cooperative", "Coopérative",
                "Cooperative", "cooperative"])
            animateur = _premier(props, [
                "Nom & Prénom de l'agent", "Nom & Prenom de l'agent",
                "Prenom et nom enqueteur", "Prénom et nom enquêteur",
                "Nom animateur", "animateur", "Animateur", "agent", "Agent",
                "enqueteur", "Enquêteur"]) if False else animateur

            try:
                created_org = created_prod = None
                with db.session.begin_nested():
                    region_obj = regions_par_nom.get(reg.strip().lower()) if reg else None

                    org = orgs_cache.get(str(nom_coop).strip().lower()) if nom_coop else None
                    if nom_coop and not org:
                        org = OrganisationProducteur(nom=str(nom_coop).strip()[:150], type="Coopérative")
                        if region_obj:
                            org.region_id = region_obj.id
                        db.session.add(org)
                        created_org = (str(nom_coop).strip().lower(), org)

                    prod = None
                    if nom_prod:
                        pk = (str(nom_prod).strip().lower(), (str(tel_prod).strip() if tel_prod else ""))
                        prod = prods_cache.get(pk)
                        if not prod:
                            prod = Producteur(
                                nom=str(nom_prod).strip()[:80],
                                telephone=(str(tel_prod).strip()[:30] if tel_prod else None),
                                sexe=_sexe(props))
                            if region_obj:
                                prod.region_id = region_obj.id
                            if org:
                                prod.organisation = org
                            db.session.add(prod)
                            created_prod = (pk, prod)
                        elif org and not prod.organisation_id:
                            prod.organisation = org

                    p = Parcelle(
                        code=code, culture=culture, surface_ha=surface,
                        observations=observations,
                        departement=(dep.strip() if dep else None),
                        commune=(com.strip() if com else None),
                        village=(vil.strip() if vil else None),
                        animateur=(str(animateur).strip()[:120] if animateur else None),
                        geom=WKTElement(g.wkt, srid=4326),
                    )
                    if region_obj:
                        p.region_id = region_obj.id
                    if prod:
                        p.producteur = prod
                    db.session.add(p)

                    # Historique cultural : "... culture ... AAAA" -> campagne Hivernage AAAA
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
                        db.session.add(HistoriqueCultural(
                            parcelle=p, campagne=camp, annee=annee,
                            culture=str(val)[:80], source="GeoJSON"))
                sigs_existantes[sig] = None
                codes_existants.add(code)
                if created_org:
                    orgs_cache[created_org[0]] = created_org[1]
                if created_prod:
                    prods_cache[created_prod[0]] = created_prod[1]
                n_ok += 1
            except Exception:
                n_skip += 1

        # Renseigner l'animateur sur les parcelles déjà existantes
        for pid_maj, anim in maj_animateur.items():
            Parcelle.query.filter_by(id=pid_maj).update(
                {Parcelle.animateur: anim}, synchronize_session=False)
            n_maj += 1

        db.session.commit()
        flash(
            f"Import additif : {n_ok} nouvelle(s) parcelle(s), "
            f"{n_doublon} déjà présente(s) ignorée(s) (dont {n_maj} animateur mis à jour), "
            f"{n_skip} en erreur. Localisation et historique inclus.",
            "success",
        )
        return redirect(url_for("parcelles.carte"))

    return render_template("parcelles/import.html")


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
