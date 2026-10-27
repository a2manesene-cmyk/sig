# -*- coding: utf-8 -*-
"""
Diagnostic : liste les producteurs NON évaluables en bancabilité et la raison.
Catégories : sans parcelle / parcelles sans rendement / département non reconnu.
Lecture seule (ne modifie rien). Usage : python diagnostic_bancabilite.py
"""
import unicodedata
from collections import Counter
from app import create_app
from app.extensions import db
from app.models.producteur import Producteur
from app.models.parcelle import Parcelle
from app.models.rendement import RendementReference

# Départements couverts par les rendements DAPSA (mêmes clés que le report)
try:
    from reporter_rendement_dapsa import DAPSA as _DAPSA
    DEPTS_DAPSA = set(_DAPSA.keys())
except Exception:
    DEPTS_DAPSA = set()


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().replace("-", " ").split())


app = create_app()
if __name__ == "__main__":
    with app.app_context():
        sans_parcelle, sans_rdt, dept_ko = [], [], []
        depts_inconnus = Counter()
        total = evaluables = 0
        for prod in Producteur.query.all():
            total += 1
            parcelles = Parcelle.query.filter_by(producteur_id=prod.id).all()
            if not parcelles:
                sans_parcelle.append(prod.nom_complet)
                continue
            pids = [p.id for p in parcelles]
            n_rdt = (RendementReference.query
                     .filter(RendementReference.parcelle_id.in_(pids),
                             RendementReference.annee.isnot(None)).count())
            if n_rdt > 0:
                evaluables += 1
                continue
            # parcelles mais pas de rendement : département reconnu ?
            depts = {_norm(p.departement) for p in parcelles if p.departement}
            inconnus = [d for d in depts if DEPTS_DAPSA and d not in DEPTS_DAPSA]
            if inconnus:
                for d in inconnus:
                    depts_inconnus[d] += 1
                dept_ko.append((prod.nom_complet, ", ".join(inconnus)))
            else:
                sans_rdt.append(prod.nom_complet)

        print(f"Producteurs : {total} | évaluables : {evaluables} | "
              f"non évaluables : {total - evaluables}")
        print(f"  • Sans aucune parcelle            : {len(sans_parcelle)}")
        print(f"  • Parcelles mais sans rendement   : {len(sans_rdt)}")
        print(f"  • Département non reconnu (DAPSA)  : {len(dept_ko)}")
        if depts_inconnus:
            print("\nDépartements non reconnus (à corriger dans les parcelles) :")
            for d, n in depts_inconnus.most_common():
                print(f"    - {d} : {n} producteur(s)")
        def ap(titre, lst):
            if lst:
                print(f"\n{titre} (jusqu'à 15) :")
                for x in lst[:15]:
                    print("    -", x if isinstance(x, str) else f"{x[0]}  [{x[1]}]")
        ap("Sans parcelle", sans_parcelle)
        ap("Parcelles sans rendement", sans_rdt)
        ap("Département non reconnu", dept_ko)
