# -*- coding: utf-8 -*-
from flask import Blueprint, render_template
from flask_login import login_required, current_user

from app.models.producteur import Producteur
from app.auth.decorators import niveau_minimum
from app.scoping import producteur_ids, organisation_ids
from .calcul import score_producteur, score_op
from app.extensions import db
from app.models.rendement import RendementReference
from sqlalchemy import func

bancabilite_bp = Blueprint("bancabilite", __name__, url_prefix="/bancabilite")


def _contexte_indicatif():
    """Score peu discriminant si une seule campagne et/ou rendements DAPSA."""
    n_annees = db.session.query(func.count(func.distinct(RendementReference.annee))).scalar() or 0
    dapsa = db.session.query(RendementReference.id).filter(
        RendementReference.source.isnot(None),
        ~RendementReference.source.ilike("%terrain%")).first() is not None
    return {"indicatif": (n_annees <= 1) or dapsa, "n_annees": n_annees, "dapsa": dapsa}




@bancabilite_bp.route("/")
@login_required
@niveau_minimum(2)
def liste():
    q = Producteur.query
    _pids = producteur_ids(current_user)
    if _pids is not None:
        q = q.filter(Producteur.id.in_(_pids))
    lignes = []
    for prod in q.all():
        s = score_producteur(prod)
        lignes.append({"prod": prod, "s": s})
    # tri : évaluables d'abord (score décroissant), puis non évaluables
    lignes.sort(key=lambda x: (x["s"]["total"] is None, -(x["s"]["total"] or 0)))
    return render_template("bancabilite/liste.html", lignes=lignes, ctx=_contexte_indicatif())


@bancabilite_bp.route("/op")
@login_required
@niveau_minimum(2)
def par_op():
    from app.models.organisation import OrganisationProducteur
    q = OrganisationProducteur.query
    _oids = organisation_ids(current_user)
    if _oids is not None:
        q = q.filter(OrganisationProducteur.id.in_(_oids))
    lignes = []
    for op in q.all():
        lignes.append({"op": op, "s": score_op(op)})
    lignes.sort(key=lambda x: (x["s"]["total"] is None, -(x["s"]["total"] or 0)))
    return render_template("bancabilite/op.html", lignes=lignes, ctx=_contexte_indicatif())
