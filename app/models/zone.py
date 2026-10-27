from app.extensions import db


class Zone(db.Model):
    """Direction de Zone (DZ) de l'ANCAR — regroupe plusieurs régions."""
    __tablename__ = "zones"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    nom = db.Column(db.String(120), nullable=False)

    regions = db.relationship("Region", back_populates="zone")

    def __repr__(self):
        return f"<Zone {self.nom}>"


class Region(db.Model):
    __tablename__ = "regions"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)

    zone_id = db.Column(db.Integer, db.ForeignKey("zones.id"))
    zone = db.relationship("Zone", back_populates="regions")

    def __repr__(self):
        return f"<Region {self.nom}>"
