"""
Séries temporelles des indices (NDVI, NDWI, NDMI, SAVI, EVI) : une valeur par passage de
Sentinel-2 depuis le début de la campagne (15 mai), pour chaque parcelle.

Le calcul de milliers de parcelles sur plusieurs mois dépasse le délai d'une page web : il
tourne en arrière-plan (script calculer_indices.py), parcelle par parcelle à partir de sa
dernière date connue. Relancé régulièrement, il ajoute les nouveaux passages ; lancé après un
import, il complète l'historique des nouvelles parcelles depuis le 15 mai.
"""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import text

from app.extensions import db

SOURCE_SERIE = "GEE Sentinel-2"
SOURCE_INSTANTANE = "GEE Sentinel-2 (composite)"
MIN_PASSAGES = 3          # en dessous, la parcelle est considérée sans historique
LOT = 40                  # parcelles par requête Earth Engine


def debut_campagne(aujourdhui=None):
    a = aujourdhui or date.today()
    d = date(a.year, 5, 15)
    return d if a >= d else date(a.year - 1, 5, 15)


def _dossier():
    d = current_app.instance_path
    os.makedirs(d, exist_ok=True)
    return d


def fichier_statut():
    return os.path.join(_dossier(), "indices_statut.json")


def fichier_journal():
    return os.path.join(_dossier(), "indices_journal.log")


def fichier_sortie():
    """Sortie brute du script (messages Python, erreurs inattendues)."""
    return os.path.join(_dossier(), "indices_sortie.log")


def lire_journal(n=12):
    """Dernières lignes du journal. Tolère un ancien journal écrit en cp1252 (Windows)."""
    try:
        with open(fichier_journal(), "rb") as f:
            brut = f.read()[-20000:]
    except OSError:
        return []
    try:
        texte = brut.decode("utf-8")
    except UnicodeDecodeError:
        texte = brut.decode("cp1252", errors="replace")
    return texte.splitlines(keepends=True)[-n:]


def statut():
    try:
        with open(fichier_statut(), encoding="utf-8", errors="replace") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    # calcul interrompu (serveur redémarré) : plus de nouvelles depuis 30 min
    if s.get("etat") == "en cours":
        try:
            maj = datetime.fromisoformat(s.get("maj"))
            if datetime.now() - maj > timedelta(minutes=30):
                s["etat"] = "interrompu"
        except (TypeError, ValueError):
            pass
    return s


def ecrire_statut(**kw):
    s = statut() or {}
    s.update(kw)
    s["maj"] = datetime.now().isoformat(timespec="seconds")
    tmp = fichier_statut() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, fichier_statut())


def en_cours():
    s = statut()
    return bool(s and s.get("etat") == "en cours")


def journal(msg):
    ligne = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        print(ligne, flush=True)
    except UnicodeEncodeError:                 # console Windows sans UTF-8
        print(ligne.encode("ascii", "replace").decode(), flush=True)
    try:
        with open(fichier_journal(), "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
def a_completer(debut, fin, pids=None, tout=False):
    """[(parcelle_id, date_de_départ)] : depuis le début de campagne pour une parcelle sans
    historique, sinon depuis le lendemain de son dernier passage enregistré."""
    rows = db.session.execute(text("""
        SELECT p.id, COUNT(iv.id) FILTER (WHERE iv.source IS DISTINCT FROM :inst),
               MAX(iv.date) FILTER (WHERE iv.source IS DISTINCT FROM :inst)
          FROM parcelles p
          LEFT JOIN indices_vegetation iv ON iv.parcelle_id = p.id AND iv.date >= :d1
         WHERE p.geom IS NOT NULL AND NOT ST_IsEmpty(p.geom)
         GROUP BY p.id
    """), {"inst": SOURCE_INSTANTANE, "d1": debut}).all()
    out = []
    for pid, n, derniere in rows:
        if pids is not None and pid not in pids:
            continue
        if tout or not n or n < MIN_PASSAGES or derniere is None:
            depart = debut
        else:
            depart = derniere + timedelta(days=1)
        if depart <= fin:
            out.append((pid, depart))
    return out


def _features(pids):
    """Géométries et centroïdes, triées par position pour que chaque lot reste compact
    (moins de scènes Sentinel-2 par requête)."""
    rows = db.session.execute(text("""
        SELECT id, ST_AsGeoJSON(geom), ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom))
          FROM parcelles WHERE id = ANY(:ids)
    """), {"ids": list(pids)}).all()
    rows.sort(key=lambda r: (round(r[3] / 0.1), r[2]))     # bandes de ~11 km, d'ouest en est
    return [{"id": r[0], "geometry": json.loads(r[1])} for r in rows]


def enregistrer(lignes):
    """Upsert par (parcelle, date d'acquisition). Retourne le nombre de valeurs écrites."""
    from app.models.indice import IndiceVegetation
    lignes = [r for r in lignes if r.get("pid") is not None and r.get("date")]
    if not lignes:
        return 0
    pids = {r["pid"] for r in lignes}
    dates = [datetime.strptime(r["date"], "%Y-%m-%d").date() for r in lignes]
    existants = {(iv.parcelle_id, iv.date): iv for iv in IndiceVegetation.query.filter(
        IndiceVegetation.parcelle_id.in_(pids), IndiceVegetation.date >= min(dates),
        IndiceVegetation.date <= max(dates))}
    n = 0
    for r, d in zip(lignes, dates):
        iv = existants.get((r["pid"], d))
        if iv is None:
            iv = IndiceVegetation(parcelle_id=r["pid"], date=d)
            db.session.add(iv)
            existants[(r["pid"], d)] = iv
        iv.ndvi, iv.ndwi, iv.ndmi = r.get("ndvi"), r.get("ndwi"), r.get("ndmi")
        iv.savi, iv.evi, iv.source = r.get("savi"), r.get("evi"), SOURCE_SERIE
        n += 1
    db.session.commit()
    return n


def calculer(debut=None, fin=None, pids=None, tout=False, project=None, calcul=None):
    """Calcule et enregistre les séries manquantes. `calcul` : fonction (features, d1, d2, project)
    → lignes ; par défaut Earth Engine (gee.compute_timeseries)."""
    if calcul is None:
        from app.teledetection.gee import compute_timeseries as calcul
    project = project or current_app.config.get("GEE_PROJECT")
    fin = fin or date.today()
    debut = debut or debut_campagne(fin)
    taches = a_completer(debut, fin, pids, tout)
    groupes = {}
    for pid, depart in taches:
        groupes.setdefault(depart, []).append(pid)
    total = len(taches)
    ecrire_statut(etat="en cours", debut=debut.isoformat(), fin=fin.isoformat(), total=total, faites=0,
                  valeurs=0, sans_image=0, erreurs=0, message=f"{total} parcelle(s) à compléter",
                  lance_le=datetime.now().isoformat(timespec="seconds"))
    journal(f"Début : {total} parcelle(s), {len(groupes)} date(s) de départ, du {debut} au {fin}.")
    faites = valeurs = sans_image = erreurs = 0
    fin_excl = (fin + timedelta(days=1)).isoformat()          # filterDate exclut la date de fin
    for depart in sorted(groupes):
        feats = _features(groupes[depart])
        for i in range(0, len(feats), LOT):
            lot = feats[i:i + LOT]
            try:
                lignes = calcul(lot, depart.isoformat(), fin_excl, project)
                valeurs += enregistrer(lignes)
            except RuntimeError as exc:
                if "Aucune image" in str(exc):
                    sans_image += len(lot)
                else:
                    erreurs += len(lot)
                    journal(f"Erreur sur un lot de {len(lot)} parcelle(s) : {exc}")
                db.session.rollback()
            except Exception as exc:            # réseau, quota : on continue avec les lots suivants
                erreurs += len(lot)
                db.session.rollback()
                journal(f"Erreur sur un lot de {len(lot)} parcelle(s) : {exc.__class__.__name__} {exc}")
            faites += len(lot)
            ecrire_statut(faites=faites, valeurs=valeurs, sans_image=sans_image, erreurs=erreurs,
                          message=f"{faites}/{total} parcelle(s) traitée(s), {valeurs} valeur(s) enregistrée(s)")
    etat = "terminé" if not erreurs else "terminé avec erreurs"
    ecrire_statut(etat=etat, faites=faites, valeurs=valeurs, sans_image=sans_image, erreurs=erreurs,
                  message=f"{faites} parcelle(s) traitée(s), {valeurs} valeur(s) enregistrée(s)"
                          + (f", {erreurs} en erreur" if erreurs else ""))
    journal(f"Fin : {faites} parcelle(s), {valeurs} valeur(s), {sans_image} sans image, {erreurs} en erreur.")
    return {"total": total, "valeurs": valeurs, "sans_image": sans_image, "erreurs": erreurs}


def lancer_en_arriere_plan(tout=False, debut=None, programme=None):
    """Lance calculer_indices.py en tâche de fond (hors du délai de la requête web)."""
    if en_cours():
        return False
    racine = os.path.dirname(current_app.root_path)
    script = os.path.join(racine, "calculer_indices.py")
    args = [sys.executable, script, "--force"]
    if tout:
        args.append("--tout")
    if debut:
        args += ["--debut", debut.isoformat() if hasattr(debut, "isoformat") else str(debut)]
    if programme:
        args += ["--programme", str(programme)]
    ecrire_statut(etat="en cours", total=None, faites=0, valeurs=0, sans_image=0, erreurs=0,
                  message="Calcul lancé, préparation…", lance_le=datetime.now().isoformat(timespec="seconds"))
    # sortie du script dans un fichier à part (le journal est écrit par le script lui-même, en UTF-8)
    log = open(fichier_sortie(), "a", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    kw = {"cwd": racine, "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)
    return True


def couverture(debut=None):
    """Nombre de parcelles avec / sans historique depuis le début de campagne."""
    debut = debut or debut_campagne()
    r = db.session.execute(text("""
        SELECT COUNT(*) FILTER (WHERE n >= :m), COUNT(*) FILTER (WHERE n < :m), MAX(derniere)
          FROM (SELECT p.id, COUNT(iv.id) FILTER (WHERE iv.source IS DISTINCT FROM :inst) AS n,
                       MAX(iv.date) FILTER (WHERE iv.source IS DISTINCT FROM :inst) AS derniere
                  FROM parcelles p LEFT JOIN indices_vegetation iv ON iv.parcelle_id = p.id AND iv.date >= :d1
                 WHERE p.geom IS NOT NULL GROUP BY p.id) x
    """), {"m": MIN_PASSAGES, "inst": SOURCE_INSTANTANE, "d1": debut}).one()
    return {"avec": r[0], "sans": r[1], "derniere": r[2], "debut": debut}
