"""
Met à jour la culture de TOUTES les parcelles pour la campagne en cours.
Par défaut : « Maïs ». Modifiez la variable CULTURE si besoin.

ATTENTION : cette opération écrase la culture actuelle de toutes les parcelles.
Faites une sauvegarde de la base avant si nécessaire.

Usage : python set_culture_mais.py
"""
from app import create_app
from app.extensions import db
from app.models.parcelle import Parcelle

CULTURE = "Maïs"

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        n = Parcelle.query.update({Parcelle.culture: CULTURE}, synchronize_session=False)
        db.session.commit()
        print(f"{n} parcelle(s) mises à jour : culture = « {CULTURE} ».")
