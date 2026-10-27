# -*- coding: utf-8 -*-
"""
Rattache le pic NDVI de chaque parcelle à sa référence de rendement, pour que
le modèle produise un RENDEMENT ESTIMÉ (colonne « Rendement estimé »).
Fonctionne quelle que soit la source des références (potentiel variété, terrain…).
Idempotent. Usage : python calibrer_ndvi.py
"""
from app import create_app
from app.extensions import db
from app.models.rendement import RendementReference
from app.models.indice import IndiceVegetation
from sqlalchemy import func

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        pics = dict(
            db.session.query(IndiceVegetation.parcelle_id, func.max(IndiceVegetation.ndvi))
            .filter(IndiceVegetation.ndvi.isnot(None))
            .group_by(IndiceVegetation.parcelle_id).all()
        )
        if not pics:
            print("Aucun NDVI calculé. Lancez d'abord Télédétection → Calculer.")
            raise SystemExit
        n_maj = n_sans = 0
        for r in RendementReference.query.all():
            nd = pics.get(r.parcelle_id)
            if nd is not None:
                r.ndvi_max = nd
                n_maj += 1
            else:
                n_sans += 1
        db.session.commit()
        print(f"NDVI rattaché à {n_maj} référence(s) ; {n_sans} sans NDVI. "
              f"La colonne « Rendement estimé » va se remplir.")
