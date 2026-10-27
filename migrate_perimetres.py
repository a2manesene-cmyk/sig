"""
Prépare le cloisonnement :
- ajoute les colonnes de périmètre à users (organisation_id, perim_departements, perim_communes)
- crée les rôles CDCAR, CAR, OP s'ils manquent
Sûr et ré-exécutable. À lancer AVANT de redémarrer l'application.
Usage : python migrate_perimetres.py
"""
from sqlalchemy import text
from app import create_app
from app.extensions import db
from app.models.role import Role

app = create_app()

DDL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS organisation_id integer",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS perim_departements text",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS perim_communes text",
]

ROLES = [
    ("CDCAR", "Coordonnateur Départemental (CDCAR)", 2),
    ("CAR", "Conseil Agricole Rural (CAR)", 2),
    ("OP", "Organisation de Producteurs", 1),
]

if __name__ == "__main__":
    with app.app_context():
        for ddl in DDL:
            db.session.execute(text(ddl))
        db.session.commit()
        for code, libelle, niveau in ROLES:
            if not Role.query.filter_by(code=code).first():
                db.session.add(Role(code=code, libelle=libelle, niveau=niveau))
        db.session.commit()
        print("Colonnes de périmètre ajoutées et rôles CDCAR / CAR / OP en place.")
