"""
Enregistre la culture de la campagne 2026 pour TOUTES les parcelles.
Par défaut : campagne « Hivernage 2026 » (année 2026), culture « Maïs ».

- Crée la campagne si elle n'existe pas (et la marque active).
- Ajoute/complète l'historique cultural 2026 de chaque parcelle (ré-exécutable).
- Met aussi à jour la culture courante des parcelles.

Usage : python set_culture_2026.py
"""
from app import create_app
from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.campagne import Campagne
from app.models.historique import HistoriqueCultural

ANNEE = 2026
LIBELLE = "Hivernage 2026"
CULTURE = "Maïs"

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        camp = Campagne.query.filter_by(libelle=LIBELLE).first()
        if not camp:
            camp = Campagne(libelle=LIBELLE, annee=ANNEE, active=True)
            db.session.add(camp)
            db.session.flush()
        # historique existant pour cette campagne
        existants = {
            h.parcelle_id: h
            for h in HistoriqueCultural.query.filter_by(campagne_id=camp.id).all()
        }
        n_new, n_maj = 0, 0
        for (pid,) in db.session.query(Parcelle.id).all():
            h = existants.get(pid)
            if h:
                h.culture = CULTURE
                h.annee = ANNEE
                h.source = "Saisie manuelle"
                n_maj += 1
            else:
                db.session.add(HistoriqueCultural(
                    parcelle_id=pid, campagne_id=camp.id, annee=ANNEE,
                    culture=CULTURE, source="Saisie manuelle"))
                n_new += 1
        # culture courante
        n_cur = Parcelle.query.update({Parcelle.culture: CULTURE},
                                      synchronize_session=False)
        db.session.commit()
        print(f"Campagne « {LIBELLE} » : {n_new} historique(s) créé(s), "
              f"{n_maj} mis à jour ; culture courante fixée sur {n_cur} parcelle(s) "
              f"= « {CULTURE} ».")
