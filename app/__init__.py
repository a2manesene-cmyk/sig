from flask import Flask

from config import Config
from .extensions import db, login_manager, migrate


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialisation des extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)

    # Import des modèles (nécessaire pour l'enregistrement des tables)
    from . import models  # noqa: F401

    # Enregistrement des blueprints
    from .auth.routes import auth_bp
    from .main.routes import main_bp
    from .parcelles.routes import parcelles_bp
    from .producteurs.routes import producteurs_bp
    from .teledetection.routes import teledetection_bp
    from .alertes.routes import alertes_bp
    from .utilisateurs.routes import utilisateurs_bp
    from .export.routes import export_bp
    from .rendement.routes import rendement_bp
    from .bancabilite.routes import bancabilite_bp
    from .financement.routes import financement_bp
    from .sol.routes import sol_bp
    from .climat.routes import climat_bp
    from .referentiel.routes import referentiel_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(parcelles_bp)
    app.register_blueprint(producteurs_bp)
    app.register_blueprint(teledetection_bp)
    app.register_blueprint(alertes_bp)
    app.register_blueprint(utilisateurs_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(rendement_bp)
    app.register_blueprint(bancabilite_bp)
    app.register_blueprint(financement_bp)
    app.register_blueprint(sol_bp)
    app.register_blueprint(climat_bp)
    app.register_blueprint(referentiel_bp)

    # Contexte Jinja utile aux templates
    @app.context_processor
    def inject_globals():
        return {"app_nom": "Plateforme Nationale de Suivi Intelligent — ANCAR"}

    return app
