"""
Extraction des embeddings TESSERA (128 dimensions) par parcelle, via GeoTessera.

⚠️ À exécuter dans un environnement Python 3.12+ avec geotessera installé :
        pip install geotessera
    (idéalement un environnement dédié, séparé de celui de l'application).

Principe : on échantillonne l'embedding au centroïde (lon/lat WGS84) de chaque
parcelle, puis on le stocke dans la table parcelle_embeddings. Le modèle IA de
rendement (app/rendement/ia.py) s'appuie ensuite dessus.

Usage :
    python tessera_extract.py --annee 2024
"""
import argparse
import json

from app import create_app
from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.embedding import ParcelleEmbedding
from sqlalchemy import func

app = create_app()

# Version recommandée pour les nouveaux projets
DATASET_VERSION = "v1.1"
DATASET_VARIANT = "cambridge"


def centroids():
    """(parcelle_id, lon, lat) au centroïde, en WGS84."""
    rows = (db.session.query(
                Parcelle.id,
                func.ST_X(func.ST_Centroid(Parcelle.geom)),
                func.ST_Y(func.ST_Centroid(Parcelle.geom)))
            .filter(Parcelle.geom.isnot(None)).all())
    return [(pid, lon, lat) for pid, lon, lat in rows]


def run(annee, batch=500):
    from geotessera import GeoTessera
    gt = GeoTessera(dataset_version=DATASET_VERSION, dataset_variant=DATASET_VARIANT)

    pts = centroids()
    print(f"{len(pts)} parcelles à traiter (année {annee})…")
    existants = {
        e.parcelle_id: e
        for e in ParcelleEmbedding.query.filter_by(annee=annee).all()
    }

    n = 0
    for i in range(0, len(pts), batch):
        lot = pts[i:i + batch]
        points = [(lon, lat) for _, lon, lat in lot]
        try:
            emb = gt.sample_embeddings_at_points(points, year=annee)  # (N, 128)
        except Exception as exc:
            print(f"  Lot {i}: échec ({exc}) — on passe.")
            continue
        for (pid, _, _), vec in zip(lot, emb):
            v = [float(x) for x in list(vec)]
            e = existants.get(pid)
            if not e:
                e = ParcelleEmbedding(parcelle_id=pid, annee=annee)
                db.session.add(e)
                existants[pid] = e
            e.vecteur = json.dumps(v)
            e.source = f"TESSERA {DATASET_VERSION}/{DATASET_VARIANT}"
            n += 1
        db.session.commit()
        print(f"  {min(i + batch, len(pts))}/{len(pts)}")
    print(f"Terminé : {n} embeddings enregistrés.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--annee", type=int, default=2024)
    args = ap.parse_args()
    with app.app_context():
        run(args.annee)
