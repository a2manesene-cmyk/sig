from app.extensions import db


class OrganisationProducteur(db.Model):
    __tablename__ = "organisations_producteurs"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(150), nullable=False)
    type = db.Column(db.String(40))   # Coopérative, GIE, Union, Fédération

    region_id = db.Column(db.Integer, db.ForeignKey("regions.id"))
    region = db.relationship("Region")

    producteurs = db.relationship("Producteur", back_populates="organisation")

    def __repr__(self):
        return f"<OP {self.nom}>"
