# -*- coding: utf-8 -*-
from datetime import datetime, date
from app.extensions import db


class SuiviTerrain(db.Model):
    """Observation de terrain saisie par un animateur / CAR pour une parcelle."""
    __tablename__ = "suivis_terrain"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(db.Integer, db.ForeignKey("parcelles.id"), index=True, nullable=False)
    campagne_id = db.Column(db.Integer, db.ForeignKey("campagnes.id"), nullable=True)
    date_obs = db.Column(db.Date, default=date.today)
    phase = db.Column(db.String(60))          # phase de croissance
    actions = db.Column(db.Text)              # actions en cours
    tendance = db.Column(db.String(20))       # Amélioration / Stable / Dégradation
    difficultes = db.Column(db.Text)          # difficultés rencontrées
    auteur_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    parcelle = db.relationship("Parcelle")
    auteur = db.relationship("User")
    campagne = db.relationship("Campagne")
