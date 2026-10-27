"""Ajoute la colonne 'langue' à producteurs. Ré-exécutable.
Usage : python migrate_langue_producteur.py"""
from sqlalchemy import text
from app import create_app
from app.extensions import db
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.session.execute(text(
            "ALTER TABLE producteurs ADD COLUMN IF NOT EXISTS langue varchar(5)"))
        db.session.commit()
        print("Colonne 'langue' ajoutée à producteurs.")
