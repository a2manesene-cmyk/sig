"""
Module Climat — création des tables, grille climatique et import de l'historique.

Usage :
    python init_climat.py                 -> crée les tables et la grille (cellules de 5 km)
    python init_climat.py 2026            -> + importe l'année 2026 (janvier – décembre : la saison des
                                             pluies et la contre-saison, utile au maraîchage)
    python init_climat.py 2016 2026       -> + importe les saisons 2016 à 2026 (une par une)

Prérequis : earthengine-api installé et authentifié (comme pour la télédétection),
GEE_PROJECT renseigné dans config.py.
"""
import sys
from datetime import date, timedelta

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.climat import CelluleClimat, ParcelleCellule, ClimatJournalier, SaisonClimat  # noqa: F401
from app.climat import service

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        db.session.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_climat_cellules_geom ON climat_cellules USING gist (geom)"))
        db.session.commit()
        print("Tables climat_cellules, climat_parcelles, climat_journalier, climat_saisons prêtes.")
        r = service.affecter_cellules()
        print(f"Grille : {r['cellules']} cellules de 5 km pour {r['parcelles']} parcelles.")

        if len(sys.argv) > 1:
            a1 = int(sys.argv[1])
            a2 = int(sys.argv[2]) if len(sys.argv) > 2 else a1
            project = app.config.get("GEE_PROJECT")
            hier = date.today() - timedelta(days=1)
            for a in range(a1, a2 + 1):
                d1, d2 = date(a, 1, 1), min(date(a, 12, 31), hier)
                if d2 < d1:
                    continue
                print(f"Import {a} ({d1} -> {d2})…", flush=True)
                try:
                    res = service.importer_periode(d1, d2, project)
                    print(f"  {res['lignes']} relevés ({res['cellules']} cellules, {res['jours']} jours).")
                except Exception as exc:
                    db.session.rollback()
                    print(f"  ÉCHEC {a} : {exc}")
            print("Terminé.")
