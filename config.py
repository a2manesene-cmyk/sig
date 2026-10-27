import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    # --- Sécurité ---
    SECRET_KEY = os.getenv("SECRET_KEY", "ancar-cle-secrete-a-changer-en-production")

    # --- Base de données PostgreSQL / PostGIS ---
    DB_USER = os.getenv("DB_USER", "ancar")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "ancar")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "ancar_db")

    SQLALCHEMY_DATABASE_URI = (
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?client_encoding=utf8"
    )
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEE_PROJECT = os.getenv("GEE_PROJECT", "ancarsi-f07fd")
    # Fuseau / divers
    LANG = "fr"
