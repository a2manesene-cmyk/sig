from app.extensions import db


class Campagne(db.Model):
    __tablename__ = "campagnes"

    id = db.Column(db.Integer, primary_key=True)
    libelle = db.Column(db.String(80), nullable=False)   # ex : Hivernage 2025
    annee = db.Column(db.Integer, nullable=False)
    date_debut = db.Column(db.Date)
    date_fin = db.Column(db.Date)
    active = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Campagne {self.libelle}>"
