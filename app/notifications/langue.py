"""Résolution de la langue et gabarits de messages d'alerte multilingues."""
from .settings import REGION_LANGUE, LANGUE_DEFAUT

# Langues proposées (code -> libellé)
LANGUES = {"fr": "Français", "wo": "Wolof", "ff": "Pulaar"}

# Gabarits texte par langue et par type d'alerte.
# {nom} = producteur, {codes} = liste des parcelles, {n} = nombre de parcelles.
MESSAGES = {
    "fr": {
        "stress": ("Bonjour {nom}. Alerte agricole ANCAR : {n} de vos parcelles "
                   "({codes}) présentent un stress hydrique / une faible vigueur. "
                   "Veuillez contacter votre Conseiller Agricole Rural."),
    },
    "wo": {
        # Texte wolof (sert au message écrit ; l'audio wolof est pré-enregistré)
        "stress": ("Asalaa maalekum {nom}. Ci ANCAR: sa tool yi ({codes}) "
                   "am nañu jafe-jafe ci ndox. Jokkoo ak sa conseiller agricole."),
    },
    "ff": {
        "stress": ("Jaaraama {nom}. ANCAR: ngesa maa ({codes}) ina heli caɗeele "
                   "ndiyam. Jokkondir e conseiller agricole maa."),
    },
}


def langue_pour(region_nom):
    if not region_nom:
        return LANGUE_DEFAUT
    return REGION_LANGUE.get(region_nom.strip().upper(), LANGUE_DEFAUT)


def langue_producteur(prod, region_nom=None):
    """Langue préférée du producteur, sinon langue de la zone, sinon défaut."""
    code = getattr(prod, "langue", None)
    if code in LANGUES:
        return code
    if region_nom is None and getattr(prod, "region", None):
        region_nom = prod.region.nom
    return langue_pour(region_nom)


def message(lang, type_alerte, nom, codes):
    lang = lang if lang in MESSAGES else LANGUE_DEFAUT
    gab = MESSAGES[lang].get(type_alerte) or MESSAGES["fr"][type_alerte]
    liste = ", ".join(codes[:6]) + ("…" if len(codes) > 6 else "")
    return gab.format(nom=(nom or "").split()[0] if nom else "", codes=liste, n=len(codes))
