# -*- coding: utf-8 -*-
"""
Remplace les rendements de référence DAPSA par le POTENTIEL de la variété
(Mastrop, estimé entre 4 et 5 t/ha -> 4500 kg/ha par défaut), modulable par
parcelle (champ parcelles.rendement_potentiel). Idempotent.
Usage : python reporter_rendement_variete.py
"""
from app import create_app
from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.rendement import RendementReference

POTENTIEL_DEFAUT = 4500          # milieu de 4–5 t/ha
VARIETE = "Mastrop"
ANNEE = 2026
SOURCE = f"Potentiel variété ({VARIETE})"

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        # 1) Retirer les anciennes références DAPSA
        n_del = (RendementReference.query
                 .filter(RendementReference.source.ilike("DAPSA%"))
                 .delete(synchronize_session=False))

        # 2) Potentiel par parcelle (défaut si non renseigné) + référence
        existants = {(r.parcelle_id, r.annee): r
                     for r in RendementReference.query.filter_by(annee=ANNEE).all()}
        n_ok = n_maj = 0
        for p in Parcelle.query.all():
            if p.rendement_potentiel is None:
                p.rendement_potentiel = POTENTIEL_DEFAUT
            pot = p.rendement_potentiel
            ref = existants.get((p.id, ANNEE))
            if ref:
                ref.rendement_kg_ha = pot
                ref.culture = p.culture or VARIETE
                ref.source = SOURCE
                n_maj += 1
            else:
                db.session.add(RendementReference(
                    parcelle_id=p.id, annee=ANNEE, culture=(p.culture or VARIETE),
                    rendement_kg_ha=pot, source=SOURCE))
                n_ok += 1
        db.session.commit()
        print(f"DAPSA retiré : {n_del} référence(s) supprimée(s).")
        print(f"Potentiel « {VARIETE} » ({POTENTIEL_DEFAUT} kg/ha par défaut) appliqué : "
              f"{n_ok} créé(s), {n_maj} mis à jour.")
