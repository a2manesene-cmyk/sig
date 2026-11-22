"""
Référentiel du module Sol : paramètres, plages plausibles, seuils de classes, texture.

Les seuils sont des références générales (à valider avec l'ISRA / l'INP) :
modifiez-les ici, ils s'appliquent à toute la plateforme.
"""

# (clé, libellé, unité, décimales, plage plausible (min, max), colonne IFDC)
PARAMETRES = [
    ("ph",      "pH eau",            "",           1, (4, 9),     "pH"),
    ("p_olsen", "Phosphore (Olsen)", "ppm",        1, (0, 100),   "P(Olsen)"),
    ("k",       "Potassium (K)",     "ppm",        0, (0, 1000),  "K"),
    ("mo",      "Matière organique", "%",          2, (0, 6),     None),
    ("n",       "Azote total (N)",   "%",          3, (0, 0.3),   "N"),
    ("c_total", "Carbone total",     "%",          2, (0, 3),     "T-C"),
    ("cec",     "CEC",               "meq/100 g",  1, (0, 40),    "C.E.C"),
    ("ca",      "Calcium (Ca)",      "ppm",        0, (0, 4000),  "Ca"),
    ("mg",      "Magnésium (Mg)",    "ppm",        0, (0, 1500),  "Mg"),
    ("s",       "Soufre (S)",        "ppm",        1, (0, 300),   "S"),
    ("zn",      "Zinc (Zn)",         "ppm",        2, (0, 30),    "Zn"),
    ("b",       "Bore (B)",          "ppm",        2, (0, 5),     "B"),
    ("sable",   "Sable",             "%",          1, (0, 100),   "%Sand"),
    ("argile",  "Argile",            "%",          1, (0, 100),   "%Clay"),
    ("limon",   "Limon",             "%",          1, (0, 100),   "%Silt"),
]
CLES = [p[0] for p in PARAMETRES]
PARAM = {p[0]: p for p in PARAMETRES}
CLES_IFDC = [p[0] for p in PARAMETRES if p[5]]

SOURCES_CAR = ["Analyse labo", "Kit terrain", "Observation terrain"]
SOURCE_IFDC = "IFDC (interpolé)"

# Rattachement parcelle -> points IFDC
RAYON_MAX_M = 5000      # au-delà : hors couverture
NB_VOISINS = 5
SEUIL_BONNE_M = 2000    # point le plus proche < 2 km : fiabilité bonne

# Seuils de classes : (bornes croissantes, libellés)
CLASSES = {
    "ph": ([5.0, 5.5, 6.5, 7.5], ["Très acide", "Acide", "Légèrement acide", "Neutre", "Basique"]),
    "p_olsen": ([10, 20], ["Faible", "Moyen", "Élevé"]),
    "k": ([78, 156], ["Faible", "Moyen", "Élevé"]),          # 78 ppm = 0,2 meq/100 g
    "mo": ([1, 2], ["Faible", "Moyen", "Élevé"]),
    "n": ([0.05, 0.1], ["Faible", "Moyen", "Élevé"]),
}
COULEUR_CLASSE = {
    "Faible": "danger", "Moyen": "warning", "Élevé": "success",
    "Très acide": "danger", "Acide": "warning", "Légèrement acide": "info",
    "Neutre": "success", "Basique": "secondary",
}


def classe(cle, valeur):
    if valeur is None or cle not in CLASSES:
        return None
    bornes, libelles = CLASSES[cle]
    for b, lib in zip(bornes, libelles):
        if valeur < b:
            return lib
    return libelles[-1]


def texture_usda(sable, argile, limon):
    """Classe texturale USDA (classes des sols sableux à limoneux)."""
    if sable is None or argile is None or limon is None:
        return None
    if limon + 1.5 * argile < 15:
        return "Sable"
    if limon + 2 * argile < 30:
        return "Sable limoneux"
    if (7 <= argile < 20 and sable > 52) or (argile < 7 and limon < 50):
        return "Limon sableux"
    if 7 <= argile < 27 and 28 <= limon < 50 and sable <= 52:
        return "Limon"
    if 20 <= argile < 35 and limon < 28 and sable > 45:
        return "Limon argilo-sableux"
    if argile >= 35:
        return "Argile"
    return "Limon argileux"


def contraintes(a):
    """Liste des contraintes de fertilité d'une analyse (objet ou dict)."""
    g = (lambda k: a.get(k)) if isinstance(a, dict) else (lambda k: getattr(a, k, None))
    out = []
    if classe("ph", g("ph")) in ("Très acide", "Acide"):
        out.append("Sol acide")
    for cle, lib in (("p_olsen", "P"), ("k", "K"), ("mo", "MO"), ("n", "N")):
        if classe(cle, g(cle)) == "Faible":
            out.append(f"{lib} faible")
    return out


def fertilite(a):
    """Synthèse Faible / Moyenne / Bonne à partir des contraintes."""
    g = (lambda k: a.get(k)) if isinstance(a, dict) else (lambda k: getattr(a, k, None))
    if all(g(k) is None for k in ("ph", "p_olsen", "k", "mo", "n")):
        return None
    nb = len(contraintes(a))
    if nb <= 1:
        return "Bonne"
    if nb <= 3:
        return "Moyenne"
    return "Faible"
