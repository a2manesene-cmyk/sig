# -*- coding: utf-8 -*-
from datetime import datetime
from app.extensions import db


class Programme(db.Model):
    """Programme / projet (ex. Grenier du maïs, FSRP) auquel des parcelles sont rattachées."""
    __tablename__ = "programmes"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), unique=True, nullable=False)
    code = db.Column(db.String(30))
    bailleur = db.Column(db.String(120))
    actif = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
