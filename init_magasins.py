"""
Module Magasins des OP — création de la table et chargement initial.

Usage :
    python init_magasins.py                    -> crée la table ; si elle est vide, charge app/magasins/donnees/magasins_op.geojson
    python init_magasins.py fichier.geojson    -> crée la table et importe le fichier (mise à jour par code)
"""
import os
import sys

from app import create_app
from app.extensions import db
from app.models.magasin import MagasinOP
from app.magasins import service

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Table magasins_op prête.")
        chemin = sys.argv[1] if len(sys.argv) > 1 else None
        if chemin is None and MagasinOP.query.count() == 0:
            defaut = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "magasins", "donnees", "magasins_op.geojson")
            chemin = defaut if os.path.exists(defaut) else None
        if chemin:
            with open(chemin, encoding="utf-8-sig") as f:
                b = service.importer(f.read())
            print(f"Magasins : {b['crees']} ajouté(s), {b['maj']} mis à jour, {b['sans_position']} sans position GPS.")
            if b["sans_op"]:
                print("OP non reconnues : " + ", ".join(sorted(set(b["sans_op"]))))
        print(f"{MagasinOP.query.count()} magasin(s) enregistré(s).")
