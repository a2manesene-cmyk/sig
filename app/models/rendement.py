from datetime import datetime

from app.extensions import db


class RendementReference(db.Model):
    """Rendement réellement mesuré sur une parcelle (calibration)."""
    __tablename__ = "rendements_reference"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(
        db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    annee = db.Column(db.Integer)
    culture = db.Column(db.String(80))          # instantané au moment de la saisie
    rendement_kg_ha = db.Column(db.Float, nullable=False)
    ndvi_max = db.Column(db.Float)              # instantané du pic NDVI
    source = db.Column(db.String(40))          # ex. DAPSA (département), Mesure terrain
    date_saisie = db.Column(db.DateTime, default=datetime.utcnow)

    parcelle = db.relationship("Parcelle")

    def __repr__(self):
        return f"<RendRef parcelle={self.parcelle_id} {self.rendement_kg_ha} kg/ha>"
