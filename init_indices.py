"""
Crée la table indices_vegetation (télédétection).
Usage : python init_indices.py
"""
from app import create_app
from app.extensions import db

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # ne touche pas aux tables existantes
        print("Table indices_vegetation prête.")
