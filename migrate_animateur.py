"""Ajoute la colonne 'animateur' à parcelles. Ré-exécutable.
Usage : python migrate_animateur.py"""
from sqlalchemy import text
from app import create_app
from app.extensions import db
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.session.execute(text(
            "ALTER TABLE parcelles ADD COLUMN IF NOT EXISTS animateur varchar(120)"))
        db.session.commit()
        print("Colonne 'animateur' ajoutée à parcelles.")
