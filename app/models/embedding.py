from datetime import datetime

from app.extensions import db


class ParcelleEmbedding(db.Model):
    """Vecteur d'embedding TESSERA (128 dimensions) échantillonné pour une parcelle."""
    __tablename__ = "parcelle_embeddings"

    id = db.Column(db.Integer, primary_key=True)
    parcelle_id = db.Column(
        db.Integer, db.ForeignKey("parcelles.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    annee = db.Column(db.Integer)
    source = db.Column(db.String(40), default="TESSERA v1.1")
    vecteur = db.Column(db.Text)          # JSON : liste de 128 flottants
    date_maj = db.Column(db.DateTime, default=datetime.utcnow)

    parcelle = db.relationship("Parcelle")

    __table_args__ = (
        db.UniqueConstraint("parcelle_id", "annee", name="uq_embedding_parcelle_annee"),
    )
