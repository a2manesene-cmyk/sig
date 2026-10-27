from app.extensions import db


class IndiceVegetation(db.Model):
    __tablename__ = "indices_vegetation"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(
        db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    date = db.Column(db.Date, nullable=False, index=True)

    ndvi = db.Column(db.Float)   # végétation (B8-B4)/(B8+B4)
    ndwi = db.Column(db.Float)   # eau de surface (B3-B8)/(B3+B8)
    ndmi = db.Column(db.Float)   # humidité (B8-B11)/(B8+B11)
    savi = db.Column(db.Float)   # végétation ajustée au sol
    evi = db.Column(db.Float)    # végétation amélioré

    source = db.Column(db.String(50), default="GEE Sentinel-2")

    parcelle = db.relationship(
        "Parcelle",
        backref=db.backref("indices", cascade="all, delete-orphan"),
    )

    __table_args__ = (
        db.UniqueConstraint("parcelle_id", "date", name="uq_indice_parcelle_date"),
    )

    def __repr__(self):
        return f"<Indice parcelle={self.parcelle_id} {self.date} ndvi={self.ndvi}>"
