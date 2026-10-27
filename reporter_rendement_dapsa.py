# -*- coding: utf-8 -*-
"""
Reporte les rendements maïs DAPSA (par département) sur chaque parcelle,
comme VALEUR DE RÉFÉRENCE (année 2024). Idempotent (upsert par parcelle+année).
Source = "DAPSA (département)". Usage : python reporter_rendement_dapsa.py
"""
import unicodedata
from app import create_app
from app.extensions import db
from app.models.parcelle import Parcelle
from app.models.rendement import RendementReference

ANNEE = 2024

# Rendement maïs (kg/ha) par département — DAPSA (meilleure année dispo : 2024, sinon 2022/2021)
DAPSA = {
    "RUFISQUE": 1250, "BAMBEY": 600, "DIOURBEL": 697, "MBACKE": 800, "FATICK": 912,
    "FOUNDIOUGNE": 3750, "GOSSAS": 1023, "BIRKILANE": 2002, "KAFFRINE": 3450,
    "KOUNGHEUL": 3541, "MALEM HODDAR": 3012, "GUINGUINEO": 2015, "KAOLACK": 2350,
    "NIORO DU RIP": 3600, "KEDOUGOU": 3868, "SALEMATA": 3301, "SARAYA": 3245,
    "KOLDA": 3588, "MEDINA YORO FOULA": 3023, "VELINGARA": 3272, "LINGUERE": 645,
    "KANEL": 1087, "MATAM": 904, "RANEROU": 812, "DAGANA": 1500, "PODOR": 3514,
    "SAINT-LOUIS": 1716, "BOUNKILING": 3549, "GOUDOMP": 2765, "SEDHIOU": 3486,
    "BAKEL": 2400, "GOUDIRY": 2324, "KOUPENTOUM": 2600, "TAMBACOUNDA": 2500,
    "MBOUR": 1234, "THIES": 670, "TIVAOUANE": 320, "BIGNONA": 3050,
    "OUSSOUYE": 1512, "ZIGUINCHOR": 1507,
}


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


app = create_app()
if __name__ == "__main__":
    with app.app_context():
        existants = {(r.parcelle_id, r.annee): r
                     for r in RendementReference.query.filter_by(annee=ANNEE).all()}
        n_ok, n_maj, n_sans = 0, 0, 0
        manquants = set()
        for p in Parcelle.query.all():
            dep = _norm(p.departement)
            rdt = DAPSA.get(dep)
            if not rdt:
                if p.departement:
                    manquants.add(p.departement)
                n_sans += 1
                continue
            ref = existants.get((p.id, ANNEE))
            if ref:
                ref.rendement_kg_ha = rdt
                ref.culture = p.culture or "Maïs"
                ref.source = "DAPSA (département)"
                n_maj += 1
            else:
                db.session.add(RendementReference(
                    parcelle_id=p.id, annee=ANNEE, culture=(p.culture or "Maïs"),
                    rendement_kg_ha=rdt, source="DAPSA (département)"))
                n_ok += 1
        db.session.commit()
        print(f"Rendement DAPSA reporté : {n_ok} créé(s), {n_maj} mis à jour, "
              f"{n_sans} parcelle(s) sans correspondance de département.")
        if manquants:
            print("Départements non trouvés dans la table DAPSA :", ", ".join(sorted(manquants)))
