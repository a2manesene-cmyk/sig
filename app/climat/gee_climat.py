"""
Extraction des données climatiques journalières via Google Earth Engine.

- Pluie : CHIRPS Daily (UCSB-CHG/CHIRPS/DAILY), 0,05°, mm/jour.
- Températures : ERA5-Land Daily Aggregated (ECMWF/ERA5_LAND/DAILY_AGGR), 0,1°, Kelvin.

On interroge le centre de chaque cellule, mois par mois et par lots de points :
chaque requête renvoie au plus `LOT` entités (une par cellule) avec une propriété
par jour, bien sous la limite de 5 000 éléments d'Earth Engine.

Délais de mise à disposition : CHIRPS ~3 semaines, ERA5-Land ~1 semaine.
"""
from datetime import date, timedelta

from app.teledetection.gee import _ee, init_ee

LOT = 400
CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
ERA5 = "ECMWF/ERA5_LAND/DAILY_AGGR"


def _mois(d1, d2):
    """Découpe [d1, d2] en tranches mensuelles (d_debut, d_fin_exclue)."""
    d = d1
    while d <= d2:
        suivant = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield d, min(suivant, d2 + timedelta(1))
        d = suivant


def _extraire(ee, collection, bandes, cellules, d1, d2, echelle):
    """{(code, date_iso, bande): valeur} pour les cellules et la période."""
    out = {}
    ic = ee.ImageCollection(collection).select(bandes)
    for m1, m2 in _mois(d1, d2):
        sous = ic.filterDate(m1.isoformat(), m2.isoformat())
        if sous.size().getInfo() == 0:
            continue
        # Une image multibande : une bande par jour et par variable
        # (noms « AAAAMMJJ_bande » pour CHIRPS et ERA5-Land, dont l'index système est la date).
        pile = sous.toBands()
        for k in range(0, len(cellules), LOT):
            lot = cellules[k:k + LOT]
            fc = ee.FeatureCollection([
                ee.Feature(ee.Geometry.Point([c["lon"], c["lat"]]), {"code": c["code"]}) for c in lot
            ])
            res = pile.reduceRegions(collection=fc, reducer=ee.Reducer.first(), scale=echelle)
            for f in res.getInfo()["features"]:
                pr = f["properties"]
                code = pr.pop("code", None)
                for nom, v in pr.items():
                    if v is None or "_" not in nom:
                        continue
                    jour, bande = nom.split("_", 1)
                    if len(jour) != 8 or not jour.isdigit():
                        continue
                    out[(code, f"{jour[:4]}-{jour[4:6]}-{jour[6:]}", bande)] = v
    return out


def extraire_climat(cellules, d1, d2, project):
    """Pluie + Tmin/Tmax journalières pour une liste de cellules {code, lon, lat}.

    Renvoie {code: {date_iso: {"pluie": mm, "tmin": °C, "tmax": °C}}}.
    """
    ee = _ee()
    init_ee(project)
    pluie = _extraire(ee, CHIRPS, ["precipitation"], cellules, d1, d2, 5566)
    temp = _extraire(ee, ERA5, ["temperature_2m_min", "temperature_2m_max"], cellules, d1, d2, 11132)

    res = {}
    for (code, jour, _b), v in pluie.items():
        res.setdefault(code, {}).setdefault(jour, {})["pluie"] = round(max(v, 0.0), 2)
    for (code, jour, b), v in temp.items():
        cle = "tmin" if b.endswith("min") else "tmax"
        res.setdefault(code, {}).setdefault(jour, {})[cle] = round(v - 273.15, 2)
    return res
