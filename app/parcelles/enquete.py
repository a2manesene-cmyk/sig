"""
Lecture des données d'enquête d'une entité GeoJSON (QField / KoBo) :
pratiques de la campagne (variété, semis, fumure, rendement) et attributs divers.
Tolérant aux variantes de noms de colonnes rencontrées dans les fichiers ANCAR.
"""
import re
import unicodedata
from datetime import date

from app.extensions import db
from app.models.enquete import PratiqueCampagne, AttributParcelle, SourceParcelle

# Colonnes jamais conservées : identifiants techniques et pièces d'identité
EXCLUES = {"fid", "id", "fid_1", "layer", "path", "sup", "numero cni", "numero_cni", "cni", "id_parcelle",
           "programme", "campagne", "indices"}

# Colonnes déjà reprises ailleurs (parcelle, producteur, pratiques)
UTILISEES = {
    "region", "departement", "commune", "village",
    "prenom et nom producteur", "prenom_nom_producteur", "nom & prenom du producteur",
    "telephone du producteur", "telephone_producteur", "telephone producteur",
    "sexe", "sexe_ producteur", "nom_cooperative", "nom cooperative",
    "prenom_nom_relais", "nom enqueteur",
    "variete", "preciser autre_variete", "date_de_semis", "date de semis", "labour",
    "application matiere_organique", "nbre_sac_uree_applique", "nbre_sac_npk applique",
    "rendement t/ha", "methode estimation rendement", "date de recolte", "rendement kg/carre",
    "surface carre", "precedent cultural", "culture actuelle", "observations generales", "observations",
    # noms du modèle GeoJSON ANCAR (app/parcelles/modele.py)
    "op_cooperative", "producteur", "sexe_producteur", "relais", "culture", "date_semis", "matiere_organique",
    "npk_sacs", "uree_sacs", "methode_rendement", "poids_carre_kg", "surface_carre_m2", "rendement_t_ha",
    "date_recolte", "precedent_cultural", "filiere", "superficie_ha", "rendement_potentiel_kg_ha",
    "sol_type", "sol_ph", "sol_mo", "sol_fertilite", "sol_source", "sol_note",
}

# Libellés harmonisés des attributs (les variantes d'un même champ sont regroupées)
LIBELLES = {
    "mode d'aquisition_parcelle": "Mode d'acquisition de la parcelle",
    "mode d' acquisition parcelle": "Mode d'acquisition de la parcelle",
    "mode d'acquisition parcelle": "Mode d'acquisition de la parcelle",
    "statut_juridique_parcelle": "Statut juridique de la parcelle",
    "date d'enregistrement": "Date d'enregistrement",
    "date enregistrement": "Date d'enregistrement",
    "date enregistement": "Date d'enregistrement",
    "date de suivi": "Date de suivi",
    "date de collecte": "Date de collecte",
    "telephone_relais": "Téléphone du relais",
    "telephone enqueteur": "Téléphone de l'enquêteur",
    "superficie_declaree": "Superficie déclarée",
    "superficie_declaree_ha": "Superficie déclarée",
    "statut_foncier": "Statut juridique de la parcelle",
    "mode_acquisition": "Mode d'acquisition de la parcelle",
    "date_enregistrement": "Date d'enregistrement",
    "superficie": "Superficie déclarée",
    "superfic_1": "Superficie déclarée",
    "superfic_2": "Superficie déclarée",
    "surface (ha)": "Superficie déclarée",
    "superficie_derniere_culture": "Superficie de la dernière culture",
    "superficie_culture_actuelle": "Superficie de la culture actuelle",
    "periode de la derniere culture": "Période de la dernière culture",
    "mode d'exhaure utilise": "Mode d'exhaure",
    "nombre motopompes_diesel": "Motopompes diesel",
    "nbre motopompe fonctionnel": "Motopompes fonctionnelles",
    "nbre kit solaire installe": "Kits solaires installés",
    "nbre kits solaire fonctionnel": "Kits solaires fonctionnels",
    "nombre de puits": "Puits",
    "nombre de mini forage": "Mini-forages",
    "nbre de pieds par carre": "Pieds par carré de rendement",
}

# Plages plausibles de rendement (t/ha) par culture
PLAGES_RDT = {"mais": (0.3, 12), "mil": (0.1, 5), "sorgho": (0.1, 6), "arachide": (0.1, 5),
              "niebe": (0.1, 4), "riz": (0.5, 12)}
PLAGE_RDT_DEFAUT = (0.1, 80)      # maraîchage


def norm(s):
    """minuscules, sans accents, espaces réduits (pour comparer les noms de colonnes)."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.strip().lower())


def _props_norm(props):
    return {norm(k): v for k, v in props.items()}


def _val(pn, *cles):
    for c in cles:
        v = pn.get(norm(c))
        if v not in (None, "") and str(v).strip().lower() not in ("none", "null", "nd", "n/a"):
            return v
    return None


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").strip())
    except ValueError:
        return None


def _date(v):
    if not v:
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def _oui_non(v):
    if v is None:
        return None
    s = norm(v)
    if s in ("oui", "o", "yes", "1", "true"):
        return True
    if s in ("non", "n", "no", "0", "false"):
        return False
    return None


def variete(pn):
    vides = ("non", "autre", "aucune")
    v = _val(pn, "Variete", "Variété")
    precise = _val(pn, "Preciser autre_variete")
    if v is None or norm(v) == "autre":
        if precise is not None and norm(precise) not in vides:
            v = precise
        elif v is not None:
            return "Autre"
    if v is None:
        return None
    s = norm(v).replace(" ", "")
    if s.startswith("mast"):
        return "Mastrop"
    if s.startswith("lg336"):
        return "LG 336"
    return str(v).strip()[:80]


def filiere_nommee(nom, cache=None):
    """Filière indiquée dans le fichier (champ « filiere ») : existante, sinon créée."""
    from app.models.filiere import Filiere
    if not nom or not str(nom).strip():
        return None
    cache = cache if cache is not None else {}
    if "_filieres" not in cache:
        cache["_filieres"] = {norm(f.nom): f for f in db.session.query(Filiere).all()}
    k = norm(nom)
    f = cache["_filieres"].get(k)
    if f is None:
        f = Filiere(nom=str(nom).strip()[:80])
        db.session.add(f)
        db.session.flush()
        cache["_filieres"][k] = f
    return f


def filiere_pour(culture, cache=None):
    """Filière d'une culture : même nom, nom du référentiel des cultures, premier mot
    (« Mil hybride » → Mil), ou « Horticulture » pour une culture maraîchère. None si aucune."""
    from app.models.filiere import Filiere
    if not culture:
        return None
    cache = cache if cache is not None else {}
    if "_filieres" not in cache:
        cache["_filieres"] = {norm(f.nom): f for f in db.session.query(Filiere).all()}
    fil = cache["_filieres"]
    c = norm(culture)
    if c in cache:
        return cache[c]
    res = fil.get(c)
    ref = None
    if res is None:
        try:
            from app.referentiel.service import culture_ref
            with db.session.begin_nested():
                ref = culture_ref(culture)
        except Exception:
            ref = None
        if ref is not None:
            res = fil.get(norm(ref.nom))
    if res is None and c.split():
        res = fil.get(c.split()[0])
    if res is None:
        maraichage = (ref is not None and (ref.systeme == "Irrigué" or "legume" in norm(ref.famille or "")))
        if maraichage:
            res = fil.get("horticulture") or fil.get("maraichage")
    cache[c] = res
    return res


def plage_rendement(culture):
    """Plage plausible : 10 % du minimum à 2 fois le maximum du référentiel des cultures
    (module Référentiel), sinon les plages par défaut ci-dessus."""
    try:
        from app.referentiel.service import culture_ref
        with db.session.begin_nested():
            ref = culture_ref(culture)
        if ref is not None and ref.rdt_min and ref.rdt_max:
            return (round(ref.rdt_min * 0.1, 2), ref.rdt_max * 2)
    except Exception:
        pass
    c = norm(culture or "")
    for cle, plage in PLAGES_RDT.items():
        if cle in c:
            return plage
    return PLAGE_RDT_DEFAUT


def precedent_de(props, annee):
    """Culture de l'année précédant la campagne (colonne « culture … AAAA »)."""
    if not annee:
        return None
    for k, v in props.items():
        n = norm(k)
        if "culture" in n and str(annee - 1) in n and v not in (None, "") \
                and str(v).strip().lower() not in ("none", "null"):
            return str(v).strip()
    return None


def extraire_pratique(props, culture, annee=None):
    pn = _props_norm(props)
    rdt = _num(_val(pn, "rendement_t_ha", "Rendement T/ha"))
    if rdt is None:
        kg_carre = _num(_val(pn, "poids_carre_kg", "Rendement Kg/carre"))
        s_carre = _num(_val(pn, "surface_carre_m2", "Surface carre"))
        if kg_carre is not None and s_carre:
            rdt = round(kg_carre / s_carre * 10, 3)     # kg/m² -> t/ha
    lo, hi = plage_rendement(culture)
    d_semis = _date(_val(pn, "date_semis", "Date_de_semis", "Date de semis"))
    pr = {
        "culture": culture,
        "precedent": _val(pn, "precedent_cultural", "Precedent cultural") or precedent_de(props, annee),
        "variete": variete(pn),
        "date_semis": d_semis,
        "labour": _oui_non(_val(pn, "Labour")),
        "matiere_organique": _oui_non(_val(pn, "matiere_organique", "Application matiere_organique")),
        "uree_sacs": _num(_val(pn, "uree_sacs", "Nbre_sac_uree_applique")),
        "npk_sacs": _num(_val(pn, "npk_sacs", "Nbre_sac_NPK applique")),
        "rendement_t_ha": rdt,
        "rendement_valide": (lo <= rdt <= hi) if rdt is not None else None,
        "methode_rendement": _val(pn, "methode_rendement", "Methode estimation rendement"),
        "date_recolte": _date(_val(pn, "date_recolte", "Date de recolte")),
        "observations": _val(pn, "Observations generales", "Observations"),
    }
    utile = any(pr[k] is not None for k in pr if k not in ("culture", "observations"))
    return pr if utile else None


def identifiant_source(props):
    """Identifie l'entité de façon stable d'un export à l'autre : « id_parcelle » du modèle ANCAR,
    sinon couche d'origine + fid (exports QGIS fusionnés)."""
    pn = _props_norm(props)
    idp = pn.get("id_parcelle")
    if idp not in (None, "") and str(idp).strip():
        return f"id#{str(idp).strip()}"[:255]
    layer, fid = props.get("layer"), props.get("fid")
    if layer in (None, "") or fid in (None, ""):
        return None
    return f"{str(layer).strip()}#{fid}"[:255]


def annee_campagne(props, annee_defaut):
    """Année de la campagne : champ « campagne » du modèle s'il est valide, sinon la campagne
    active (les dates saisies sur le terrain contiennent des erreurs d'année : semis en 2027…)."""
    v = _props_norm(props).get("campagne")
    try:
        a = int(float(str(v).strip()[:4])) if v not in (None, "") else None
    except ValueError:
        a = None
    return a if a and 2000 <= a <= date.today().year + 1 else annee_defaut


def _date_coherente(d, annee):
    """Date retenue seulement si elle tombe dans l'année de campagne et n'est pas dans le futur."""
    return d if d and d.year == annee and d <= date.today() else None


def mesure_au_champ(methode):
    m = norm(methode or "")
    return "carre" in m or "comptage" in m


def enregistrer(p, props, annee, culture):
    """Crée / met à jour la pratique de la campagne et les attributs d'enquête de la parcelle.
    Renvoie (pratique_enregistree, rendement_mesure_valide)."""
    from app.models.rendement import RendementReference

    rdt_ok = False
    pr = extraire_pratique(props, culture, annee)
    if pr:
        incoherentes = []
        for k, lib in (("date_semis", "semis"), ("date_recolte", "récolte")):
            if pr[k] and not _date_coherente(pr[k], annee):
                incoherentes.append(f"date de {lib} saisie {pr[k].strftime('%d/%m/%Y')} incohérente")
                pr[k] = None
        if incoherentes:
            pr["observations"] = "; ".join(filter(None, [pr["observations"], *incoherentes]))
        obj = (db.session.query(PratiqueCampagne).filter_by(parcelle_id=p.id, annee=annee).first()
               or PratiqueCampagne(parcelle_id=p.id, annee=annee))
        for k, v in pr.items():
            if v is not None or getattr(obj, k) is None:
                setattr(obj, k, v.strip()[:80] if isinstance(v, str) and k != "observations" else v)
        db.session.add(obj)

        # Rendement mesuré au champ et plausible -> référence pour calibrer le modèle de rendement
        if pr["rendement_valide"] and pr["rendement_t_ha"] > 0 and mesure_au_champ(pr["methode_rendement"]):
            src = "Carré de rendement (enquête)"
            ref = (db.session.query(RendementReference)
                   .filter_by(parcelle_id=p.id, annee=annee, source=src).first()
                   or RendementReference(parcelle_id=p.id, annee=annee, source=src))
            ref.culture = culture
            ref.rendement_kg_ha = round(pr["rendement_t_ha"] * 1000)
            db.session.add(ref)
            rdt_ok = True

    src = identifiant_source(props)
    if src and not db.session.query(SourceParcelle.id).filter_by(source_id=src).first():
        db.session.add(SourceParcelle(parcelle_id=p.id, source_id=src))

    existants = {a.cle: a for a in db.session.query(AttributParcelle).filter_by(parcelle_id=p.id)}
    for cle, v in props.items():
        n = norm(cle)
        if n in EXCLUES or n in UTILISEES or v in (None, ""):
            continue
        if "culture" in n and re.search(r"20\d{2}", n):
            continue                  # historique cultural, déjà importé à part
        valeur = str(v).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}T", valeur):
            valeur = valeur[:10]
        if isinstance(v, float) and v.is_integer():
            valeur = str(int(v))
        lib = LIBELLES.get(n, str(cle).replace("_", " ").strip())[:80]
        a = existants.get(lib)
        if a is None:
            a = AttributParcelle(parcelle_id=p.id, cle=lib)
            db.session.add(a)
            existants[lib] = a
        a.valeur = valeur[:255]
    return pr is not None, rdt_ok
