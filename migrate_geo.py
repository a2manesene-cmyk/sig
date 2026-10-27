"""
Ajoute les colonnes departement / commune / village à la table parcelles.
Sûr et ré-exécutable (ADD COLUMN IF NOT EXISTS).
À lancer AVANT de redémarrer l'application avec le nouveau modèle.
Usage : python migrate_geo.py
"""
from sqlalchemy import text
from app import create_app
from app.extensions import db

app = create_app()

DDL = [
    "ALTER TABLE parcelles ADD COLUMN IF NOT EXISTS departement varchar(80)",
    "ALTER TABLE parcelles ADD COLUMN IF NOT EXISTS commune varchar(80)",
    "ALTER TABLE parcelles ADD COLUMN IF NOT EXISTS village varchar(120)",
    "CREATE INDEX IF NOT EXISTS ix_parcelles_departement ON parcelles (departement)",
    "CREATE INDEX IF NOT EXISTS ix_parcelles_commune ON parcelles (commune)",
]

if __name__ == "__main__":
    with app.app_context():
        for ddl in DDL:
            db.session.execute(text(ddl))
        db.session.commit()
        print("Colonnes géographiques ajoutées à parcelles (departement, commune, village).")
