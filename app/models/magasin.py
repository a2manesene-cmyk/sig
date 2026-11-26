"""
Magasins de stockage des organisations de producteurs (OP).

Un magasin appartient à une OP ; il a une position GPS (peut manquer, à compléter),
une capacité en tonnes et un responsable. La capacité est comparée à la production
attendue des parcelles de l'OP (module Rendement).
"""
from datetime import datetime

from geoalchemy2 import Geometry

from app.extensions import db


class MagasinOP(db.Model):
    __tablename__ = "magasins_op"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False, index=True)     # ex. MAG-01
    organisation_id = db.Column(db.Integer, db.ForeignKey("organisations_producteurs.id"), index=True)
    organisation = db.relationship("OrganisationProducteur")
    nom_op_source = db.Column(db.String(150))       # nom de l'OP tel qu'écrit dans le fichier
    localite = db.Column(db.String(120))
    commune = db.Column(db.String(120))
    type = db.Column(db.String(60))                 # Magasin de la coopérative, en location…
    responsable = db.Column(db.String(120))
    telephone = db.Column(db.String(30))
    longueur_m = db.Column(db.Float)
    largeur_m = db.Column(db.Float)
    capacite_t = db.Column(db.Float)
    observations = db.Column(db.Text)
    geom = db.Column(Geometry(geometry_type="POINT", srid=4326))
    maj_le = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def surface_m2(self):
        return round(self.longueur_m * self.largeur_m, 1) if self.longueur_m and self.largeur_m else None

    def __repr__(self):
        return f"<Magasin {self.code}>"
