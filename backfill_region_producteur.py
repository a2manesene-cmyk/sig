# -*- coding: utf-8 -*-
"""
Renseigne la RÉGION de chaque producteur à partir du DÉPARTEMENT de ses
parcelles (correspondance département -> région du Sénégal). La région est
créée si elle n'existe pas. Idempotent.
Usage : python backfill_region_producteur.py
"""
from collections import Counter
from app import create_app
from app.extensions import db
from app.models.zone import Region
from app.models.producteur import Producteur
from app.models.parcelle import Parcelle
from app.rendement.calcul import cle_lieu

# Département -> Région (Sénégal)
DEP_REGION = {
    "DAKAR": "Dakar", "GUEDIAWAYE": "Dakar", "PIKINE": "Dakar", "RUFISQUE": "Dakar", "KEUR MASSAR": "Dakar",
    "DIOURBEL": "Diourbel", "BAMBEY": "Diourbel", "MBACKE": "Diourbel",
    "FATICK": "Fatick", "FOUNDIOUGNE": "Fatick", "GOSSAS": "Fatick",
    "KAFFRINE": "Kaffrine", "BIRKILANE": "Kaffrine", "KOUNGHEUL": "Kaffrine", "MALEM HODDAR": "Kaffrine",
    "KAOLACK": "Kaolack", "GUINGUINEO": "Kaolack", "NIORO DU RIP": "Kaolack",
    "KEDOUGOU": "Kédougou", "SALEMATA": "Kédougou", "SARAYA": "Kédougou",
    "KOLDA": "Kolda", "MEDINA YORO FOULA": "Kolda", "MEDINA YORO FOULAH": "Kolda", "VELINGARA": "Kolda",
    "LOUGA": "Louga", "KEBEMER": "Louga", "LINGUERE": "Louga",
    "MATAM": "Matam", "KANEL": "Matam", "RANEROU": "Matam", "RANEROU FERLO": "Matam",
    "SAINT LOUIS": "Saint-Louis", "DAGANA": "Saint-Louis", "PODOR": "Saint-Louis",
    "SEDHIOU": "Sédhiou", "BOUNKILING": "Sédhiou", "GOUDOMP": "Sédhiou",
    "TAMBACOUNDA": "Tambacounda", "BAKEL": "Tambacounda", "GOUDIRY": "Tambacounda", "KOUMPENTOUM": "Tambacounda",
    "THIES": "Thiès", "MBOUR": "Thiès", "TIVAOUANE": "Thiès",
    "ZIGUINCHOR": "Ziguinchor", "BIGNONA": "Ziguinchor", "OUSSOUYE": "Ziguinchor",
}

app = create_app()
if __name__ == "__main__":
    with app.app_context():
        regions = {cle_lieu(r.nom): r for r in Region.query.all()}

        def get_region(nom):
            k = cle_lieu(nom)
            r = regions.get(k)
            if not r:
                r = Region(nom=nom)
                db.session.add(r)
                db.session.flush()
                regions[k] = r
            return r

        n_ok, n_sans = 0, 0
        inconnus = set()
        for prod in Producteur.query.filter(Producteur.region_id.is_(None)).all():
            deps = [p.departement for p in
                    Parcelle.query.filter_by(producteur_id=prod.id).all() if p.departement]
            if not deps:
                n_sans += 1
                continue
            dep = Counter(cle_lieu(d) for d in deps).most_common(1)[0][0]
            region_nom = DEP_REGION.get(dep)
            if not region_nom:
                inconnus.add(dep)
                n_sans += 1
                continue
            prod.region_id = get_region(region_nom).id
            n_ok += 1
        db.session.commit()
        print(f"Région renseignée pour {n_ok} producteur(s) ; {n_sans} sans correspondance.")
        if inconnus:
            print("Départements non mappés :", ", ".join(sorted(inconnus)))
