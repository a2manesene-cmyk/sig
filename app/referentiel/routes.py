from datetime import datetime, date

from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.auth.decorators import niveau_minimum
from app.models.parcelle import Parcelle
from app.models.referentiel import CultureRef, VarieteRef
from app.scoping import parcelle_ids, peut_voir_parcelle
from app.referentiel import service, fiches

referentiel_bp = Blueprint("referentiel", __name__, url_prefix="/referentiel")
NIVEAU_EDITION = 4

CHAMPS_NUM = ["cycle_j", "l_ini", "l_dev", "l_mid", "l_fin", "kc_ini", "kc_mid", "kc_fin",
              "rdt_min", "rdt_max", "fumure_org_min_t", "fumure_org_max_t", "fond_kg",
              "couverture_kg", "ph_min"]
CHAMPS_TXT = ["synonymes", "famille", "systeme", "periode", "ecartements", "engrais_fond",
              "engrais_couverture", "apports_j", "source"]
CHAMPS_INT = {"cycle_j", "l_ini", "l_dev", "l_mid", "l_fin"}


def _resume(p):
    """Résumé des besoins de la culture pour la fiche parcelle (None si non applicable)."""
    try:
        with db.session.begin_nested():
            c = service.culture_ref(p.culture)
            if c is None:
                return {"culture": None}
            debut, origine = service.date_debut(p)
            fum = service.plan_fumure(c, debut, p.surface_ha, _sol(p.id))
            eau = (service.besoins_eau(c, debut, p.surface_ha, p.id)
                   if c.systeme == "Irrigué" and c.kc_ini is not None and c.cycle_j else None)
            sv = fiches.suivi(p, c, debut) if origine != "aujourd'hui" else None
            return {"culture": c, "debut": debut, "origine": origine, "fumure": fum, "eau": eau, "suivi": sv}
    except Exception:
        return None


def _sol(pid):
    try:
        from app.models.sol import AnalyseSol
        with db.session.begin_nested():
            return db.session.query(AnalyseSol).filter_by(parcelle_id=pid).first()
    except Exception:
        return None


def _origine_potentiel(pid):
    try:
        from app.models.referentiel import PotentielParcelle
        with db.session.begin_nested():
            return db.session.get(PotentielParcelle, pid)
    except Exception:
        return None


@referentiel_bp.app_context_processor
def _ctx():
    return {"besoins_culture": _resume, "origine_potentiel": _origine_potentiel}


# --------------------------------------------------------------------------
@referentiel_bp.route("/")
@login_required
def liste():
    cultures = db.session.query(CultureRef).order_by(CultureRef.systeme.desc(), CultureRef.nom).all()
    q = db.session.query(Parcelle.culture, func.count()).group_by(Parcelle.culture)
    ids = parcelle_ids(current_user)
    if ids is not None:
        q = q.filter(Parcelle.id.in_(ids))
    par_culture = {}
    sans_ref = []
    for cult, n in q.all():
        c = service.culture_ref(cult)
        if c:
            par_culture[c.id] = par_culture.get(c.id, 0) + n
        elif cult:
            sans_ref.append((cult, n))
    sans_ref.sort(key=lambda x: -x[1])
    return render_template("referentiel/liste.html", cultures=cultures, par_culture=par_culture,
                           sans_ref=sans_ref)


@referentiel_bp.route("/culture/<int:cid>", methods=["GET", "POST"])
@login_required
def culture(cid):
    c = db.session.get(CultureRef, cid)
    if c is None:
        flash("Culture introuvable.", "warning")
        return redirect(url_for("referentiel.liste"))
    peut = current_user.niveau >= NIVEAU_EDITION
    if request.method == "POST":
        if not peut:
            flash("Modification réservée à l'administration.", "warning")
            return redirect(url_for("referentiel.culture", cid=cid))
        action = request.form.get("action", "culture")
        if action == "culture":
            erreurs = []
            for k in CHAMPS_NUM:
                v = (request.form.get(k) or "").strip().replace(",", ".")
                if v == "":
                    setattr(c, k, None)
                    continue
                try:
                    setattr(c, k, int(float(v)) if k in CHAMPS_INT else float(v))
                except ValueError:
                    erreurs.append(f"« {k} » : valeur non numérique ({v}).")
            for k in CHAMPS_TXT:
                setattr(c, k, (request.form.get(k) or "").strip() or None)
            c.a_valider = request.form.get("a_valider") == "on"
            stades = [c.l_ini or 0, c.l_dev or 0, c.l_mid or 0, c.l_fin or 0]
            if c.cycle_j and sum(stades) != c.cycle_j:
                erreurs.append(f"La somme des stades ({sum(stades)} j) doit être égale au cycle ({c.cycle_j} j).")
            if erreurs:
                db.session.rollback()
                for e in erreurs:
                    flash(e, "danger")
            else:
                db.session.commit()
                flash(f"Référentiel « {c.nom} » enregistré.", "success")
        elif action == "variete":
            nom = (request.form.get("v_nom") or "").strip()
            if nom:
                v = (db.session.query(VarieteRef).filter_by(culture_id=c.id, nom=nom).first()
                     or VarieteRef(culture_id=c.id, nom=nom))
                for k, typ in (("cycle_j", int), ("rdt_potentiel_t", float)):
                    val = (request.form.get(f"v_{k}") or "").strip().replace(",", ".")
                    try:
                        setattr(v, k, typ(float(val)) if val else None)
                    except ValueError:
                        pass
                v.notes = (request.form.get("v_notes") or "").strip() or None
                v.source = (request.form.get("v_source") or "").strip() or None
                db.session.add(v)
                db.session.commit()
                flash(f"Variété « {nom} » enregistrée.", "success")
        elif action == "supprimer_variete":
            v = db.session.get(VarieteRef, request.form.get("vid", type=int))
            if v and v.culture_id == c.id:
                db.session.delete(v)
                db.session.commit()
                flash("Variété supprimée.", "success")
        return redirect(url_for("referentiel.culture", cid=cid))
    return render_template("referentiel/culture.html", c=c, peut=peut,
                           varietes=c.varietes.order_by(VarieteRef.nom).all(),
                           observe=service.pratiques_observees(c))


@referentiel_bp.route("/parcelle/<int:pid>")
@login_required
def parcelle(pid):
    p = db.session.get(Parcelle, pid)
    if not p or not peut_voir_parcelle(current_user, pid):
        flash("Parcelle introuvable ou hors de votre périmètre.", "warning")
        return redirect(url_for("referentiel.liste"))
    c = service.culture_ref(p.culture)
    if c is None:
        flash(f"La culture « {p.culture or '—'} » n'est pas encore dans le référentiel.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid))
    demande = None
    if request.args.get("debut"):
        try:
            demande = datetime.strptime(request.args["debut"], "%Y-%m-%d").date()
        except ValueError:
            flash("Date de début invalide.", "warning")
    debut, origine = service.date_debut(p, demande)
    irrigation = request.args.get("irrigation") or service.EFFICIENCE_DEFAUT
    if irrigation not in service.EFFICIENCES:
        irrigation = service.EFFICIENCE_DEFAUT
    sol = _sol(pid)
    eau = (service.besoins_eau(c, debut, p.surface_ha, pid, irrigation)
           if c.systeme == "Irrigué" and c.kc_ini is not None and c.cycle_j else None)
    return render_template(
        "referentiel/parcelle.html", p=p, c=c, debut=debut, origine=origine, sol=sol,
        calendrier=service.calendrier(c, debut),
        fumure=service.plan_fumure(c, debut, p.surface_ha, sol),
        eau=eau, irrigation=irrigation, efficiences=service.EFFICIENCES,
        observe=service.pratiques_observees(c),
        suivi=fiches.suivi(p, c, debut),
        doses=fiches.phases_irrigation(c, debut, fiches.SYSTEME_DE_MODE.get(irrigation), p.surface_ha),
        ennemis=c.ravageurs.all(), peut_suivre=current_user.niveau >= 2,
        aujourdhui=date.today())


# --------------------------------------------------------------------------
# Espace d'administration : rendements potentiels par spéculation et variété
# --------------------------------------------------------------------------
def _float(v):
    v = (v or "").strip().replace(",", ".")
    if v == "":
        return None
    x = float(v)
    if x < 0 or x > 200:
        raise ValueError
    return x


@referentiel_bp.route("/potentiels", methods=["GET", "POST"])
@login_required
@niveau_minimum(NIVEAU_EDITION)
def potentiels():
    from app.models.referentiel import PotentielParcelle
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "enregistrer":
                erreurs = 0
                for c in db.session.query(CultureRef).all():
                    if f"c_{c.id}" in request.form:
                        try:
                            c.rdt_potentiel_t = _float(request.form.get(f"c_{c.id}"))
                        except ValueError:
                            erreurs += 1
                for v in db.session.query(VarieteRef).all():
                    if f"v_{v.id}" in request.form:
                        try:
                            v.rdt_potentiel_t = _float(request.form.get(f"v_{v.id}"))
                            cyc = (request.form.get(f"vc_{v.id}") or "").strip()
                            v.cycle_j = int(float(cyc)) if cyc else None
                        except ValueError:
                            erreurs += 1
                if erreurs:
                    db.session.rollback()
                    flash(f"{erreurs} valeur(s) invalide(s) : rien n'a été enregistré "
                          "(potentiel en t/ha entre 0 et 200, cycle en jours).", "danger")
                    return redirect(url_for("referentiel.potentiels"))
                db.session.commit()
                msg = "Potentiels enregistrés."
                if request.form.get("appliquer") == "on":
                    s = service.appliquer_potentiels()
                    msg += (f" Appliqués aux parcelles : {s['modifiees']} modifiée(s) "
                            f"({s['variete']} par variété, {s['speculation']} par spéculation, "
                            f"{s['manuel']} saisies manuelles conservées).")
                flash(msg, "success")

            elif action == "ajouter_culture":
                nom = (request.form.get("nom") or "").strip()
                if not nom:
                    raise ValueError("Donnez le nom de la spéculation.")
                if service.culture_ref(nom) is not None:
                    raise ValueError(f"« {nom} » existe déjà dans le référentiel (nom ou synonyme).")
                c = CultureRef(nom=nom[:60], systeme=request.form.get("systeme") or None,
                               famille=(request.form.get("famille") or "").strip() or None,
                               synonymes=(request.form.get("synonymes") or "").strip() or nom.lower(),
                               rdt_potentiel_t=_float(request.form.get("potentiel")),
                               source=f"Saisie {current_user.nom if hasattr(current_user, 'nom') else ''} "
                                      f"le {datetime.utcnow():%d/%m/%Y}".strip(),
                               a_valider=True)
                db.session.add(c)
                db.session.commit()
                flash(f"Spéculation « {c.nom} » ajoutée. Complétez son cycle et sa fumure depuis sa fiche "
                      "pour le calendrier et les besoins en eau.", "success")

            elif action == "ajouter_variete":
                c = db.session.get(CultureRef, request.form.get("culture_id", type=int))
                nom = (request.form.get("nom") or "").strip()
                if c is None or not nom:
                    raise ValueError("Choisissez la spéculation et donnez le nom de la variété.")
                if any(service._norm(v.nom) == service._norm(nom) for v in c.varietes):
                    raise ValueError(f"La variété « {nom} » existe déjà pour {c.nom}.")
                cyc = (request.form.get("cycle") or "").strip()
                db.session.add(VarieteRef(culture_id=c.id, nom=nom[:80],
                                          rdt_potentiel_t=_float(request.form.get("potentiel")),
                                          cycle_j=int(float(cyc)) if cyc else None,
                                          notes=(request.form.get("notes") or "").strip() or None,
                                          source=(request.form.get("source") or "").strip() or None))
                db.session.commit()
                flash(f"Variété « {nom} » ajoutée à {c.nom}.", "success")

            elif action == "appliquer":
                s = service.appliquer_potentiels()
                flash(f"Potentiels appliqués à toutes les parcelles : {s['modifiees']} modifiée(s) ; "
                      f"{s['variete']} par variété, {s['speculation']} par potentiel de la spéculation, "
                      f"{s['manuel']} saisie(s) manuelle(s) conservée(s), "
                      f"{s['sans_potentiel']} sans potentiel renseigné, "
                      f"{s['sans_referentiel']} de culture hors référentiel.", "success")
                if s["cultures_absentes"]:
                    flash("Cultures hors référentiel : " + ", ".join(
                        f"{k} ({n})" for k, n in s["cultures_absentes"][:12]), "warning")

            elif action == "supprimer_culture":
                c = db.session.get(CultureRef, request.form.get("culture_id", type=int))
                par_c, _pv = service.comptes_parcelles()
                if c is None:
                    raise ValueError("Spéculation introuvable.")
                if par_c.get(c.id):
                    raise ValueError(f"« {c.nom} » est utilisée par {par_c[c.id]} parcelle(s) : "
                                     "suppression impossible.")
                db.session.query(PotentielParcelle).filter_by(culture_id=c.id).delete()
                db.session.delete(c)
                db.session.commit()
                flash(f"Spéculation « {c.nom} » supprimée.", "success")

            elif action == "supprimer_variete":
                v = db.session.get(VarieteRef, request.form.get("variete_id", type=int))
                if v is not None:
                    nom = v.nom
                    db.session.delete(v)
                    db.session.commit()
                    flash(f"Variété « {nom} » supprimée. Relancez l'application aux parcelles "
                          "pour mettre à jour leurs potentiels.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc) or "Valeur invalide.", "danger")
        return redirect(url_for("referentiel.potentiels"))

    cultures = (db.session.query(CultureRef)
                .order_by(CultureRef.systeme.desc(), CultureRef.nom).all())
    par_c, par_v = service.comptes_parcelles()
    origines = dict(db.session.query(PotentielParcelle.origine, func.count())
                    .group_by(PotentielParcelle.origine).all())
    return render_template("referentiel/potentiels.html", cultures=cultures, par_c=par_c, par_v=par_v,
                           non_ref=service.varietes_non_referencees(), origines=origines,
                           n_parcelles=db.session.query(func.count(Parcelle.id)).scalar())


# --------------------------------------------------------------------------
# Suivi de l'itinéraire technique sur la parcelle
# --------------------------------------------------------------------------
def _parcelle_modifiable(pid):
    p = db.session.get(Parcelle, pid)
    if not p or not peut_voir_parcelle(current_user, pid):
        flash("Parcelle introuvable ou hors de votre périmètre.", "warning")
        return None
    if current_user.niveau < 2:
        flash("Le suivi des opérations est réservé aux conseillers (CAR) et à l'encadrement.", "warning")
        return None
    return p


def _date(v):
    try:
        return datetime.strptime(v or "", "%Y-%m-%d").date()
    except ValueError:
        return None


@referentiel_bp.route("/parcelle/<int:pid>/debut", methods=["POST"])
@login_required
def enregistrer_debut(pid):
    """Date de semis / repiquage de la campagne : point de départ de l'itinéraire."""
    from app.models.enquete import PratiqueCampagne
    p = _parcelle_modifiable(pid)
    if p is None:
        return redirect(url_for("referentiel.parcelle", pid=pid))
    d = _date(request.form.get("debut"))
    if d is None or d > date.today().replace(year=date.today().year + 1):
        flash("Date de semis / repiquage invalide.", "danger")
        return redirect(url_for("referentiel.parcelle", pid=pid))
    pr = db.session.query(PratiqueCampagne).filter_by(parcelle_id=pid, annee=d.year).first()
    if pr is None:
        pr = PratiqueCampagne(parcelle_id=pid, annee=d.year, culture=p.culture, source="Saisie conseiller")
        db.session.add(pr)
    pr.date_semis = d
    db.session.commit()
    flash(f"Date de {'repiquage' if (service.culture_ref(p.culture) and service.culture_ref(p.culture).reference_j0 == 'repiquage') else 'semis'} "
          f"enregistrée : {d:%d/%m/%Y}. L'itinéraire de la parcelle est calé sur cette date.", "success")
    return redirect(url_for("referentiel.parcelle", pid=pid) + "#itineraire")


@referentiel_bp.route("/parcelle/<int:pid>/operation", methods=["POST"])
@login_required
def enregistrer_operation(pid):
    from app.models.referentiel import ItineraireRef, OperationParcelle
    p = _parcelle_modifiable(pid)
    if p is None:
        return redirect(url_for("referentiel.parcelle", pid=pid))
    o = db.session.get(ItineraireRef, request.form.get("itineraire_id", type=int))
    annee = request.form.get("annee", type=int)
    if o is None or not annee:
        flash("Opération introuvable.", "warning")
        return redirect(url_for("referentiel.parcelle", pid=pid))
    op = db.session.query(OperationParcelle).filter_by(parcelle_id=pid, annee=annee, itineraire_id=o.id).first()
    if request.form.get("action") == "annuler":
        if op is not None:
            db.session.delete(op)
            db.session.commit()
            flash(f"« {o.libelle} » remise à faire.", "success")
    else:
        d = _date(request.form.get("date_realisee")) or date.today()
        if d > date.today():
            flash("La date de réalisation ne peut pas être dans le futur.", "danger")
            return redirect(url_for("referentiel.parcelle", pid=pid) + "#itineraire")
        q = (request.form.get("quantite") or "").strip().replace(",", ".")
        if op is None:
            op = OperationParcelle(parcelle_id=pid, annee=annee, itineraire_id=o.id)
            db.session.add(op)
        op.date_realisee = d
        try:
            op.quantite_kg = float(q) if q else None
        except ValueError:
            op.quantite_kg = None
        op.note = (request.form.get("note") or "").strip()[:255] or None
        op.user_id = current_user.id
        db.session.commit()
        flash(f"« {o.libelle} » réalisée le {d:%d/%m/%Y}.", "success")
    return redirect(url_for("referentiel.parcelle", pid=pid) + "#itineraire")


@referentiel_bp.route("/suivi")
@login_required
def suivi():
    """Parcelles avec des opérations en retard ou à faire cette semaine (périmètre de l'utilisateur)."""
    ids = parcelle_ids(current_user)
    lignes = fiches.operations_en_retard(ids)
    filtre = request.args.get("filtre", "tout")
    if filtre == "retard":
        lignes = [x for x in lignes if x[3]["comptes"].get("En retard")]
    return render_template("referentiel/suivi.html", lignes=lignes[:500], total=len(lignes), filtre=filtre,
                           tolerance=fiches.TOLERANCE_J)


# --------------------------------------------------------------------------
# Fiches techniques : dépôt, lecture (OCR), validation
# --------------------------------------------------------------------------
@referentiel_bp.route("/fiches", methods=["GET", "POST"])
@login_required
def fiches_liste():
    from app.models.referentiel import FicheTechnique
    if request.method == "POST":
        if current_user.niveau < NIVEAU_EDITION:
            abort(403)
        fichiers = [f for f in request.files.getlist("fichiers") if f and f.filename]
        if not fichiers:
            flash("Choisissez au moins un fichier.", "warning")
            return redirect(url_for("referentiel.fiches_liste"))
        deposees = []
        for f in fichiers:
            try:
                fi = fiches.deposer(f, current_user.id)
                deposees.append(fi)
                if fi.statut == "Illisible":
                    flash(f"« {fi.fichier_nom} » : {fiches.donnees(fi).get('erreur')}", "danger")
            except Exception as exc:          # fichier refusé : on continue avec les autres
                db.session.rollback()
                flash(f"« {f.filename} » : {exc}", "danger")
        lues = [f for f in deposees if f.statut != "Illisible"]
        if lues:
            flash(f"{len(lues)} fiche(s) lue(s). Vérifiez les valeurs extraites puis validez-les.", "success")
        if len(lues) == 1:
            return redirect(url_for("referentiel.fiche", fid=lues[0].id))
        return redirect(url_for("referentiel.fiches_liste"))
    liste = db.session.query(FicheTechnique).order_by(FicheTechnique.depose_le.desc()).all()
    cultures = db.session.query(CultureRef).order_by(CultureRef.nom).all()
    return render_template("referentiel/fiches.html", fiches=liste, cultures=cultures,
                           ocr=fiches.lecture.ocr_disponible(), peut=current_user.niveau >= NIVEAU_EDITION)


@referentiel_bp.route("/fiches/<int:fid>", methods=["GET", "POST"])
@login_required
def fiche(fid):
    from app.models.referentiel import FicheTechnique
    f = db.session.get(FicheTechnique, fid) or abort(404)
    peut = current_user.niveau >= NIVEAU_EDITION
    r = fiches.donnees(f)
    if request.method == "POST":
        if not peut:
            abort(403)
        action = request.form.get("action")
        if action == "relire":
            fiches.analyser(f)
            f.statut = "À valider"
            db.session.commit()
            flash("Fiche relue : les valeurs proposées ont été recalculées à partir du texte.", "success")
            return redirect(url_for("referentiel.fiche", fid=fid))
        try:
            r = fiches.lire_formulaire(request.form, r)
        except ValueError as exc:
            flash(f"Rien n'a été enregistré : {exc}", "danger")
            return redirect(url_for("referentiel.fiche", fid=fid))
        if action == "appliquer":
            try:
                res = fiches.appliquer(f, r, current_user.id)
            except ValueError as exc:
                db.session.rollback()
                flash(str(exc), "danger")
                return redirect(url_for("referentiel.fiche", fid=fid))
            msg = (f"Fiche validée et appliquée au référentiel « {res['culture']} »"
                   f"{' (nouvelle spéculation)' if res['nouvelle'] else ''} : {res['itineraire']} opération(s) "
                   f"d'itinéraire, {res['ennemis']} ennemi(s), {res['irrigation']} dose(s) d'irrigation, "
                   f"{res['varietes']} variété(s) ajoutée(s).")
            if "potentiels" in res:
                msg += f" Potentiel modifié : {res['potentiels']} parcelle(s) mise(s) à jour."
            flash(msg, "success")
            c = db.session.get(CultureRef, r["culture_id"])
            return redirect(url_for("referentiel.culture", cid=c.id))
        f.culture_id = r.get("culture_id") or None
        f.extraction = fiches.json.dumps(r, ensure_ascii=False, default=str)
        db.session.commit()
        flash("Corrections enregistrées (fiche non encore appliquée au référentiel).", "success")
        return redirect(url_for("referentiel.fiche", fid=fid))
    cultures = db.session.query(CultureRef).order_by(CultureRef.nom).all()
    c = f.culture
    cle = lambda s: fiches.extraction.norm(s).replace(" ", "")          # noqa: E731
    existantes = {cle(v.nom) for v in c.varietes} if c else set()
    return render_template("referentiel/fiche.html", f=f, r=r, c=c, cultures=cultures, peut=peut,
                           categories=fiches.CATEGORIES, types=fiches.TYPES_ENNEMI, systemes=fiches.SYSTEMES,
                           cle=cle, existantes=existantes)


@referentiel_bp.route("/fiches/<int:fid>/fichier")
@login_required
def fiche_fichier(fid):
    from app.models.referentiel import FicheTechnique
    f = db.session.get(FicheTechnique, fid) or abort(404)
    p = fiches.chemin(f)
    if not fiches.os.path.exists(p):
        flash("Le fichier d'origine n'est plus sur le serveur.", "warning")
        return redirect(url_for("referentiel.fiche", fid=fid))
    return send_file(p, as_attachment=True, download_name=f.fichier_nom)


@referentiel_bp.route("/fiches/<int:fid>/supprimer", methods=["POST"])
@login_required
@niveau_minimum(NIVEAU_EDITION)
def fiche_supprimer(fid):
    from app.models.referentiel import FicheTechnique
    f = db.session.get(FicheTechnique, fid) or abort(404)
    if f.statut == "Appliquée":
        flash("Cette fiche alimente le référentiel : déposez et validez la nouvelle version, "
              "elle la remplacera.", "warning")
        return redirect(url_for("referentiel.fiche", fid=fid))
    try:
        os = fiches.os
        if os.path.exists(fiches.chemin(f)):
            os.remove(fiches.chemin(f))
    except OSError:
        pass
    db.session.delete(f)
    db.session.commit()
    flash("Fiche supprimée.", "success")
    return redirect(url_for("referentiel.fiches_liste"))
