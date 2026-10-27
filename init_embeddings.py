"""Crée la table parcelle_embeddings. Usage : python init_embeddings.py"""
from app import create_app
from app.extensions import db
from app.models.embedding import ParcelleEmbedding  # noqa
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Table parcelle_embeddings prête.")
