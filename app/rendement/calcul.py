"""
Moteur d'estimation du rendement.

Étage 1 — Indice de potentiel (0–100) : dérivé du pic de NDVI, sans donnée terrain.
Étage 2 — Estimation en kg/ha : régression linéaire NDVI_max -> rendement, calée
          par culture à partir des rendements de référence saisis. S'active dès
          qu'il y a assez de points (min. MIN_POINTS) pour une culture.
"""
from sqlalchemy import func

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.indice import IndiceVegetation
from app.models.rendement import RendementReference

MIN_POINTS = 4          # points de calibration minimaux par culture
NDVI_MIN, NDVI_MAX = 0.08, 0.83   # bornes pour l'indice de potentiel


def ndvi_max_par_parcelle(pids=None):
    """Pic de NDVI (et nb de relevés) par parcelle."""
    q = (db.session.query(
            IndiceVegetation.parcelle_id,
            func.max(IndiceVegetation.ndvi),
            func.count(IndiceVegetation.id))
         .filter(IndiceVegetation.ndvi.isnot(None))
         .group_by(IndiceVegetation.parcelle_id))
    if pids is not None:
        q = q.filter(IndiceVegetation.parcelle_id.in_(pids))
    return {pid: {"ndvi_max": vmax, "n": n} for pid, vmax, n in q.all()}


def indice_potentiel(ndvi_max):
    if ndvi_max is None:
        return None
    x = (ndvi_max - NDVI_MIN) / (NDVI_MAX - NDVI_MIN)
    return round(max(0.0, min(1.0, x)) * 100)


POTENTIEL_DEFAUT = 4500   # potentiel variété (kg/ha) par défaut si non renseigné


def rendement_estime(ndvi_max, potentiel):
    """Rendement estimé = (indice de potentialité / 100) × rendement potentiel."""
    ip = indice_potentiel(ndvi_max)
    if ip is None:
        return None
    pot = potentiel if potentiel else POTENTIEL_DEFAUT
    return round(pot * ip / 100.0)


def _fit(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    ybar = sy / n
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((y - (a * x + b)) ** 2 for x, y in zip(xs, ys))
    r2 = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    return {"a": a, "b": b, "r2": round(r2, 3), "n": n}


def modeles():
    """Un modèle linéaire par culture, calé sur les références disponibles."""
    refs = RendementReference.query.filter(
        RendementReference.ndvi_max.isnot(None),
        RendementReference.rendement_kg_ha.isnot(None),
    ).all()
    par_culture = {}
    for r in refs:
        cle = (r.culture or "—").strip()
        par_culture.setdefault(cle, []).append((r.ndvi_max, r.rendement_kg_ha))
    out = {}
    for cul, pts in par_culture.items():
        if len(pts) >= MIN_POINTS:
            m = _fit([x for x, _ in pts], [y for _, y in pts])
            if m:
                out[cul] = m
    return out


def estimation(culture, ndvi_max, mdl):
    """Rendement estimé (kg/ha) si un modèle existe pour la culture."""
    if ndvi_max is None or not culture:
        return None
    m = mdl.get(culture.strip())
    if not m:
        return None
    val = m["a"] * ndvi_max + m["b"]
    return round(max(0.0, val))


def rendement_attendu_par_parcelle(pids=None):
    """Rendement attendu (kg/ha) par parcelle :
    - si NDVI disponible : estimé = (indice/100) × potentiel de la parcelle ;
    - sinon : rendement potentiel de la parcelle, ou référence la plus récente.
    Retourne {pid: rdt}."""
    # Potentiel + référence par parcelle
    pot = {}
    qp = db.session.query(Parcelle.id, Parcelle.rendement_potentiel)
    if pids is not None:
        qp = qp.filter(Parcelle.id.in_(pids))
    for pid, rp in qp.all():
        pot[pid] = rp
    ref = {}
    qr = RendementReference.query
    if pids is not None:
        qr = qr.filter(RendementReference.parcelle_id.in_(pids))
    for r in qr.order_by(RendementReference.annee.asc()).all():
        if r.rendement_kg_ha is not None:
            ref[r.parcelle_id] = r.rendement_kg_ha

    nvm = ndvi_max_par_parcelle(pids)
    out = {}
    pids_all = set(pot) | set(ref) | set(nvm)
    for pid in pids_all:
        m = nvm.get(pid)
        if m and m.get("ndvi_max") is not None:
            out[pid] = rendement_estime(m["ndvi_max"], pot.get(pid) or ref.get(pid))
        elif pot.get(pid):
            out[pid] = round(pot[pid])
        elif pid in ref:
            out[pid] = round(ref[pid])
    return out


import unicodedata as _ud

_PETITS = {"DU", "DE", "DES", "LA", "LE", "LES", "ET", "D", "L", "A", "AU", "AUX"}


def cle_lieu(s):
    """Clé de regroupement d'un lieu : majuscules, sans accents, espaces normalisés."""
    if not s:
        return None
    s2 = _ud.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s2.upper().replace("-", " ").split())


def belle_casse(cle):
    """Affichage propre d'une clé de lieu : 'NIORO DU RIP' -> 'Nioro du Rip'."""
    if not cle:
        return cle
    mots = str(cle).split()
    out = []
    for i, m in enumerate(mots):
        out.append(m.lower() if (i > 0 and m in _PETITS) else m.capitalize())
    return " ".join(out)
