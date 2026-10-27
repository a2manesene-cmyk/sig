from app.extensions import db


class Role(db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)   # ADMIN, DG, DZ, CONSEILLER, LECTEUR
    libelle = db.Column(db.String(80), nullable=False)
    niveau = db.Column(db.Integer, nullable=False, default=1)      # hiérarchie 1..5

    users = db.relationship("User", back_populates="role")

    def __repr__(self):
        return f"<Role {self.code}>"
