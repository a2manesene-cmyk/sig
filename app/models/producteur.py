from app.extensions import db


class Producteur(db.Model):
    __tablename__ = "producteurs"

    id = db.Column(db.Integer, primary_key=True)
    prenom = db.Column(db.String(80))
    nom = db.Column(db.String(80), nullable=False)
    telephone = db.Column(db.String(30))
    sexe = db.Column(db.String(1))   # H / F
    langue = db.Column(db.String(5))  # fr / wo / ff … (préférence de communication)

    organisation_id = db.Column(db.Integer, db.ForeignKey("organisations_producteurs.id"))
    organisation = db.relationship("OrganisationProducteur", back_populates="producteurs")

    region_id = db.Column(db.Integer, db.ForeignKey("regions.id"))
    region = db.relationship("Region")

    conseiller_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    conseiller = db.relationship("User")

    parcelles = db.relationship("Parcelle", back_populates="producteur")

    @property
    def nom_complet(self):
        return f"{self.prenom or ''} {self.nom}".strip()

    def __repr__(self):
        return f"<Producteur {self.nom_complet}>"
