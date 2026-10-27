# -*- coding: utf-8 -*-
"""
Rattache le pic NDVI de chaque parcelle à sa référence de rendement,
pour permettre au modèle de produire un RENDEMENT ESTIMÉ.

ATTENTION : si les références sont des moyennes DAPSA (par département),
les estimations obtenues sont INDICATIVES (calibration approximative) et
seront remplacées par de vraies mesures à la parcelle le moment venu.

Idempotent. Usage : python calibrer_dapsa_ndvi.py
"""
from app import create_app
from app.extensions import db
from app.models.rendement import RendementReference
from app.models.indice import IndiceVegetation
from sqlalchemy import func

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        # pic NDVI par parcelle
        pics = dict(
            db.session.query(IndiceVegetation.parcelle_id, func.max(IndiceVegetation.ndvi))
            .filter(IndiceVegetation.ndvi.isnot(None))
            .group_by(IndiceVegetation.parcelle_id).all()
        )
        if not pics:
            print("Aucun NDVI calculé en base. Lancez d'abord Télédétection → Calculer, "
                  "puis relancez ce script.")
            raise SystemExit

        n_maj, n_sans = 0, 0
        for r in RendementReference.query.all():
            nd = pics.get(r.parcelle_id)
            if nd is not None:
                r.ndvi_max = nd
                n_maj += 1
            else:
                n_sans += 1
        db.session.commit()
        print(f"NDVI rattaché à {n_maj} référence(s) ; {n_sans} sans NDVI (parcelle non calculée).")
        print("Le rendement estimé va maintenant s'afficher (valeurs indicatives tant que la "
              "calibration repose sur des moyennes DAPSA).")
