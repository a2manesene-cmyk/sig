"""
Estimation du rendement par IA, à partir des embeddings TESSERA (128 dim).

Modèle : régression ridge (numpy pur) apprise par culture à partir des
rendements de référence saisis. Métrique honnête par validation
leave-one-out (adaptée aux petits échantillons). S'active dès qu'une culture
dispose d'au moins MIN_POINTS parcelles à la fois embeddées et mesurées.
"""
import json

try:
    import numpy as np
except Exception:          # numpy absent : la page IA se désactive proprement
    np = None

from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.embedding import ParcelleEmbedding
from app.models.rendement import RendementReference

MIN_POINTS = 6
LAMBDA = 10.0     # régularisation (forte, car 128 dimensions et peu de points)


def _embeddings(annee=None):
    """{parcelle_id: np.array(128)}"""
    q = ParcelleEmbedding.query
    if annee is not None:
        q = q.filter_by(annee=annee)
    out = {}
    for e in q.all():
        try:
            out[e.parcelle_id] = np.array(json.loads(e.vecteur), dtype=float)
        except Exception:
            pass
    return out


def _standardise(X):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    return (X - mu) / sd, mu, sd


def _ridge_fit(X, y, lam=LAMBDA):
    Xs, mu, sd = _standardise(X)
    n = Xs.shape[0]
    k = int(max(2, min(8, n // 3)))
    U, S, Vt = np.linalg.svd(Xs, full_matrices=False)
    comp = Vt[:k]                 # (k, 128) axes principaux
    Z = Xs @ comp.T
    d = Z.shape[1]
    A = Z.T @ Z + lam * np.eye(d)
    w = np.linalg.solve(A, Z.T @ (y - y.mean()))
    b = y.mean()
    return {"w": w, "b": b, "mu": mu, "sd": sd, "comp": comp}


def _predict(model, X):
    Xs = (X - model["mu"]) / model["sd"]
    Z = Xs @ model["comp"].T
    return Z @ model["w"] + model["b"]


def _loo_r2(X, y, lam=LAMBDA):
    n = len(y)
    if n < 3:
        return None
    preds = np.zeros(n)
    for i in range(n):
        mask = np.ones(n, dtype=bool)
        mask[i] = False
        m = _ridge_fit(X[mask], y[mask], lam)
        preds[i] = _predict(m, X[i:i + 1])[0]
    ss_res = float(np.sum((y - preds) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else None


def statut():
    """Diagnostic : nb d'embeddings, et par culture le nb de points d'entraînement."""
    emb = _embeddings()
    refs = RendementReference.query.all()
    par_culture = {}
    for r in refs:
        if r.parcelle_id in emb and r.rendement_kg_ha is not None:
            par_culture.setdefault((r.culture or "—").strip(), 0)
            par_culture[(r.culture or "—").strip()] += 1
    return {
        "nb_embeddings": len(emb),
        "points_par_culture": par_culture,
        "min_points": MIN_POINTS,
    }


def modeles(annee=None):
    """Un modèle IA par culture (si assez de points) + métrique LOO R²."""
    if np is None:
        return {}
    emb = _embeddings(annee)
    refs = RendementReference.query.all()
    data = {}
    for r in refs:
        vec = emb.get(r.parcelle_id)
        if vec is None or r.rendement_kg_ha is None:
            continue
        data.setdefault((r.culture or "—").strip(), []).append((vec, r.rendement_kg_ha))

    out = {}
    for cul, pts in data.items():
        if len(pts) < MIN_POINTS:
            continue
        X = np.array([p[0] for p in pts])
        y = np.array([p[1] for p in pts], dtype=float)
        out[cul] = {"model": _ridge_fit(X, y), "n": len(pts), "r2": _loo_r2(X, y)}
    return out


def predictions(pids=None, annee=None):
    """{parcelle_id: rendement_estimé_kg_ha} pour les parcelles embeddées."""
    emb = _embeddings(annee)
    if not emb:
        return {}
    mdl = modeles(annee)
    if not mdl:
        return {}
    # culture par parcelle
    q = db.session.query(Parcelle.id, Parcelle.culture).filter(
        Parcelle.id.in_(list(emb.keys())))
    if pids is not None:
        q = q.filter(Parcelle.id.in_(pids))
    out = {}
    for pid, culture in q.all():
        m = mdl.get((culture or "—").strip())
        if not m:
            continue
        val = float(_predict(m["model"], emb[pid].reshape(1, -1))[0])
        out[pid] = max(0.0, round(val))
    return out
