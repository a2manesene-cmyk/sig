"""
Crée la table rendements_reference (calibration du rendement).
Usage : python init_rendement.py
"""
from app import create_app
from app.extensions import db
from app.models.rendement import RendementReference  # noqa

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Table rendements_reference prête.")
