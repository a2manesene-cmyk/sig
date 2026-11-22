"""
Indices de végétation à chaque passage de Sentinel-2 (Google Earth Engine), en arrière-plan.

Usage :
    python calculer_indices.py                 -> complète chaque parcelle : depuis le 15 mai si elle n'a
                                                  pas d'historique, sinon depuis son dernier passage
    python calculer_indices.py --tout          -> recalcule toutes les parcelles depuis le 15 mai
    python calculer_indices.py --debut 2026-05-15 --programme "Grenier du maïs"
    python calculer_indices.py --parcelles 101,102

À programmer tous les 5 jours (revisite de Sentinel-2), par exemple dans la crontab :
    0 3 */5 * *  cd /chemin/ancar_platform && venv/bin/python calculer_indices.py >> instance/indices_cron.log 2>&1
"""
import argparse
import sys
from datetime import date

from app import create_app
from app.extensions import db
from app.teledetection import series

app = create_app()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tout", action="store_true")
    ap.add_argument("--debut")
    ap.add_argument("--fin")
    ap.add_argument("--programme")
    ap.add_argument("--parcelles")
    ap.add_argument("--force", action="store_true", help=argparse.SUPPRESS)   # lancé par la plateforme
    a = ap.parse_args()
    with app.app_context():
        if series.en_cours() and not a.force:
            print("Un calcul est déjà en cours (voir Télédétection → Calcul des indices).")
            sys.exit(0)
        pids = None
        if a.parcelles:
            pids = {int(x) for x in a.parcelles.split(",") if x.strip()}
        if a.programme:
            from app.models.parcelle import Parcelle
            from app.models.programme import Programme
            pr = Programme.query.filter(db.func.lower(Programme.nom) == a.programme.lower()).first()
            if pr is None:
                print(f"Programme « {a.programme} » introuvable.")
                series.ecrire_statut(etat="terminé avec erreurs", message=f"Programme « {a.programme} » introuvable")
                sys.exit(1)
            ids = {pid for (pid,) in db.session.query(Parcelle.id).filter_by(programme_id=pr.id)}
            pids = ids if pids is None else pids & ids
        try:
            r = series.calculer(debut=date.fromisoformat(a.debut) if a.debut else None,
                                fin=date.fromisoformat(a.fin) if a.fin else None,
                                pids=pids, tout=a.tout)
        except Exception as exc:
            series.journal(f"Arrêt : {exc}")
            series.ecrire_statut(etat="terminé avec erreurs", message=str(exc)[:300])
            raise
        print(r)
