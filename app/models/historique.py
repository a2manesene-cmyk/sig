from app.extensions import db


class HistoriqueCultural(db.Model):
    __tablename__ = "historique_cultural"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(
        db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    campagne_id = db.Column(
        db.Integer, db.ForeignKey("campagnes.id"), nullable=False, index=True,
    )
    annee = db.Column(db.Integer)          # copie de campagne.annee (tri rapide)
    culture = db.Column(db.String(80))
    source = db.Column(db.String(50), default="GeoJSON")

    parcelle = db.relationship(
        "Parcelle",
        backref=db.backref("historique", cascade="all, delete-orphan"),
    )
    campagne = db.relationship("Campagne")

    __table_args__ = (
        db.UniqueConstraint("parcelle_id", "campagne_id", name="uq_hist_parcelle_campagne"),
    )

    def __repr__(self):
        return f"<Hist parcelle={self.parcelle_id} campagne={self.campagne_id} {self.culture}>"
