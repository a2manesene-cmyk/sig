"""
Applique, pour les parcelles du programme « Grenier du maïs » :
  - filière = Maïs
  - culture courante = Maïs
  - campagne « Hivernage 2026 » (créée + activée) avec historique culture = Maïs
Idempotent. Usage : python appliquer_mais_2026.py
"""
from app import create_app
from app.extensions import db
import app.models  # noqa (enregistre les modèles)
from app.models.parcelle import Parcelle
from app.models.filiere import Filiere
from app.models.campagne import Campagne
from app.models.programme import Programme
from app.models.historique import HistoriqueCultural

PROGRAMME = "Grenier du maïs"
ANNEE, LIBELLE, CULTURE = 2026, "Hivernage 2026", "Maïs"

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        # Filière Maïs
        fil = Filiere.query.filter(db.func.lower(Filiere.nom) == "maïs").first() \
            or Filiere.query.filter(db.func.lower(Filiere.nom) == "mais").first()
        if not fil:
            fil = Filiere(nom="Maïs"); db.session.add(fil); db.session.flush()

        # Campagne 2026 active (désactive les autres)
        Campagne.query.update({Campagne.active: False})
        camp = Campagne.query.filter_by(annee=ANNEE).first()
        if not camp:
            camp = Campagne(libelle=LIBELLE, annee=ANNEE); db.session.add(camp); db.session.flush()
        camp.active = True

        # Cible : parcelles du programme (sinon toutes si programme absent)
        prog = Programme.query.filter(db.func.lower(Programme.nom) == PROGRAMME.lower()).first()
        q = Parcelle.query
        if prog:
            q = q.filter(Parcelle.programme_id == prog.id)
        parcelles = q.all()

        n_fil = q.update({Parcelle.filiere_id: fil.id, Parcelle.culture: CULTURE},
                         synchronize_session=False)

        # Historique 2026 = Maïs (upsert par parcelle)
        existants = {h.parcelle_id for h in
                     HistoriqueCultural.query.filter_by(campagne_id=camp.id).all()}
        n_hist = 0
        for p in parcelles:
            if p.id in existants:
                (HistoriqueCultural.query
                 .filter_by(parcelle_id=p.id, campagne_id=camp.id)
                 .update({HistoriqueCultural.culture: CULTURE, HistoriqueCultural.annee: ANNEE},
                         synchronize_session=False))
            else:
                db.session.add(HistoriqueCultural(parcelle_id=p.id, campagne_id=camp.id,
                                                  annee=ANNEE, culture=CULTURE, source="Saisie"))
                n_hist += 1
        db.session.commit()
        print(f"Filière « {fil.nom} » + culture « {CULTURE} » appliquées à {n_fil} parcelle(s) "
              f"du programme « {PROGRAMME if prog else 'TOUTES' } ».")
        print(f"Campagne active : « {camp.libelle} » ; {n_hist} historique(s) créé(s).")
