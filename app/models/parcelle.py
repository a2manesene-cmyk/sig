from datetime import datetime

from geoalchemy2 import Geometry

from app.extensions import db


class Parcelle(db.Model):
    __tablename__ = "parcelles"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True)
    surface_ha = db.Column(db.Float)
    culture = db.Column(db.String(80))
    observations = db.Column(db.Text)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    # Localisation administrative (texte, issue du GeoJSON) — utilisée pour le cloisonnement
    departement = db.Column(db.String(80), index=True)
    commune = db.Column(db.String(80), index=True)
    village = db.Column(db.String(120))
    animateur = db.Column(db.String(120))   # agent/enquêteur ayant cartographié

    # Géométrie PostGIS : multipolygone en WGS84 (EPSG:4326)
    geom = db.Column(Geometry(geometry_type="MULTIPOLYGON", srid=4326))

    producteur_id = db.Column(db.Integer, db.ForeignKey("producteurs.id"))
    producteur = db.relationship("Producteur", back_populates="parcelles")

    filiere_id = db.Column(db.Integer, db.ForeignKey("filieres.id"))
    filiere = db.relationship("Filiere")

    campagne_id = db.Column(db.Integer, db.ForeignKey("campagnes.id"))
    campagne = db.relationship("Campagne")

    conseiller_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    conseiller = db.relationship("User")

    region_id = db.Column(db.Integer, db.ForeignKey("regions.id"))
    region = db.relationship("Region")

    zone_id = db.Column(db.Integer, db.ForeignKey("zones.id"))
    zone = db.relationship("Zone")

    def __repr__(self):
        return f"<Parcelle {self.code}>"
