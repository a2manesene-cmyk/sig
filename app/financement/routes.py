# -*- coding: utf-8 -*-
import unicodedata

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.auth.decorators import niveau_minimum
from app.models.organisation import OrganisationProducteur
from app.models.programme import Programme
from app.models.financement import FinancementOP

financement_bp = Blueprint("financement", __name__, url_prefix="/financement")

# Mots ignorés pour le rapprochement des noms d'OP
_STOP = {"COOPERATIVE", "COOP", "SCOOP", "GIE", "FEPROMAS", "CAC", "SA", "UNION",
         "FEDERATION", "DE", "DES", "DU", "LA", "LE", "LES", "ET"}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in s.upper()).split())


def _cle(s):
    """Clé de rapprochement : normalisée, sans mots génériques ni espaces."""
    mots = [m for m in _norm(s).split() if m not in _STOP]
    return "".join(mots)


def _i(v):
    try:
        if v is None or v == "":
            return None
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


@financement_bp.route("/")
@login_required
@niveau_minimum(2)
def liste():
    lignes = (FinancementOP.query
              .order_by(FinancementOP.region, FinancementOP.raison_sociale).all())
    total_accorde = sum((l.montant_accorde or 0) for l in lignes)
    total_sollicite = sum((l.montant_sollicite or 0) for l in lignes)
    total_realisation = sum((l.realisation or 0) for l in lignes)
    nb_lies = sum(1 for l in lignes if l.organisation_id)
    return render_template("financement/liste.html", lignes=lignes,
                           total_accorde=total_accorde, total_sollicite=total_sollicite,
                           total_realisation=total_realisation, nb_lies=nb_lies)


@financement_bp.route("/import", methods=["GET", "POST"])
@login_required
@niveau_minimum(3)
def importer():
    if request.method == "POST":
        import io
        from openpyxl import load_workbook
        fichier = request.files.get("fichier")
        annee = _i(request.form.get("annee")) or 2026
        apercu = bool(request.form.get("apercu"))
        prog_nom = (request.form.get("programme") or "Grenier du maïs").strip()
        if not fichier or fichier.filename == "":
            flash("Aucun fichier sélectionné.", "warning")
            return redirect(url_for("financement.importer"))
        try:
            wb = load_workbook(io.BytesIO(fichier.read()), data_only=True, read_only=True)
            ws = wb.worksheets[0]
            rows = [r for r in ws.iter_rows(values_only=True)]
        except Exception as exc:
            flash(f"Fichier Excel illisible : {exc}", "danger")
            return redirect(url_for("financement.importer"))
        if len(rows) < 3:
            flash("Fichier vide ou format inattendu.", "warning")
            return redirect(url_for("financement.importer"))

        # En-têtes en 2e ligne (index 1) -> index des colonnes
        entetes = [str(c).strip() if c is not None else "" for c in rows[1]]
        data_rows = rows[2:]

        def idx(*noms):
            for n in noms:
                for j, h in enumerate(entetes):
                    if h.lower().startswith(n.lower()):
                        return j
            return None

        IDX = {
            "rs": idx("Raison Sociale") or 1,
            "region": idx("RÉGION", "REGION", "Région"),
            "nbprod": idx("Nbre producteur"),
            "sup_dem": idx("Superficie demand"),
            "mnt_sol": idx("Montant sollicit"),
            "sup_acc": idx("Superficie Accord", "Superficie accord"),
            "mnt_acc": idx("Montant accord"),
            "etat": idx("Etat", "État"),
            "real": idx("Réalisation", "Realisation"),
            "ha_fact": idx("Nbre ha factur"),
            "prime_ha": idx("Prime assurance /"),
            "prime_tot": idx("Prime assurance total"),
        }

        def cell(row, key):
            j = IDX.get(key)
            if j is None or j >= len(row):
                return None
            return row[j]


        def g(row, *noms):
            for n in noms:
                for c in cols:
                    if c.lower().startswith(n.lower()):
                        return row[cols[c]]
            return None

        # Programme
        prog = Programme.query.filter(func.lower(Programme.nom) == prog_nom.lower()).first()
        if not prog and not apercu:
            prog = Programme(nom=prog_nom, actif=True)
            db.session.add(prog)
            db.session.flush()

        # Index des OP existantes par clé de rapprochement
        ops = OrganisationProducteur.query.all()
        index = {}
        for o in ops:
            index.setdefault(_cle(o.nom), o)

        n_new, n_lies, n_skip = 0, 0, 0
        region_courante = None
        for row in data_rows:
            rs = cell(row, "rs")
            reg = cell(row, "region")
            if reg is not None and str(reg).strip() and str(reg) != "nan":
                if "total" not in str(reg).lower():
                    region_courante = str(reg).strip()
            if rs is None or str(rs).strip() in ("", "nan"):
                n_skip += 1
                continue
            rs = str(rs).strip()
            if rs.replace(" ", "").replace(".", "").isdigit():
                n_skip += 1
                continue
            montant_accorde = _i(cell(row, "mnt_acc"))
            op = index.get(_cle(rs))
            if op:
                n_lies += 1
            etat_v = cell(row, "etat")
            fin = FinancementOP(
                organisation_id=(op.id if op else None),
                programme_id=(prog.id if prog else None),
                raison_sociale=rs[:150],
                region=region_courante,
                annee=annee,
                nb_producteurs=_i(cell(row, "nbprod")),
                superficie_demandee=_f(cell(row, "sup_dem")),
                montant_sollicite=_i(cell(row, "mnt_sol")),
                superficie_accordee=_f(cell(row, "sup_acc")),
                montant_accorde=montant_accorde,
                etat=(str(etat_v).strip()[:60] if etat_v is not None else None),
                realisation=_i(cell(row, "real")),
                nb_ha_facture=_f(cell(row, "ha_fact")),
                prime_ha=_f(cell(row, "prime_ha")),
                prime_total=_f(cell(row, "prime_tot")),
            )
            n_new += 1
            if not apercu:
                db.session.add(fin)

        if apercu:
            db.session.rollback()
            flash(f"APERÇU : {n_new} financement(s) à importer, {n_lies} liés à une OP, "
                  f"{n_skip} lignes ignorées (totaux/vides). Décochez « aperçu » pour importer.",
                  "info")
            return redirect(url_for("financement.importer"))

        db.session.commit()
        flash(f"Import financement : {n_new} ligne(s) enregistrée(s) "
              f"(dont {n_lies} liées à une OP), {n_skip} ignorées.", "success")
        return redirect(url_for("financement.liste"))

    return render_template("financement/import.html",
                           programmes=Programme.query.order_by(Programme.nom).all())
