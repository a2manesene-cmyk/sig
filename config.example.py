# -*- coding: utf-8 -*-
"""Modèle de configuration. Copiez ce fichier en « config.py » et renseignez vos valeurs.
NE COMMITEZ PAS config.py (identifiants)."""
class Config:
    SECRET_KEY = "a-changer-par-une-cle-secrete-longue"
    SQLALCHEMY_DATABASE_URI = (
        "postgresql://ancar:MOT_DE_PASSE@localhost:5432/ancar_db?client_encoding=utf8")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GEE_PROJECT = "votre-projet-gee"
