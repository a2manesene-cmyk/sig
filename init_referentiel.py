"""
Référentiel des cultures — création des tables, valeurs de référence et potentiels des parcelles.

Usage :
    python init_referentiel.py                    -> crée / met à jour les tables, ajoute les cultures
                                                     absentes et applique les potentiels aux parcelles
    python init_referentiel.py --reinitialiser    -> réécrit aussi les cultures existantes avec les
                                                     valeurs d'origine (écrase les modifications !)

Crée aussi les tables des fiches techniques (lecture Word / PDF / OCR), de l'itinéraire technique,
des ennemis, des doses d'irrigation et du suivi des opérations sur les parcelles.
Pour lire les scans et les photos : sudo apt install tesseract-ocr tesseract-ocr-fra poppler-utils
"""
import shutil
import sys

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.referentiel import (CultureRef, VarieteRef, PotentielParcelle, FicheTechnique,  # noqa: F401
                                    ItineraireRef, RavageurRef, IrrigationRef, OperationParcelle)
from app.referentiel import service

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # colonne ajoutée après la première version du module
        for col in ("rdt_potentiel_t DOUBLE PRECISION", "nom_scientifique VARCHAR(120)",
                    "reference_j0 VARCHAR(12)",
                    "fiche_id INTEGER REFERENCES referentiel_fiches(id) ON DELETE SET NULL"):
            db.session.execute(text(f"ALTER TABLE referentiel_cultures ADD COLUMN IF NOT EXISTS {col}"))
        db.session.commit()
        dossier = app.config.get("FICHES_DIR") or app.instance_path + "/fiches_techniques"
        import os
        os.makedirs(dossier, exist_ok=True)
        print(f"Fiches techniques : fichiers stockés dans {dossier}")
        print("OCR (scans, photos) : " + ("Tesseract disponible" if shutil.which("tesseract")
              else "Tesseract ABSENT — sudo apt install tesseract-ocr tesseract-ocr-fra poppler-utils"))
        n_c, n_v = service.initialiser(reinitialiser="--reinitialiser" in sys.argv)
        print(f"Référentiel : {n_c} culture(s) et {n_v} variété(s) chargées.")
        s = service.appliquer_potentiels()
        print(f"Potentiels des parcelles : {s['modifiees']} modifié(s) — {s['variete']} par variété, "
              f"{s['speculation']} par spéculation, {s['manuel']} saisie(s) manuelle(s) conservée(s), "
              f"{s['sans_potentiel']} sans potentiel, {s['sans_referentiel']} hors référentiel.")
        if s["cultures_absentes"]:
            print("Cultures hors référentiel (à ajouter comme spéculation ou synonyme) : "
                  + ", ".join(f"{k} ({n})" for k, n in s["cultures_absentes"][:15]))
