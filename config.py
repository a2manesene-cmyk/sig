import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # --- Sécurité ---
    SECRET_KEY = "ancar-cle-secrete-a-changer-en-production"

    # --- Base de données PostgreSQL / PostGIS ---
    # Identifiants en clair : à adapter à votre installation locale.
    DB_USER = "ancar"
    DB_PASSWORD = "ancar"
    DB_HOST = "localhost"
    DB_PORT = "5432"
    DB_NAME = "ancar_db"

    SQLALCHEMY_DATABASE_URI = (

        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?client_encoding=utf8"
    )
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEE_PROJECT = "ancarsi-f07fd"   # ID de ton projet Google Cloud/Earth Engine
    # Fuseau / divers
    LANG = "fr"
