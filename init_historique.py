"""
Crée la table historique_cultural.
Usage : python init_historique.py
"""
from app import create_app
from app.extensions import db

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # ne touche pas aux tables existantes
        print("Table historique_cultural prête.")
