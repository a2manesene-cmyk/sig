"""
Services du module Climat : grille de cellules, import des séries GEE,
calcul des indicateurs de saison et des moyennes pluriannuelles.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import text, func

from app.extensions import db
from app.models.climat import CelluleClimat, ParcelleCellule, ClimatJournalier, SaisonClimat
from app.climat import agro


# --------------------------------------------------------------------------
# Grille
# --------------------------------------------------------------------------
def affecter_cellules():
    """Crée les cellules CHIRPS couvrant les parcelles et rattache chaque parcelle.

    Recalcule aussi la texture dominante et la réserve utile de chaque cellule
    à partir du module Sol (s'il est installé).
    """
    rows = db.session.execute(text("""
        SELECT id, ST_X(ST_Centroid(geom)), ST_Y(ST_Centroid(geom))
        FROM parcelles WHERE geom IS NOT NULL
    """)).all()
    existantes = {c.code: c for c in db.session.query(CelluleClimat).all()}
    affect = {}
    for pid, lon, lat in rows:
        _i, _j, code, clon, clat = agro.cellule(lon, lat)
        if code not in existantes:
            c = CelluleClimat(code=code, lon=round(clon, 4), lat=round(clat, 4),
                              geom=f"SRID=4326;POINT({clon} {clat})")
            db.session.add(c)
            existantes[code] = c
        affect[pid] = code
    db.session.flush()

    db.session.query(ParcelleCellule).delete()
    db.session.bulk_save_objects([ParcelleCellule(parcelle_id=pid, cellule_id=existantes[code].id)
                                  for pid, code in affect.items()])

    # Texture dominante -> réserve utile
    textures = defaultdict(Counter)
    try:
        for cid, tex in db.session.execute(text("""
            SELECT pc.cellule_id, a.texture FROM climat_parcelles pc
            JOIN analyses_sol a ON a.parcelle_id = pc.parcelle_id WHERE a.texture IS NOT NULL
        """)):
            textures[cid][tex] += 1
    except Exception:          # module Sol absent
        db.session.rollback()
    for c in existantes.values():
        tex = textures[c.id].most_common(1)[0][0] if textures.get(c.id) else None
        c.texture = tex
        c.ru_mm = agro.ru_texture(tex)
    db.session.commit()
    return {"parcelles": len(affect), "cellules": len(existantes)}


# --------------------------------------------------------------------------
# Import GEE
# --------------------------------------------------------------------------
def importer_periode(d1, d2, project, extraire=None):
    """Télécharge pluie et températures pour toutes les cellules sur [d1, d2],
    enregistre les séries et recalcule les saisons des années concernées.

    `extraire` permet de substituer la source (tests) ; par défaut Earth Engine.
    """
    if extraire is None:
        from app.climat.gee_climat import extraire_climat as extraire
    if d2 < d1:
        raise ValueError("La date de fin précède la date de début.")
    cellules = db.session.query(CelluleClimat).all()
    if not cellules:
        affecter_cellules()
        cellules = db.session.query(CelluleClimat).all()
    if not cellules:
        raise ValueError("Aucune parcelle géolocalisée : impossible de construire la grille climatique.")

    donnees = extraire([{"code": c.code, "lon": c.lon, "lat": c.lat} for c in cellules], d1, d2, project)
    par_code = {c.code: c for c in cellules}
    lignes = []
    for code, jours in donnees.items():
        c = par_code.get(code)
        if c is None:
            continue
        for jour, v in jours.items():
            d = date.fromisoformat(jour)
            lignes.append({
                "cid": c.id, "d": d, "p": v.get("pluie"), "tn": v.get("tmin"), "tx": v.get("tmax"),
                "e": agro.et0_hargreaves(v.get("tmin"), v.get("tmax"), c.lat, d),
            })
    if not lignes:
        raise ValueError("Earth Engine n'a renvoyé aucune donnée pour cette période "
                         "(CHIRPS est publié avec ~3 semaines de retard).")
    sql = text("""
        INSERT INTO climat_journalier (cellule_id, date, pluie_mm, tmin, tmax, et0_mm)
        VALUES (:cid, :d, :p, :tn, :tx, :e)
        ON CONFLICT (cellule_id, date) DO UPDATE SET
            pluie_mm = COALESCE(EXCLUDED.pluie_mm, climat_journalier.pluie_mm),
            tmin = COALESCE(EXCLUDED.tmin, climat_journalier.tmin),
            tmax = COALESCE(EXCLUDED.tmax, climat_journalier.tmax),
            et0_mm = COALESCE(EXCLUDED.et0_mm, climat_journalier.et0_mm)
    """)
    for k in range(0, len(lignes), 5000):
        db.session.execute(sql, lignes[k:k + 5000])
    db.session.commit()

    annees = sorted({l["d"].year for l in lignes})
    for a in annees:
        calculer_saisons(a)
    return {"jours": len({l["d"] for l in lignes}), "cellules": len(donnees),
            "lignes": len(lignes), "annees": annees}


# --------------------------------------------------------------------------
# Saisons
# --------------------------------------------------------------------------
def series_cellule(cellule_id, annee):
    rows = (db.session.query(ClimatJournalier.date, ClimatJournalier.pluie_mm, ClimatJournalier.et0_mm)
            .filter(ClimatJournalier.cellule_id == cellule_id,
                    ClimatJournalier.date >= date(annee, 1, 1),
                    ClimatJournalier.date <= date(annee, 12, 31))
            .order_by(ClimatJournalier.date).all())
    return ({d: p for d, p, _e in rows if p is not None}, {d: e for d, _p, e in rows if e is not None})


def calculer_saisons(annee, cellule_ids=None):
    q = db.session.query(CelluleClimat)
    if cellule_ids:
        q = q.filter(CelluleClimat.id.in_(cellule_ids))
    n = 0
    for c in q.all():
        pluie, etp = series_cellule(c.id, annee)
        ind = agro.indicateurs_saison(pluie, etp, annee, c.ru_mm or agro.RU_DEFAUT)
        if ind is None:
            continue
        s = (db.session.query(SaisonClimat).filter_by(cellule_id=c.id, annee=annee).first()
             or SaisonClimat(cellule_id=c.id, annee=annee))
        for k in ("debut", "fin", "longueur_j", "cumul_mm", "cumul_mai_oct", "nb_jours_pluie",
                  "seq_seche_max_j", "seq_seche_max_debut", "nb_seq_seches_10j", "faux_departs",
                  "derniere_donnee", "complete"):
            setattr(s, k, ind[k])
        s.calcule_le = func.now()
        db.session.add(s)
        n += 1
    db.session.commit()
    return n


def _jour_an(d):
    return d.timetuple().tm_yday if d else None


def moyennes(cellule_ids, sauf_annee=None):
    """Moyennes pluriannuelles (saisons complètes) pour un ensemble de cellules."""
    q = db.session.query(SaisonClimat).filter(SaisonClimat.cellule_id.in_(cellule_ids),
                                             SaisonClimat.complete.is_(True))
    if sauf_annee:
        q = q.filter(SaisonClimat.annee != sauf_annee)
    ss = q.all()
    if not ss:
        return None
    def moy(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None
    annees = sorted({s.annee for s in ss})
    debut_j = moy(_jour_an(s.debut) for s in ss)
    fin_j = moy(_jour_an(s.fin) for s in ss)
    return {
        "annees": annees, "n": len(annees),
        "debut_jour": debut_j, "fin_jour": fin_j,
        "longueur_j": moy(s.longueur_j for s in ss),
        "cumul_mm": moy(s.cumul_mm for s in ss),
        "cumul_mai_oct": moy(s.cumul_mai_oct for s in ss),
        "seq_seche_max_j": moy(s.seq_seche_max_j for s in ss),
    }


def date_du_jour(annee, jour_an):
    if jour_an is None:
        return None
    return date(annee, 1, 1) + timedelta(days=round(jour_an) - 1)


def couverture():
    r = db.session.query(func.min(ClimatJournalier.date), func.max(ClimatJournalier.date),
                         func.count(func.distinct(ClimatJournalier.cellule_id))).one()
    annees = [a for (a,) in db.session.query(SaisonClimat.annee).distinct().order_by(SaisonClimat.annee)]
    return {"debut": r[0], "fin": r[1], "cellules": r[2],
            "nb_cellules": db.session.query(func.count(CelluleClimat.id)).scalar(), "annees": annees}
