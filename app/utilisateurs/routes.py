from flask import (
    Blueprint, render_template, request, redirect, url_for, flash
)
from flask_login import login_required, current_user

from app.extensions import db
from app.models.user import User
from app.models.role import Role
from app.models.zone import Zone, Region
from app.models.parcelle import Parcelle
from app.models.organisation import OrganisationProducteur
from app.auth.decorators import role_required

utilisateurs_bp = Blueprint("utilisateurs", __name__, url_prefix="/utilisateurs")


def _i(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _dedupe(rows):
    seen = {}
    for (v,) in rows:
        if not v:
            continue
        k = v.strip().lower()
        if k and k not in seen:
            seen[k] = v.strip()
    return sorted(seen.values())


def _refs():
    return {
        "roles": Role.query.order_by(Role.niveau.desc()).all(),
        "zones": Zone.query.order_by(Zone.nom).all(),
        "regions": Region.query.order_by(Region.nom).all(),
        "organisations": OrganisationProducteur.query.order_by(OrganisationProducteur.nom).all(),
        "departements": _dedupe(db.session.query(Parcelle.departement)
                                .filter(Parcelle.departement.isnot(None)).distinct().all()),
        "communes": _dedupe(db.session.query(Parcelle.commune)
                            .filter(Parcelle.commune.isnot(None)).distinct().all()),
    }


def _appliquer_perimetre(u):
    """Lit le périmètre depuis le formulaire et l'applique à l'utilisateur."""
    u.organisations = request.form.getlist("organisations")
    u.departements = request.form.getlist("departements")
    u.communes = request.form.getlist("communes")


@utilisateurs_bp.route("/maintenance")
@login_required
@role_required("ADMIN")
def maintenance():
    from app.models.parcelle import Parcelle
    from app.models.producteur import Producteur
    from app.models.indice import IndiceVegetation
    from app.models.historique import HistoriqueCultural
    counts = {
        "parcelles": Parcelle.query.count(),
        "producteurs": Producteur.query.count(),
        "organisations": OrganisationProducteur.query.count(),
        "indices": IndiceVegetation.query.count(),
        "historique": HistoriqueCultural.query.count(),
    }
    return render_template("utilisateurs/maintenance.html", counts=counts)


@utilisateurs_bp.route("/maintenance/reset", methods=["POST"])
@login_required
@role_required("ADMIN")
def reset():
    from app.models.parcelle import Parcelle
    from app.models.producteur import Producteur
    from app.models.indice import IndiceVegetation
    from app.models.historique import HistoriqueCultural
    try:
        from app.models.financement import FinancementOP
    except Exception:
        FinancementOP = None
    try:
        from app.models.suivi import SuiviTerrain
    except Exception:
        SuiviTerrain = None
    try:
        from app.models.rendement import RendementReference
    except Exception:
        RendementReference = None

    if not request.form.get("confirmer"):
        flash("Veuillez cocher la case de confirmation.", "warning")
        return redirect(url_for("utilisateurs.maintenance"))

    cible = request.form.get("cible")
    faits = []

    def purge_parcelles():
        faits.append(("Indices", db.session.query(IndiceVegetation).delete()))
        faits.append(("Historique", db.session.query(HistoriqueCultural).delete()))
        if SuiviTerrain is not None:
            faits.append(("Suivi terrain", db.session.query(SuiviTerrain).delete()))
        if RendementReference is not None:
            faits.append(("Rendements réf.", db.session.query(RendementReference).delete()))
        faits.append(("Parcelles", db.session.query(Parcelle).delete()))

    if cible == "indices":
        faits.append(("Indices", db.session.query(IndiceVegetation).delete()))
    elif cible == "historique":
        faits.append(("Historique", db.session.query(HistoriqueCultural).delete()))
    elif cible == "parcelles":
        purge_parcelles()
    elif cible == "parcelles_prod":
        purge_parcelles()
        faits.append(("Producteurs", db.session.query(Producteur).delete()))
    elif cible == "tout":
        User.query.filter(User.organisation_id.isnot(None)).update(
            {User.organisation_id: None}, synchronize_session=False)
        purge_parcelles()
        faits.append(("Producteurs", db.session.query(Producteur).delete()))
        if FinancementOP is not None:   # référence les organisations -> à purger avant
            faits.append(("Financements", db.session.query(FinancementOP).delete()))
        faits.append(("Organisations", db.session.query(OrganisationProducteur).delete()))
    else:
        flash("Cible de réinitialisation inconnue.", "warning")
        return redirect(url_for("utilisateurs.maintenance"))

    db.session.commit()
    resume = ", ".join(f"{lib} : {n}" for lib, n in faits)
    flash(f"Réinitialisation effectuée — {resume}.", "success")
    return redirect(url_for("utilisateurs.maintenance"))


@utilisateurs_bp.route("/")
@login_required
@role_required("ADMIN")
def liste():
    users = User.query.order_by(User.nom).all()
    return render_template("utilisateurs/liste.html", users=users)


@utilisateurs_bp.route("/nouveau", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def creer():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        nom = (request.form.get("nom") or "").strip()
        password = request.form.get("password") or ""
        role_id = _i(request.form.get("role_id"))

        if not email or not nom or not password or not role_id:
            flash("Nom, e-mail, mot de passe et rôle sont obligatoires.", "warning")
            return render_template("utilisateurs/form.html", u=None, **_refs())
        if User.query.filter_by(email=email).first():
            flash("Un utilisateur avec cet e-mail existe déjà.", "danger")
            return render_template("utilisateurs/form.html", u=None, **_refs())

        u = User(
            nom=nom, email=email,
            telephone=(request.form.get("telephone") or None),
            role_id=role_id,
            zone_id=_i(request.form.get("zone_id")),
            region_id=_i(request.form.get("region_id")),
            actif=bool(request.form.get("actif")),
        )
        u.set_password(password)
        _appliquer_perimetre(u)
        db.session.add(u)
        db.session.commit()
        flash("Utilisateur créé.", "success")
        return redirect(url_for("utilisateurs.liste"))

    return render_template("utilisateurs/form.html", u=None, **_refs())


@utilisateurs_bp.route("/<int:uid>/modifier", methods=["GET", "POST"])
@login_required
@role_required("ADMIN")
def modifier(uid):
    u = db.session.get(User, uid)
    if not u:
        flash("Utilisateur introuvable.", "warning")
        return redirect(url_for("utilisateurs.liste"))

    if request.method == "POST":
        u.nom = (request.form.get("nom") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        autre = User.query.filter_by(email=email).first()
        if autre and autre.id != u.id:
            flash("Cet e-mail est déjà utilisé par un autre compte.", "danger")
            return render_template("utilisateurs/form.html", u=u, **_refs())
        u.email = email
        u.telephone = request.form.get("telephone") or None
        u.role_id = _i(request.form.get("role_id"))
        u.zone_id = _i(request.form.get("zone_id"))
        u.region_id = _i(request.form.get("region_id"))
        u.actif = bool(request.form.get("actif"))
        nouveau_mdp = request.form.get("password")
        if nouveau_mdp:
            u.set_password(nouveau_mdp)
        _appliquer_perimetre(u)
        db.session.commit()
        flash("Utilisateur mis à jour.", "success")
        return redirect(url_for("utilisateurs.liste"))

    return render_template("utilisateurs/form.html", u=u, **_refs())


@utilisateurs_bp.route("/<int:uid>/supprimer", methods=["POST"])
@login_required
@role_required("ADMIN")
def supprimer(uid):
    u = db.session.get(User, uid)
    if not u:
        flash("Utilisateur introuvable.", "warning")
    elif u.id == current_user.id:
        flash("Vous ne pouvez pas supprimer votre propre compte.", "warning")
    else:
        db.session.delete(u)
        db.session.commit()
        flash("Utilisateur supprimé.", "info")
    return redirect(url_for("utilisateurs.liste"))
