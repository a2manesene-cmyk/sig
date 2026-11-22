from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import median

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.auth.decorators import niveau_minimum
from app.models.parcelle import Parcelle
from app.models.climat import CelluleClimat, ParcelleCellule, ClimatJournalier, SaisonClimat
from app.scoping import parcelle_ids, peut_voir_parcelle
from app.climat import agro, service

climat_bp = Blueprint("climat", __name__, url_prefix="/climat")
NIVEAU_IMPORT = 4

MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def jour_mois(d):
    return f"{d.day} {MOIS[d.month - 1]}" if d else "—"


@climat_bp.app_template_filter("jour_mois")
def _f_jour_mois(d):
    return jour_mois(d)


def _texture_parcelle(pid):
    try:
        from app.models.sol import AnalyseSol
        a = db.session.query(AnalyseSol.texture).filter_by(parcelle_id=pid).scalar()
        return a
    except Exception:
        return None


def resume_parcelle(pid, annee=None):
    """Indicateurs climatiques de la parcelle (saison `annee` ou la plus récente) + moyennes."""
    pc = db.session.get(ParcelleCellule, pid)
    if pc is None:
        return None
    q = db.session.query(SaisonClimat).filter_by(cellule_id=pc.cellule_id)
    s = (q.filter_by(annee=annee).first() if annee
         else q.order_by(SaisonClimat.annee.desc()).first())
    if s is None:
        return {"cellule": pc.cellule, "saison": None, "moy": None}
    moy = service.moyennes([pc.cellule_id], sauf_annee=s.annee)
    return {"cellule": pc.cellule, "saison": s, "moy": moy}


def _resume_sur(pid, annee=None):
    """Version tolérante pour la fiche parcelle (tables du module pas encore créées)."""
    try:
        with db.session.begin_nested():
            return resume_parcelle(pid, annee)
    except Exception:
        return None


@climat_bp.app_context_processor
def _ctx():
    return {"climat_parcelle": _resume_sur, "date_du_jour": service.date_du_jour}


# --------------------------------------------------------------------------
@climat_bp.route("/")
@login_required
def tableau():
    annees = [a for (a,) in db.session.query(SaisonClimat.annee).distinct()
              .order_by(SaisonClimat.annee.desc())]
    annee = request.args.get("annee", type=int) or (annees[0] if annees else date.today().year)

    q = (db.session.query(Parcelle.commune, Parcelle.departement, ParcelleCellule.cellule_id)
         .join(ParcelleCellule, ParcelleCellule.parcelle_id == Parcelle.id))
    ids = parcelle_ids(current_user)
    if ids is not None:
        q = q.filter(Parcelle.id.in_(ids))
    par_commune = defaultdict(lambda: {"n": 0, "cellules": set(), "dep": None})
    for commune, dep, cid in q.all():
        c = par_commune[(commune or "—").strip().title()]
        c["n"] += 1
        c["cellules"].add(cid)
        c["dep"] = (dep or "").strip().title()

    toutes = {cid for c in par_commune.values() for cid in c["cellules"]}
    saisons = {s.cellule_id: s for s in db.session.query(SaisonClimat)
               .filter(SaisonClimat.annee == annee, SaisonClimat.cellule_id.in_(toutes))} if toutes else {}

    def med(vals):
        vals = [v for v in vals if v is not None]
        return median(vals) if vals else None

    lignes = []
    for commune, c in par_commune.items():
        ss = [saisons[cid] for cid in c["cellules"] if cid in saisons]
        if not ss:
            lignes.append({"commune": commune, "dep": c["dep"], "n": c["n"], "s": None})
            continue
        debut_j = med([s.debut.timetuple().tm_yday for s in ss if s.debut])
        fin_j = med([s.fin.timetuple().tm_yday for s in ss if s.fin])
        moy = service.moyennes(list(c["cellules"]), sauf_annee=annee)
        cumul = med([s.cumul_mai_oct for s in ss])
        lignes.append({
            "commune": commune, "dep": c["dep"], "n": c["n"], "moy": moy,
            "s": {
                "debut": service.date_du_jour(annee, debut_j),
                "fin": service.date_du_jour(annee, fin_j),
                "longueur": med([s.longueur_j for s in ss]),
                "cumul": cumul,
                "seq": max((s.seq_seche_max_j or 0) for s in ss),
                "seq10": max((s.nb_seq_seches_10j or 0) for s in ss),
                "faux": max((s.faux_departs or 0) for s in ss),
                "complete": all(s.complete for s in ss),
                "derniere": max(s.derniere_donnee for s in ss),
                "ecart": (cumul / moy["cumul_mai_oct"] - 1) * 100
                         if moy and moy["cumul_mai_oct"] and cumul is not None else None,
                "ecart_debut": (debut_j - moy["debut_jour"])
                               if moy and moy["debut_jour"] and debut_j else None,
            },
        })
    lignes.sort(key=lambda l: (l["dep"] or "", l["commune"]))
    return render_template("climat/tableau.html", annee=annee, annees=annees, lignes=lignes,
                           couv=service.couverture())


@climat_bp.route("/parcelle/<int:pid>")
@login_required
def parcelle(pid):
    p = db.session.get(Parcelle, pid)
    if not p or not peut_voir_parcelle(current_user, pid):
        flash("Parcelle introuvable ou hors de votre périmètre.", "warning")
        return redirect(url_for("climat.tableau"))
    pc = db.session.get(ParcelleCellule, pid)
    if pc is None:
        flash("Cette parcelle n'est pas encore rattachée à la grille climatique.", "warning")
        return redirect(url_for("parcelles.fiche", pid=pid))
    annees = [a for (a,) in db.session.query(SaisonClimat.annee)
              .filter_by(cellule_id=pc.cellule_id).order_by(SaisonClimat.annee.desc())]
    annee = request.args.get("annee", type=int) or (annees[0] if annees else date.today().year)

    # Saison recalculée avec la réserve utile du sol de la parcelle
    texture = _texture_parcelle(pid)
    ru = agro.ru_texture(texture) if texture else (pc.cellule.ru_mm or agro.RU_DEFAUT)
    pluie, etp = service.series_cellule(pc.cellule_id, annee)
    ind = agro.indicateurs_saison(pluie, etp, annee, ru) if pluie else None
    moy = service.moyennes([pc.cellule_id], sauf_annee=annee)

    # Graphique : pluie journalière et cumul (1er avril -> 30 novembre) + cumul moyen des autres années
    d0, d1 = date(annee, 4, 1), date(annee, 11, 30)
    jours, pl, cumul, c = [], [], [], 0.0
    d = d0
    while d <= d1:
        jours.append(d.isoformat())
        v = pluie.get(d)
        pl.append(v)
        if v is not None:
            c += v
        cumul.append(round(c, 1) if (v is not None or d <= max(pluie, default=d0)) else None)
        d += timedelta(1)
    autres = (db.session.query(ClimatJournalier.date, ClimatJournalier.pluie_mm)
              .filter(ClimatJournalier.cellule_id == pc.cellule_id,
                      func.extract("year", ClimatJournalier.date) != annee,
                      func.extract("month", ClimatJournalier.date).between(4, 11)).all())
    par_an = defaultdict(dict)
    for dd, v in autres:
        if v is not None:
            par_an[dd.year][(dd.month, dd.day)] = v
    cumul_moy = None
    ans_complets = [a for a, s in par_an.items() if len(s) >= 200]
    if ans_complets:
        cumul_moy = []
        tot = {a: 0.0 for a in ans_complets}
        d = d0
        while d <= d1:
            for a in ans_complets:
                tot[a] += par_an[a].get((d.month, d.day), 0.0)
            cumul_moy.append(round(sum(tot.values()) / len(ans_complets), 1))
            d += timedelta(1)

    historique = (db.session.query(SaisonClimat).filter_by(cellule_id=pc.cellule_id)
                  .order_by(SaisonClimat.annee.desc()).all())
    tmax = (db.session.query(func.avg(ClimatJournalier.tmax), func.avg(ClimatJournalier.et0_mm))
            .filter(ClimatJournalier.cellule_id == pc.cellule_id,
                    ClimatJournalier.date.between(ind["debut"], ind["fin"] or ind["derniere_donnee"]))
            .one() if ind and ind["debut"] else (None, None))
    return render_template("climat/parcelle.html", p=p, cellule=pc.cellule, annee=annee, annees=annees,
                           ind=ind, moy=moy, ru=ru, texture=texture, jours=jours, pluie=pl,
                           cumul=cumul, cumul_moy=cumul_moy, n_moy=len(ans_complets),
                           historique=historique, tmax=tmax)


# --------------------------------------------------------------------------
@climat_bp.route("/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(NIVEAU_IMPORT)
def importer():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "grille":
                r = service.affecter_cellules()
                flash(f"Grille climatique : {r['cellules']} cellules de 5 km pour {r['parcelles']} parcelles.",
                      "success")
            elif action == "import":
                d1 = datetime.strptime(request.form["date_debut"], "%Y-%m-%d").date()
                d2 = datetime.strptime(request.form["date_fin"], "%Y-%m-%d").date()
                if (d2 - d1).days > 400:
                    flash("Période trop longue pour l'interface (13 mois max). "
                          "Pour l'historique, utilisez : python init_climat.py 2016 2025", "warning")
                    return redirect(url_for("climat.importer"))
                project = current_app.config.get("GEE_PROJECT")
                if not project:
                    raise ValueError("GEE_PROJECT n'est pas défini dans config.py.")
                r = service.importer_periode(d1, d2, project)
                flash(f"{r['lignes']} relevés journaliers importés ({r['cellules']} cellules, "
                      f"{r['jours']} jours) ; saisons recalculées pour {', '.join(map(str, r['annees']))}.",
                      "success")
            elif action == "recalculer":
                annees = [a for (a,) in db.session.query(
                    func.distinct(func.extract("year", ClimatJournalier.date)))]
                for a in annees:
                    service.calculer_saisons(int(a))
                flash(f"Saisons recalculées ({len(annees)} année(s)).", "success")
        except (ValueError, KeyError, RuntimeError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception as exc:   # erreurs Earth Engine
            db.session.rollback()
            flash(f"Erreur Earth Engine : {exc}", "danger")
        return redirect(url_for("climat.importer"))
    auj = date.today()
    return render_template("climat/import.html", couv=service.couverture(),
                           d1=date(auj.year, 1, 1),
                           d2=auj - timedelta(days=1), agro=agro)
