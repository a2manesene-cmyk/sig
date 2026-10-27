"""Crée la table notifications_log. Usage : python init_notifications.py"""
from app import create_app
from app.extensions import db
from app.models.notification import NotificationLog  # noqa
app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Table notifications_log prête.")
