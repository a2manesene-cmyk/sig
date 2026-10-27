"""
Traduction des indices de télédétection en langage agronomique.
Source unique de vérité pour les libellés, descriptions et seuils d'interprétation.
"""

# Libellés « métier » de chaque indice
INDICES_INFO = {
    "ndvi": {
        "label": "Vigueur de la végétation", "acronyme": "NDVI",
        "theme": "Suivi de la végétation",
        "description": "Densité et santé du couvert végétal. Plus c'est élevé, plus la végétation est développée et saine.",
    },
    "ndmi": {
        "label": "Humidité / stress hydrique", "acronyme": "NDMI",
        "theme": "Stress hydrique",
        "description": "Teneur en eau de la végétation. Une valeur basse signale un stress hydrique (manque d'eau).",
    },
    "ndwi": {
        "label": "Eau de surface", "acronyme": "NDWI",
        "theme": "Eau",
        "description": "Présence d'eau libre ou de zones humides.",
    },
    "savi": {
        "label": "Végétation (sols peu couverts)", "acronyme": "SAVI",
        "theme": "Suivi de la végétation",
        "description": "Vigueur végétative ajustée pour les sols nus ou en début de cycle.",
    },
    "evi": {
        "label": "Végétation (couverts denses)", "acronyme": "EVI",
        "theme": "Suivi de la végétation",
        "description": "Vigueur végétative, plus fiable sur les couverts denses.",
    },
}

ORDRE = ["ndvi", "ndmi", "ndwi", "savi", "evi"]


def _r(niveau, classe, texte):
    return {"niveau": niveau, "classe": classe, "texte": texte}


def interpret(indice, v):
    """Retourne {niveau, classe (bootstrap), texte} pour une valeur d'indice."""
    if v is None:
        return _r("—", "secondary", "Donnée indisponible")

    if indice in ("ndvi", "savi", "evi"):
        if v < 0.10:
            return _r("Sol nu / Échec", "danger", "Sol nu ou culture non levée / échec")
        if v < 0.20:
            return _r("Végétation critique", "danger", "Végétation très clairsemée, situation critique")
        if v < 0.35:
            return _r("Retard / À surveiller", "warning", "Développement en retard, à surveiller")
        if v < 0.55:
            return _r("Bon comportement", "success", "Culture en bon développement")
        return _r("Excellent développement", "success", "Couvert dense, excellent développement")

    if indice == "ndmi":
        if v < 0.00:
            return _r("Stress hydrique sévère", "danger", "Déficit hydrique très important")
        if v < 0.10:
            return _r("Stress hydrique modéré", "warning", "Déficit hydrique modéré")
        if v < 0.20:
            return _r("Humidité intermédiaire", "info", "Humidité intermédiaire")
        if v < 0.35:
            return _r("Humidité élevée", "success", "Bon état hydrique")
        return _r("Végétation très humide", "success", "Végétation très humide")

    if indice == "ndwi":
        if v < 0.0:
            return _r("Pas d'eau", "secondary", "Absence d'eau de surface")
        return _r("Eau présente", "info", "Présence d'eau / zone humide")

    return _r("—", "secondary", "")


def conseil_fertilisation(ndvi, ndmi):
    """Indication prudente reliant vigueur et humidité à la fertilisation."""
    if ndvi is None:
        return None
    if ndvi < 0.40:
        if ndmi is not None and ndmi < 0.10:
            return ("Végétation faible avec signe de stress hydrique : la contrainte principale "
                    "semble être l'eau. Vérifier l'irrigation ou la pluviométrie avant tout apport d'engrais.")
        return ("Végétation faible malgré une humidité correcte : possible déficit de fertilité "
                "(azote notamment). Un diagnostic agronomique et un apport ciblé peuvent être envisagés.")
    if ndvi < 0.60:
        return ("Végétation moyenne : surveiller l'évolution ; un appoint de fertilisation peut "
                "être utile selon le stade de la culture.")
    return ("Végétation vigoureuse : pas d'indication de carence ; maintenir les pratiques en cours.")
