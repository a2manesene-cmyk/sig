"""
Services du module Sol : import de la base IFDC, rattachement des parcelles
(interpolation IDW des points voisins), saisie CAR et synchronisation avec la parcelle.
"""
import json
from datetime import datetime

from openpyxl import load_workbook
from sqlalchemy import text

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.sol import PointSolIFDC, AnalyseSol, HistoriqueSol
from app.sol import referentiel as R


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Import de la base IFDC
# --------------------------------------------------------------------------
def importer_ifdc(fichier):
    """Remplace les points IFDC par ceux du classeur. Renvoie un résumé."""
    wb = load_workbook(fichier, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    lignes = list(ws.iter_rows(values_only=True))
    wb.close()

    # Ligne d'en-tête : celle qui contient « CodeGeopoint »
    idx_h = None
    for i, row in enumerate(lignes[:10]):
        if any(str(c or "").strip().lower() == "codegeopoint" for c in row):
            idx_h = i
            break
    if idx_h is None:
        raise ValueError("Colonne « CodeGeopoint » introuvable : ce n'est pas la base IFDC attendue.")
    entetes = [str(c or "").strip() for c in lignes[idx_h]]
    col = {h: j for j, h in enumerate(entetes) if h}
    for obligatoire in ("CodeGeopoint", "X", "Y"):
        if obligatoire not in col:
            raise ValueError(f"Colonne « {obligatoire} » manquante.")

    points, ecartes = [], 0
    for row in lignes[idx_h + 1:]:
        code = row[col["CodeGeopoint"]] if col["CodeGeopoint"] < len(row) else None
        x, y = _num(row[col["X"]]), _num(row[col["Y"]])
        if not code or x is None or y is None:
            continue   # ligne d'unités ou vide
        vals, rejet = {}, {}
        for cle, _lib, _u, _d, (lo, hi), nom_ifdc in R.PARAMETRES:
            if not nom_ifdc or nom_ifdc not in col:
                continue
            v = _num(row[col[nom_ifdc]])
            if v is not None and not (lo <= v <= hi):
                rejet[cle] = v
                v = None
            vals[cle] = v
        ecartes += len(rejet)
        points.append((str(code).strip(), x, y, vals, rejet))

    if not points:
        raise ValueError("Aucun point valide trouvé dans le fichier.")

    db.session.query(PointSolIFDC).delete()
    sql = text(f"""
        INSERT INTO sol_points_ifdc (code, x_utm, y_utm, geom_utm, geom,
            {", ".join(R.CLES_IFDC)}, valeurs_ecartees, source, importe_le)
        VALUES (:code, :x, :y,
            ST_SetSRID(ST_MakePoint(:x, :y), 32628),
            ST_Transform(ST_SetSRID(ST_MakePoint(:x, :y), 32628), 4326),
            {", ".join(":" + c for c in R.CLES_IFDC)}, :rej, 'IFDC Dundel Suuf', now())
    """)
    params = []
    for code, x, y, vals, rejet in points:
        d = {"code": code, "x": x, "y": y,
             "rej": json.dumps(rejet) if rejet else None}
        d.update({c: vals.get(c) for c in R.CLES_IFDC})
        params.append(d)
    db.session.execute(sql, params)
    db.session.commit()
    return {"points": len(points), "valeurs_ecartees": ecartes}


# --------------------------------------------------------------------------
# Rattachement parcelles <- points IFDC
# --------------------------------------------------------------------------
def _completer(vals):
    """Calcule MO, texture, contraintes et fertilité à partir des valeurs."""
    if vals.get("mo") is None and vals.get("c_total") is not None:
        vals["mo"] = round(vals["c_total"] * 1.724, 3)
    vals["texture"] = vals.get("texture") or R.texture_usda(
        vals.get("sable"), vals.get("argile"), vals.get("limon"))
    vals["contraintes"] = "; ".join(R.contraintes(vals)) or None
    vals["fertilite"] = R.fertilite(vals)
    return vals


def synchroniser_parcelle(p, a):
    """Recopie la synthèse dans les champs sol_* de la parcelle (utilisés ailleurs)."""
    p.sol_ph = round(a.ph, 2) if a.ph is not None else None
    p.sol_mo = round(a.mo, 2) if a.mo is not None else None
    p.sol_type = a.texture
    p.sol_fertilite = a.fertilite
    p.sol_source = a.source
    p.sol_note = a.note


def rattacher(parcelle_ids=None):
    """Interpole les valeurs IFDC pour chaque parcelle (IDW, 5 voisins < 5 km).

    Les fiches saisies par le CAR gardent leurs valeurs : seules les valeurs IFDC
    de référence (retour arrière) et la fiabilité sont mises à jour.
    """
    pts = {p.id: p for p in db.session.query(PointSolIFDC).all()}
    if not pts:
        raise ValueError("Aucun point IFDC en base : importez d'abord la base IFDC.")

    filtre = "AND p.id = ANY(:ids)" if parcelle_ids else ""
    rows = db.session.execute(text(f"""
        SELECT p.id, k.pid, k.d
        FROM parcelles p
        CROSS JOIN LATERAL (
            SELECT s.id AS pid,
                   ST_Distance(ST_Transform(ST_Centroid(p.geom), 32628), s.geom_utm) AS d
            FROM sol_points_ifdc s
            ORDER BY s.geom_utm <-> ST_Transform(ST_Centroid(p.geom), 32628)
            LIMIT :k
        ) k
        WHERE p.geom IS NOT NULL {filtre}
        ORDER BY p.id, k.d
    """), {"k": R.NB_VOISINS, "ids": list(parcelle_ids or [])}).all()

    voisins = {}
    for pid, ptid, d in rows:
        voisins.setdefault(pid, []).append((ptid, d))

    existantes = {a.parcelle_id: a for a in db.session.query(AnalyseSol)
                  .filter(AnalyseSol.parcelle_id.in_(list(voisins)))}
    parcelles = {p.id: p for p in db.session.query(Parcelle)
                 .filter(Parcelle.id.in_(list(voisins)))}
    stats = {"bonne": 0, "indicative": 0, "hors": 0, "car_conserve": 0}

    for pid, vs in voisins.items():
        proches = [(pts[i], max(d, 50.0)) for i, d in vs if d <= R.RAYON_MAX_M]
        d1 = vs[0][1] if vs else None
        vals = {}
        if proches:
            for cle in R.CLES_IFDC:
                num = den = 0.0
                for pt, d in proches:
                    v = getattr(pt, cle)
                    if v is not None:
                        w = 1.0 / d ** 2
                        num += w * v
                        den += w
                vals[cle] = round(num / den, 4) if den else None
            vals = _completer(vals)
            fiab = "Bonne (< 2 km)" if d1 < R.SEUIL_BONNE_M else "Indicative (2–5 km)"
            stats["bonne" if d1 < R.SEUIL_BONNE_M else "indicative"] += 1
        else:
            fiab = "Hors couverture IFDC"
            stats["hors"] += 1

        a = existantes.get(pid)
        if a is None:
            a = AnalyseSol(parcelle_id=pid, origine="IFDC")
            db.session.add(a)
        a.fiabilite = fiab
        a.distance_m = round(d1) if d1 is not None else None
        a.nb_points = len(proches)
        a.ifdc_json = json.dumps(vals) if vals else None

        if a.origine == "CAR":
            stats["car_conserve"] += 1
            continue
        for cle in R.CLES:
            setattr(a, cle, vals.get(cle))
        a.texture = vals.get("texture")
        a.contraintes = vals.get("contraintes")
        a.fertilite = vals.get("fertilite")
        a.source = R.SOURCE_IFDC if vals else None
        a.date_analyse = None
        a.laboratoire = None
        p = parcelles.get(pid)
        if p is not None and vals:
            synchroniser_parcelle(p, a)
    db.session.commit()
    return stats


# --------------------------------------------------------------------------
# Saisie / correction par le CAR
# --------------------------------------------------------------------------
def lire_formulaire(form):
    """Valide le formulaire CAR. Renvoie (valeurs, erreurs)."""
    vals, erreurs = {}, []
    for cle, lib, unite, _d, (lo, hi), _n in R.PARAMETRES:
        brut = form.get(cle, "")
        if str(brut).strip() == "":
            vals[cle] = None
            continue
        v = _num(brut)
        if v is None:
            erreurs.append(f"{lib} : valeur non numérique « {brut} ».")
        elif not (lo <= v <= hi):
            erreurs.append(f"{lib} : {v} hors de la plage plausible ({lo}–{hi} {unite}).")
        else:
            vals[cle] = v
    gran = [vals.get(k) for k in ("sable", "argile", "limon")]
    if all(g is not None for g in gran) and abs(sum(gran) - 100) > 2:
        erreurs.append(f"Sable + argile + limon = {sum(gran):.1f} % : le total doit faire 100 %.")
    vals["texture"] = (form.get("texture") or "").strip() or None
    source = form.get("source") or ""
    if source not in R.SOURCES_CAR:
        erreurs.append("Choisissez la source de la donnée (analyse labo, kit terrain ou observation).")
    if not any(vals.get(k) is not None for k in R.CLES) and not vals["texture"]:
        erreurs.append("Renseignez au moins un paramètre ou la texture.")
    return vals, erreurs


def enregistrer_saisie_car(p, form, user):
    vals, erreurs = lire_formulaire(form)
    if erreurs:
        return erreurs
    if vals.get("mo") is None and vals.get("c_total") is not None:
        vals["mo"] = round(vals["c_total"] * 1.724, 3)
    if all(vals.get(k) is not None for k in ("sable", "argile", "limon")):
        vals["texture"] = R.texture_usda(vals["sable"], vals["argile"], vals["limon"])

    a = p.analyse_sol
    if a is None:
        a = AnalyseSol(parcelle_id=p.id, fiabilite="Hors couverture IFDC")
        db.session.add(a)
    avant = {k: getattr(a, k) for k in R.CLES + ["texture", "source"]}

    for cle in R.CLES:
        setattr(a, cle, vals.get(cle))
    a.texture = vals.get("texture")
    a.contraintes = "; ".join(R.contraintes(a)) or None
    a.fertilite = R.fertilite(a)
    a.origine = "CAR"
    a.source = form.get("source")
    a.laboratoire = (form.get("laboratoire") or "").strip() or None
    d = (form.get("date_analyse") or "").strip()
    a.date_analyse = datetime.strptime(d, "%Y-%m-%d").date() if d else None
    a.note = (form.get("note") or "").strip() or None
    a.saisi_par_id = user.id
    a.saisi_le = datetime.utcnow()
    synchroniser_parcelle(p, a)

    apres = {k: getattr(a, k) for k in R.CLES + ["texture", "source"]}
    diff = {k: [avant[k], apres[k]] for k in apres if avant[k] != apres[k]}
    db.session.add(HistoriqueSol(parcelle_id=p.id, user_id=user.id, action="Saisie CAR",
                                 detail=json.dumps(diff, default=str, ensure_ascii=False)))
    db.session.commit()
    return []


def revenir_ifdc(p, user):
    a = p.analyse_sol
    if a is None or not a.ifdc:
        return False
    vals = a.ifdc
    avant = {k: getattr(a, k) for k in R.CLES + ["texture", "source"]}
    for cle in R.CLES:
        setattr(a, cle, vals.get(cle))
    a.texture = vals.get("texture")
    a.contraintes = vals.get("contraintes")
    a.fertilite = vals.get("fertilite")
    a.origine = "IFDC"
    a.source = R.SOURCE_IFDC
    a.laboratoire = a.date_analyse = a.note = None
    a.saisi_par_id = user.id
    a.saisi_le = datetime.utcnow()
    synchroniser_parcelle(p, a)
    apres = {k: getattr(a, k) for k in R.CLES + ["texture", "source"]}
    diff = {k: [avant[k], apres[k]] for k in apres if avant[k] != apres[k]}
    db.session.add(HistoriqueSol(parcelle_id=p.id, user_id=user.id,
                                 action="Retour aux valeurs IFDC",
                                 detail=json.dumps(diff, default=str, ensure_ascii=False)))
    db.session.commit()
    return True


def reprendre_saisies_existantes():
    """Convertit les anciennes saisies (champs sol_* de la parcelle) en fiches CAR."""
    n = 0
    q = (db.session.query(Parcelle)
         .outerjoin(AnalyseSol, AnalyseSol.parcelle_id == Parcelle.id)
         .filter(AnalyseSol.id.is_(None))
         .filter((Parcelle.sol_ph.isnot(None)) | (Parcelle.sol_mo.isnot(None))
                 | (Parcelle.sol_type.isnot(None)) | (Parcelle.sol_fertilite.isnot(None))))
    for p in q.all():
        a = AnalyseSol(parcelle_id=p.id, origine="CAR", ph=p.sol_ph, mo=p.sol_mo,
                       texture=p.sol_type, source=p.sol_source or "Observation terrain",
                       note=p.sol_note)
        a.contraintes = "; ".join(R.contraintes(a)) or None
        a.fertilite = p.sol_fertilite or R.fertilite(a)
        db.session.add(a)
        n += 1
    db.session.commit()
    return n
