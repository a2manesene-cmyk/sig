"""
Données d'enquête — création des tables et correction des superficies.

Usage : python init_enquete.py
  - crée les tables pratiques_campagne, parcelles_attributs, parcelles_sources ;
  - recalcule la superficie de chaque parcelle à partir de son polygone (ha), car les anciens
    imports reprenaient la superficie déclarée du fichier (arrondie, parfois en m²).
Ensuite, réimportez les fichiers GeoJSON depuis Parcelles -> Importer, mode « Mise à jour ».
"""
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models.enquete import PratiqueCampagne, AttributParcelle, SourceParcelle  # noqa: F401

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print("Tables pratiques_campagne, parcelles_attributs et parcelles_sources prêtes.")
        n = db.session.execute(text("""
            UPDATE parcelles
               SET surface_ha = round((ST_Area(geom::geography) / 10000.0)::numeric, 4)
             WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom)
               AND (surface_ha IS NULL
                    OR abs(surface_ha - ST_Area(geom::geography) / 10000.0)
                       > 0.05 * (ST_Area(geom::geography) / 10000.0))
        """)).rowcount
        db.session.commit()
        print(f"Superficies recalculées depuis les polygones : {n} parcelle(s) corrigée(s).")

        # Filière déduite de la culture pour les parcelles qui n'en ont pas
        from app.models.parcelle import Parcelle
        from app.parcelles.enquete import filiere_pour
        cache, n_fil = {}, 0
        for p in db.session.query(Parcelle).filter(Parcelle.filiere_id.is_(None), Parcelle.culture.isnot(None)):
            f = filiere_pour(p.culture, cache)
            if f is not None:
                p.filiere_id = f.id
                n_fil += 1
        db.session.commit()
        print(f"Filière renseignée d'après la culture : {n_fil} parcelle(s).")

        # Doublons laissés par un import précédent : parcelle sans producteur ni identifiant source,
        # recouverte à plus de 95 % par une autre parcelle qui, elle, a un producteur.
        doublons = [pid for (pid,) in db.session.execute(text("""
            SELECT o.id FROM parcelles o
             WHERE o.producteur_id IS NULL AND o.geom IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM parcelles_sources s WHERE s.parcelle_id = o.id)
               AND EXISTS (SELECT 1 FROM parcelles q
                            WHERE q.id <> o.id AND q.producteur_id IS NOT NULL
                              AND ST_Intersects(o.geom, q.geom)
                              AND ST_Area(ST_Intersection(o.geom, q.geom))
                                  >= 0.95 * LEAST(ST_Area(o.geom), ST_Area(q.geom)))
        """))]
        if doublons:
            import sys
            from app.models.parcelle import Parcelle
            if "--supprimer-doublons" in sys.argv:
                n_sup = 0
                for pid in doublons:
                    try:
                        with db.session.begin_nested():
                            db.session.delete(db.session.get(Parcelle, pid))
                        n_sup += 1
                    except Exception as exc:
                        print(f"  parcelle {pid} non supprimée : {exc.__class__.__name__}")
                db.session.commit()
                print(f"{n_sup} parcelle(s) en double supprimée(s).")
            else:
                print(f"{len(doublons)} parcelle(s) en double sans producteur détectée(s) "
                      f"(laissées par un import précédent). Après réimport du GeoJSON, supprimez-les avec :\n"
                      f"  python init_enquete.py --supprimer-doublons")
