from app.extensions import db


class Filiere(db.Model):
    __tablename__ = "filieres"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(80), unique=True, nullable=False)

    def __repr__(self):
        return f"<Filiere {self.nom}>"
