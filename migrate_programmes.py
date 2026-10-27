"""
Crée la table 'programmes' et la colonne parcelles.programme_id,
crée le programme « Grenier du maïs » et y rattache TOUTES les parcelles
actuellement en base. Ré-exécutable.
Usage : python migrate_programmes.py
"""
from sqlalchemy import text
from app import create_app
from app.extensions import db
import app.models.programme  # noqa (enregistre le modèle)
from app.models.programme import Programme
from app.models.parcelle import Parcelle

PROGRAMME_INITIAL = "Grenier du maïs"

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # crée la table programmes si absente
        db.session.execute(text(
            "ALTER TABLE parcelles ADD COLUMN IF NOT EXISTS programme_id integer "
            "REFERENCES programmes(id)"))
        db.session.commit()

        prog = Programme.query.filter_by(nom=PROGRAMME_INITIAL).first()
        if not prog:
            prog = Programme(nom=PROGRAMME_INITIAL, code="GM", actif=True)
            db.session.add(prog); db.session.flush()

        n = (Parcelle.query.filter(Parcelle.programme_id.is_(None))
             .update({Parcelle.programme_id: prog.id}, synchronize_session=False))
        db.session.commit()
        print(f"Programme « {PROGRAMME_INITIAL} » (id={prog.id}) : "
              f"{n} parcelle(s) rattachée(s).")
