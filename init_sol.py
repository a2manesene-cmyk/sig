"""
Module Sol — création des tables et chargement initial.

Usage :
    python init_sol.py                          -> crée les tables
    python init_sol.py chemin\\base_IFDC.xlsx    -> crée les tables, importe la base IFDC
                                                   et rattache toutes les parcelles
"""
import sys

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.sol import PointSolIFDC, AnalyseSol, HistoriqueSol  # noqa: F401
from app.sol import service

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_sol_points_geom_utm ON sol_points_ifdc USING gist (geom_utm)"))
        db.session.commit()
        print("Tables sol_points_ifdc, analyses_sol, analyses_sol_historique prêtes.")

        n = service.reprendre_saisies_existantes()
        if n:
            print(f"{n} ancienne(s) saisie(s) de sol reprise(s) comme saisies CAR.")

        if len(sys.argv) > 1:
            with open(sys.argv[1], "rb") as f:
                r = service.importer_ifdc(f)
            print(f"{r['points']} points IFDC importés ({r['valeurs_ecartees']} valeurs hors plage écartées).")
            s = service.rattacher()
            print(f"Rattachement : {s['bonne']} fiabilité bonne, {s['indicative']} indicative, "
                  f"{s['hors']} hors couverture, {s['car_conserve']} saisies CAR conservées.")
