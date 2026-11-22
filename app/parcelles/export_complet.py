"""
Export GeoJSON complet des parcelles, au format du modèle ANCAR (app/parcelles/modele.py).

Le fichier contient tout ce qui décrit une parcelle : localisation, producteur, OP, relais,
historique cultural (une colonne culture_AAAA par année), pratiques et rendement de la campagne,
attributs d'enquête, sol, rendement potentiel et, en option, les indices de végétation à chaque
passage du satellite. Réimporté (mode « Mise à jour »), il reconstitue les mêmes parcelles sur une
autre plateforme : c'est une sauvegarde lisible par QGIS et par la plateforme.
"""
import json
from collections import defaultdict
from datetime import date

from sqlalchemy import text

from app.extensions import db


def _num(v, d=None):
    if v is None:
        return None
    return round(float(v), d) if d is not None else float(v)


def _oui_non(v):
    return None if v is None else ("Oui" if v else "Non")


def geojson_complet(pids=None, avec_indices=True, programme_id=None):
    params = {}
    filtre = "WHERE p.geom IS NOT NULL AND NOT ST_IsEmpty(p.geom)"
    if pids is not None:
        filtre += " AND p.id = ANY(:ids)"
        params["ids"] = list(pids)
    if programme_id:
        filtre += " AND p.programme_id = :prog"
        params["prog"] = programme_id
    rows = db.session.execute(text(f"""
        SELECT p.id, p.code, ST_AsGeoJSON(p.geom, 7), p.surface_ha, p.culture, p.observations,
               p.departement, p.commune, p.village, p.animateur, p.rendement_potentiel,
               p.sol_type, p.sol_ph, p.sol_mo, p.sol_fertilite, p.sol_source, p.sol_note,
               r.nom, pr.nom, f.nom, c.annee, o.nom,
               TRIM(COALESCE(pd.prenom, '') || ' ' || COALESCE(pd.nom, '')), pd.telephone, pd.sexe
          FROM parcelles p
          LEFT JOIN regions r ON r.id = p.region_id
          LEFT JOIN programmes pr ON pr.id = p.programme_id
          LEFT JOIN filieres f ON f.id = p.filiere_id
          LEFT JOIN campagnes c ON c.id = p.campagne_id
          LEFT JOIN producteurs pd ON pd.id = p.producteur_id
          LEFT JOIN organisations_producteurs o ON o.id = pd.organisation_id
          {filtre}
          ORDER BY p.id"""), params).all()
    ids = [r[0] for r in rows]

    def par_parcelle(sql):
        d = defaultdict(list)
        try:
            with db.session.begin_nested():
                for r in db.session.execute(text(sql), {"ids": ids}):
                    d[r[0]].append(r[1:])
        except Exception:                       # table absente (module non installé)
            pass
        return d

    sources = par_parcelle("SELECT parcelle_id, source_id FROM parcelles_sources WHERE parcelle_id = ANY(:ids) ORDER BY id")
    histo = par_parcelle("SELECT parcelle_id, annee, culture FROM historique_cultural WHERE parcelle_id = ANY(:ids) ORDER BY annee")
    attrs = par_parcelle("SELECT parcelle_id, cle, valeur FROM parcelles_attributs WHERE parcelle_id = ANY(:ids) ORDER BY id")
    prats = par_parcelle("""SELECT parcelle_id, annee, culture, precedent, variete, date_semis, labour, matiere_organique,
                                   uree_sacs, npk_sacs, rendement_t_ha, methode_rendement, date_recolte, observations
                              FROM pratiques_campagne WHERE parcelle_id = ANY(:ids) ORDER BY annee""")
    indices = par_parcelle("""SELECT parcelle_id, date, ndvi, ndwi, ndmi, savi, evi FROM indices_vegetation
                               WHERE parcelle_id = ANY(:ids) AND source IS DISTINCT FROM 'GEE Sentinel-2 (composite)'
                               ORDER BY date""") if avec_indices else {}

    annee_active = date.today().year
    try:
        with db.session.begin_nested():
            a = db.session.execute(text("SELECT annee FROM campagnes WHERE active ORDER BY annee DESC LIMIT 1")).scalar()
            annee_active = a or annee_active
    except Exception:
        pass

    feats = []
    for (pid, code, gj, surf, culture, obs, dep, com, vil, anim, pot, s_type, s_ph, s_mo, s_fert, s_src, s_note,
         reg, prog, fil, camp, op, prod, tel, sexe) in rows:
        src = next((s[0] for s in sources.get(pid, []) if s[0].startswith("id#")), None)
        id_parcelle = src[3:] if src else (code or f"P{pid}")
        at = {k: v for k, v in attrs.get(pid, [])}
        pr = {}
        for ligne in prats.get(pid, []):
            if ligne[0] == (camp or annee_active):
                pr = dict(zip(["annee", "culture", "precedent", "variete", "date_semis", "labour", "matiere_organique",
                               "uree_sacs", "npk_sacs", "rendement_t_ha", "methode_rendement", "date_recolte",
                               "observations"], ligne))
        props = {
            "id_parcelle": id_parcelle, "programme": prog, "campagne": camp or annee_active,
            "region": reg, "departement": dep, "commune": com, "village": vil,
            "op_cooperative": op, "producteur": prod or None, "telephone_producteur": tel,
            "sexe_producteur": {"F": "Femme", "H": "Homme", "M": "Homme"}.get((sexe or "").upper()),
            "relais": anim, "telephone_relais": at.pop("Téléphone du relais", None),
            "date_enregistrement": at.pop("Date d'enregistrement", None),
            "superficie_ha": _num(surf, 4), "superficie_declaree_ha": _num_ou_texte(at.pop("Superficie déclarée", None)),
            "statut_foncier": at.pop("Statut juridique de la parcelle", None),
            "mode_acquisition": at.pop("Mode d'acquisition de la parcelle", None),
            "culture": culture, "filiere": fil, "variete": pr.get("variete"),
        }
        for annee, cult in histo.get(pid, []):
            if annee != props["campagne"]:
                props[f"culture_{annee}"] = cult
        props.update({
            "precedent_cultural": pr.get("precedent"),
            "date_semis": pr["date_semis"].isoformat() if pr.get("date_semis") else None,
            "labour": _oui_non(pr.get("labour")), "matiere_organique": _oui_non(pr.get("matiere_organique")),
            "npk_sacs": pr.get("npk_sacs"), "uree_sacs": pr.get("uree_sacs"),
            "methode_rendement": pr.get("methode_rendement"), "rendement_t_ha": pr.get("rendement_t_ha"),
            "date_recolte": pr["date_recolte"].isoformat() if pr.get("date_recolte") else None,
            "observations": obs or pr.get("observations"),
            "rendement_potentiel_kg_ha": _num(pot, 1),
            "sol_type": s_type, "sol_ph": s_ph, "sol_mo": s_mo, "sol_fertilite": s_fert, "sol_source": s_src,
            "sol_note": s_note,
        })
        props.update(at)                         # autres informations d'enquête, telles quelles
        if avec_indices:
            props["indices"] = [{"date": d.isoformat(), "ndvi": _num(a, 4), "ndwi": _num(b, 4), "ndmi": _num(c, 4),
                                 "savi": _num(e, 4), "evi": _num(f_, 4)} for d, a, b, c, e, f_ in indices.get(pid, [])]
        feats.append({"type": "Feature", "properties": props, "geometry": json.loads(gj)})
    return {"type": "FeatureCollection", "name": "parcelles_ANCAR_complet",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": feats}


def _num_ou_texte(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return v
