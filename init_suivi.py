"""Crée la table de suivi de terrain. Ré-exécutable. Usage : python init_suivi.py"""
from app import create_app
from app.extensions import db
import app.models.suivi  # noqa (enregistre le modèle)
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Table 'suivis_terrain' créée (ou déjà présente).")
