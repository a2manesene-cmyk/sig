"""
Active la campagne courante (Hivernage 2026) et désactive les autres.
Crée la campagne 2026 si elle n'existe pas. Ré-exécutable.
Usage : python activer_campagne_2026.py
"""
from app import create_app
from app.extensions import db
from app.models.campagne import Campagne

ANNEE = 2026
LIBELLE = "Hivernage 2026"

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        Campagne.query.update({Campagne.active: False})
        camp = Campagne.query.filter_by(annee=ANNEE).first()
        if not camp:
            camp = Campagne(libelle=LIBELLE, annee=ANNEE)
            db.session.add(camp)
        camp.active = True
        db.session.commit()
        print(f"Campagne active : « {camp.libelle} » ({camp.annee}). Autres désactivées.")
