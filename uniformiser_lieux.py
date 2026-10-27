# -*- coding: utf-8 -*-
"""
Uniformise les champs 'departement' et 'commune' des parcelles vers une
écriture canonique unique (casse et accents harmonisés), pour supprimer les
doublons du type 'KAFFRINE' / 'Kaffrine'. Idempotent.
Usage : python uniformiser_lieux.py
"""
from collections import Counter
from app import create_app
from app.extensions import db
from app.models.parcelle import Parcelle
from app.rendement.calcul import cle_lieu, belle_casse

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        # Pour chaque clé normalisée, choisir le libellé le plus fréquent déjà
        # présent (sinon la belle casse) comme forme canonique.
        def canoniser(champ):
            variantes = {}   # cle -> Counter(libellés bruts)
            for (val,) in db.session.query(getattr(Parcelle, champ)).filter(
                    getattr(Parcelle, champ).isnot(None)).all():
                k = cle_lieu(val)
                if not k:
                    continue
                variantes.setdefault(k, Counter())[val.strip()] += 1
            n_modif = 0
            for k, cnt in variantes.items():
                canon = belle_casse(k)            # forme propre et stable
                for brut in cnt:
                    if brut != canon:
                        n = (Parcelle.query
                             .filter(getattr(Parcelle, champ) == brut)
                             .update({getattr(Parcelle, champ): canon},
                                     synchronize_session=False))
                        n_modif += n or 0
            return len(variantes), n_modif

        nd, md = canoniser("departement")
        nc, mc = canoniser("commune")
        db.session.commit()
        print(f"Départements : {nd} valeurs uniques après harmonisation, {md} parcelle(s) corrigée(s).")
        print(f"Communes     : {nc} valeurs uniques après harmonisation, {mc} parcelle(s) corrigée(s).")
