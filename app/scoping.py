"""
Cloisonnement des données selon le périmètre de l'utilisateur.

- ADMIN, DG, Consultation (LECTEUR), Conseiller : accès global (None)
- DZ, CDCAR : limités à leurs départements
- CAR : limité à ses communes
- OP : limité à son organisation

Chaque fonction renvoie :
  None  -> aucun filtre (accès global)
  []    -> périmètre vide (ne voit rien)
  [..]  -> liste d'identifiants autorisés
"""
from sqlalchemy import func

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.producteur import Producteur

GLOBAL = {"ADMIN", "DG", "LECTEUR", "CONSEILLER"}


def _deps(user):
    return [d.strip().lower() for d in (user.departements or []) if d]


def _coms(user):
    return [c.strip().lower() for c in (user.communes or []) if c]


def parcelle_ids(user):
    code = user.role.code if user.role else None
    if code is None or code in GLOBAL:
        return None
    q = db.session.query(Parcelle.id)
    if code in ("DZ", "CDCAR"):
        deps = _deps(user)
        if not deps:
            return []
        q = q.filter(func.lower(func.trim(Parcelle.departement)).in_(deps))
    elif code in ("CAR", "ANIMATEUR"):
        coms = _coms(user)
        if not coms:
            return []
        q = q.filter(func.lower(func.trim(Parcelle.commune)).in_(coms))
    elif code == "OP":
        orgs = user.organisations
        if not orgs:
            return []
        q = (q.join(Producteur, Producteur.id == Parcelle.producteur_id)
               .filter(Producteur.organisation_id.in_(orgs)))
    else:
        return None
    return [pid for (pid,) in q.all()]


def producteur_ids(user):
    code = user.role.code if user.role else None
    if code is None or code in GLOBAL:
        return None
    if code == "OP":
        orgs = user.organisations
        if not orgs:
            return []
        return [pid for (pid,) in db.session.query(Producteur.id)
                .filter(Producteur.organisation_id.in_(orgs)).all()]
    pids = parcelle_ids(user)
    if pids is None:
        return None
    if not pids:
        return []
    return [x for (x,) in db.session.query(Parcelle.producteur_id)
            .filter(Parcelle.id.in_(pids), Parcelle.producteur_id.isnot(None))
            .distinct().all()]


def organisation_ids(user):
    code = user.role.code if user.role else None
    if code is None or code in GLOBAL:
        return None
    if code == "OP":
        return user.organisations
    prod = producteur_ids(user)
    if prod is None:
        return None
    if not prod:
        return []
    return [o for (o,) in db.session.query(Producteur.organisation_id)
            .filter(Producteur.id.in_(prod), Producteur.organisation_id.isnot(None))
            .distinct().all()]


def filtrer_parcelles(query, user):
    ids = parcelle_ids(user)
    if ids is None:
        return query
    return query.filter(Parcelle.id.in_(ids))


def peut_voir_parcelle(user, parcelle_id):
    ids = parcelle_ids(user)
    return ids is None or parcelle_id in ids


def peut_voir_producteur(user, producteur_id):
    ids = producteur_ids(user)
    return ids is None or producteur_id in ids
