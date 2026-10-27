"""Ajoute la colonne perim_organisations (liste d'OP) à users. Ré-exécutable.
Usage : python migrate_op_multi.py"""
from sqlalchemy import text
from app import create_app
from app.extensions import db
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.session.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS perim_organisations text"))
        db.session.commit()
        print("Colonne 'perim_organisations' ajoutée à users.")
