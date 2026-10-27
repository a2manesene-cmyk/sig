# -*- coding: utf-8 -*-
"""
Affecte la campagne « Hivernage 2026 » (créée et activée si besoin) à TOUTES
les parcelles, sans condition de programme. Corrige la colonne Campagne vide.
Idempotent. Usage : python affecter_campagne.py
"""
from app import create_app
from app.extensions import db
from app.models.campagne import Campagne
from app.models.parcelle import Parcelle

ANNEE, LIBELLE = 2026, "Hivernage 2026"

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        Campagne.query.update({Campagne.active: False})
        camp = Campagne.query.filter_by(annee=ANNEE).first()
        if not camp:
            camp = Campagne(libelle=LIBELLE, annee=ANNEE)
            db.session.add(camp)
            db.session.flush()
        camp.active = True
        n = Parcelle.query.update({Parcelle.campagne_id: camp.id},
                                  synchronize_session=False)
        db.session.commit()
        print(f"Campagne « {camp.libelle} » affectée à {n} parcelle(s).")
