import json
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    telephone = db.Column(db.String(30))
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)

    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    role = db.relationship("Role", back_populates="users")

    # Périmètre géographique / organisationnel
    zone_id = db.Column(db.Integer, db.ForeignKey("zones.id"))
    zone = db.relationship("Zone")
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id"))
    region = db.relationship("Region")
    organisation_id = db.Column(db.Integer, db.ForeignKey("organisations_producteurs.id"))
    organisation = db.relationship("OrganisationProducteur")

    # Listes (JSON) de départements / communes autorisés
    perim_departements = db.Column(db.Text)   # JSON : ["Nioro du Rip", ...]
    perim_communes = db.Column(db.Text)       # JSON : ["Darou salam", ...]
    perim_organisations = db.Column(db.Text)  # JSON : [12, 8, ...] (ids d'OP)

    @property
    def departements(self):
        try:
            return json.loads(self.perim_departements) if self.perim_departements else []
        except ValueError:
            return []

    @departements.setter
    def departements(self, valeurs):
        self.perim_departements = json.dumps(valeurs, ensure_ascii=False) if valeurs else None

    @property
    def communes(self):
        try:
            return json.loads(self.perim_communes) if self.perim_communes else []
        except ValueError:
            return []

    @communes.setter
    def communes(self, valeurs):
        self.perim_communes = json.dumps(valeurs, ensure_ascii=False) if valeurs else None

    @property
    def organisations(self):
        """Liste d'ids d'organisations autorisées (repli sur organisation_id)."""
        try:
            ids = json.loads(self.perim_organisations) if self.perim_organisations else []
        except ValueError:
            ids = []
        if not ids and self.organisation_id:
            ids = [self.organisation_id]
        out = []
        for x in ids:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                pass
        return out

    @organisations.setter
    def organisations(self, valeurs):
        ids = []
        for v in (valeurs or []):
            try:
                ids.append(int(v))
            except (TypeError, ValueError):
                pass
        self.perim_organisations = json.dumps(ids) if ids else None
        # cohérence héritée : garder organisation_id sur la première
        self.organisation_id = ids[0] if ids else None

    # --- Mots de passe ---
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # --- Helpers de rôle ---
    @property
    def niveau(self):
        return self.role.niveau if self.role else 0

    def a_role(self, *codes):
        return bool(self.role and self.role.code in codes)

    def __repr__(self):
        return f"<User {self.email}>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
