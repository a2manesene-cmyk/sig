"""Ajoute le rôle ANIMATEUR (niveau 2) s'il n'existe pas. Ré-exécutable."""
from app import create_app
from app.extensions import db
from app.models.role import Role
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        if not Role.query.filter_by(code="ANIMATEUR").first():
            db.session.add(Role(code="ANIMATEUR", libelle="Animateur", niveau=2))
            db.session.commit()
            print("Rôle ANIMATEUR (niveau 2) ajouté.")
        else:
            print("Rôle ANIMATEUR déjà présent.")
