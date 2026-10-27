"""
Met la base à jour pour correspondre à la version complète de la plateforme.
Idempotent : peut être relancé sans risque (n'ajoute que ce qui manque).

Usage : python mettre_a_jour_bdd.py
"""
from sqlalchemy import text
from app import create_app
from app.extensions import db

# Enregistre TOUS les modèles (pour create_all)
import app.models  # noqa

from app.models.role import Role
from app.models.programme import Programme
from app.models.parcelle import Parcelle

# --- Colonnes ajoutées au fil des versions ---
ALTERS = [
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS departement varchar(80)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS commune varchar(80)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS village varchar(120)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS animateur varchar(120)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS programme_id integer REFERENCES programmes(id)",
    "ALTER TABLE users      ADD COLUMN IF NOT EXISTS organisation_id integer",
    "ALTER TABLE users      ADD COLUMN IF NOT EXISTS perim_departements text",
    "ALTER TABLE users      ADD COLUMN IF NOT EXISTS perim_communes text",
    "ALTER TABLE users      ADD COLUMN IF NOT EXISTS perim_organisations text",
    "ALTER TABLE producteurs ADD COLUMN IF NOT EXISTS langue varchar(5)",
    "ALTER TABLE parcelles ADD COLUMN IF NOT EXISTS rendement_potentiel double precision",
    "ALTER TABLE rendements_reference ADD COLUMN IF NOT EXISTS source varchar(40)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS sol_type varchar(60)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS sol_ph double precision",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS sol_mo double precision",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS sol_fertilite varchar(20)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS sol_source varchar(60)",
    "ALTER TABLE parcelles  ADD COLUMN IF NOT EXISTS sol_note text",
    # Financement -> SET NULL si l'OP est supprimée
    "ALTER TABLE financements_op DROP CONSTRAINT IF EXISTS financements_op_organisation_id_fkey",
    "ALTER TABLE financements_op ADD CONSTRAINT financements_op_organisation_id_fkey "
    "FOREIGN KEY (organisation_id) REFERENCES organisations_producteurs(id) ON DELETE SET NULL",
]

# --- Rôles nécessaires (code, libellé, niveau) ---
ROLES = [
    ("CDCAR", "Chef de CAR", 2),
    ("CAR", "Conseiller Agricole Rural", 2),
    ("OP", "Organisation de Producteurs", 1),
    ("ANIMATEUR", "Animateur", 2),
]

PROGRAMME_INITIAL = "Grenier du maïs"

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        # 1) Crée toutes les tables manquantes (programmes, suivis_terrain, etc.)
        db.create_all()
        print("Tables vérifiées / créées.")

        # 2) Ajoute les colonnes manquantes
        for sql in ALTERS:
            db.session.execute(text(sql))
        db.session.commit()
        print(f"{len(ALTERS)} colonnes vérifiées / ajoutées.")

        # 3) Rôles manquants
        n_roles = 0
        for code, libelle, niveau in ROLES:
            if not Role.query.filter_by(code=code).first():
                db.session.add(Role(code=code, libelle=libelle, niveau=niveau))
                n_roles += 1
        db.session.commit()
        print(f"{n_roles} rôle(s) ajouté(s).")

        # 4) Programme initial + rattachement des parcelles orphelines
        prog = Programme.query.filter_by(nom=PROGRAMME_INITIAL).first()
        if not prog:
            prog = Programme(nom=PROGRAMME_INITIAL, code="GM", actif=True)
            db.session.add(prog); db.session.flush()
        n_p = (Parcelle.query.filter(Parcelle.programme_id.is_(None))
               .update({Parcelle.programme_id: prog.id}, synchronize_session=False))
        db.session.commit()
        print(f"Programme « {PROGRAMME_INITIAL} » : {n_p} parcelle(s) rattachée(s).")

        print("\nBase de données à jour.")
