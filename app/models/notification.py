from datetime import datetime
from app.extensions import db


class NotificationLog(db.Model):
    __tablename__ = "notifications_log"
    id = db.Column(db.Integer, primary_key=True)
    producteur_id = db.Column(db.Integer, db.ForeignKey("producteurs.id", ondelete="SET NULL"))
    numero = db.Column(db.String(20))
    canal = db.Column(db.String(20), default="whatsapp")
    langue = db.Column(db.String(5))
    type_alerte = db.Column(db.String(30))
    statut = db.Column(db.String(12))          # "envoye" / "echec"
    detail = db.Column(db.Text)
    date_envoi = db.Column(db.DateTime, default=datetime.utcnow)

    producteur = db.relationship("Producteur")
