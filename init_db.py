"""
Initialisation de la base de données PostgreSQL/PostGIS.

Ce script :
  1. active l'extension PostGIS ;
  2. crée les tables ;
  3. insère les données de référence (rôles, zones, régions, filières, campagne) ;
  4. crée un compte administrateur par défaut.

Usage :  python init_db.py
"""
from datetime import date

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.role import Role
from app.models.user import User
from app.models.zone import Zone, Region
from app.models.filiere import Filiere
from app.models.campagne import Campagne

app = create_app()

# --- Données de référence ---------------------------------------------------
ROLES = [
    ("ADMIN", "Administrateur", 5),
    ("DG", "Direction Générale", 4),
    ("DZ", "Direction de Zone", 3),
    ("CDCAR", "Coordonnateur Départemental (CDCAR)", 2),
    ("CAR", "Conseil Agricole Rural (CAR)", 2),
    ("CONSEILLER", "Conseiller agricole", 2),
    ("OP", "Organisation de Producteurs", 1),
    ("LECTEUR", "Consultation", 1),
]

# Directions de Zone de l'ANCAR (indépendantes des régions)
ZONES = [
    ("NIAYES", "Zone des Niayes"),
    ("BAN", "Bassin Arachidier Nord"),
    ("BAS", "Bassin Arachidier Sud"),
    ("BMC", "Basse et Moyenne Casamance"),
    ("SOHC", "Sénégal Oriental et Haute Casamance"),
    ("SP", "Zone Sylvo-Pastorale"),
    ("VFS", "Vallée du Fleuve Sénégal"),
]

# Régions administratives du Sénégal (autonomes)
REGIONS = [
    "Dakar", "Thiès", "Diourbel", "Fatick", "Kaolack", "Kaffrine", "Louga",
    "Saint-Louis", "Matam", "Tambacounda", "Kédougou", "Kolda", "Sédhiou",
    "Ziguinchor",
]

FILIERES = ["Riz", "Arachide", "Maïs", "Niébé", "Sorgho", "Horticulture", "Mil"]

ADMIN_EMAIL = "admin@ancar.sn"
ADMIN_PASSWORD = "Ancar2026!"


def seed():
    # 1. Extension PostGIS
    db.session.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
    db.session.commit()

    # 2. Tables
    db.create_all()

    # 3. Rôles
    if not Role.query.first():
        for code, libelle, niveau in ROLES:
            db.session.add(Role(code=code, libelle=libelle, niveau=niveau))
        db.session.commit()
        print("  - Rôles créés")

    # 4. Zones (Directions de Zone) et régions — indépendantes
    if not Zone.query.first():
        for code, nom in ZONES:
            db.session.add(Zone(code=code, nom=nom))
        db.session.commit()
        print("  - Zones créées")
    if not Region.query.first():
        for nom in REGIONS:
            db.session.add(Region(nom=nom))
        db.session.commit()
        print("  - Régions créées")

    # 5. Filières
    if not Filiere.query.first():
        for nom in FILIERES:
            db.session.add(Filiere(nom=nom))
        db.session.commit()
        print("  - Filières créées")

    # 6. Campagne active
    if not Campagne.query.first():
        db.session.add(Campagne(
            libelle="Hivernage 2025",
            annee=2025,
            date_debut=date(2025, 6, 1),
            date_fin=date(2025, 11, 30),
            active=True,
        ))
        db.session.commit()
        print("  - Campagne active créée")

    # 7. Administrateur par défaut
    if not User.query.filter_by(email=ADMIN_EMAIL).first():
        admin_role = Role.query.filter_by(code="ADMIN").first()
        admin = User(nom="Administrateur", email=ADMIN_EMAIL, role=admin_role)
        admin.set_password(ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()
        print(f"  - Administrateur créé : {ADMIN_EMAIL} / {ADMIN_PASSWORD}")


if __name__ == "__main__":
    with app.app_context():
        print("Initialisation de la base de données…")
        seed()
        print("Terminé.")
