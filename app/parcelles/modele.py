"""
Modèle GeoJSON des parcelles ANCAR : noms de champs reconnus à l'import, sans aucune adaptation.

Un fichier qui suit ce modèle (formulaire QField / KoBo, export QGIS) s'importe tel quel :
parcelle, producteur, OP, relais, historique cultural, pratiques de la campagne et rendement.
Les anciens noms de colonnes restent acceptés (voir les listes d'alias dans l'import).
Les noms sont lus sans tenir compte des majuscules ni des accents.
"""

# (champ, rubrique, obligatoire, type, exemple, description)
CHAMPS = [
    ("id_parcelle", "Identification", True, "texte", "GM26-0001",
     "Identifiant unique et stable de la parcelle (jamais réutilisé). Sert à reconnaître la parcelle "
     "aux imports suivants : la parcelle est mise à jour au lieu d'être recréée."),
    ("programme", "Identification", False, "texte", "Grenier du maïs",
     "Programme / projet. Si vide : celui choisi au moment de l'import."),
    ("campagne", "Identification", False, "année", 2026,
     "Année de la campagne décrite par la ligne. Si vide : campagne active de la plateforme."),
    ("region", "Localisation", True, "texte", "Kaolack", "Région administrative."),
    ("departement", "Localisation", True, "texte", "Nioro du Rip", "Département."),
    ("commune", "Localisation", True, "texte", "Ndrame Escale", "Commune."),
    ("village", "Localisation", True, "texte", "Ngayenne", "Village ou localité."),
    ("op_cooperative", "Producteur", False, "texte", "FEPROMAS-CAC-BOUBOU DEME",
     "Organisation de producteurs / coopérative (créée si elle n'existe pas)."),
    ("producteur", "Producteur", True, "texte", "Mamadou BALDE", "Prénom et nom de l'exploitant."),
    ("telephone_producteur", "Producteur", False, "texte", "771234567",
     "9 chiffres, sans indicatif. Avec le nom, il distingue deux homonymes."),
    ("sexe_producteur", "Producteur", False, "Homme / Femme", "Homme", ""),
    ("relais", "Suivi", False, "texte", "Modou FALL",
     "Prénom et nom du relais / animateur / enquêteur qui a cartographié la parcelle."),
    ("telephone_relais", "Suivi", False, "texte", "773548076", ""),
    ("date_enregistrement", "Suivi", False, "date AAAA-MM-JJ", "2026-07-15", "Date de la collecte."),
    ("superficie_ha", "Parcelle", False, "nombre (ha)", 0.87,
     "Superficie calculée à partir du polygone (la plateforme la recalcule à l'import)."),
    ("superficie_declaree_ha", "Parcelle", False, "nombre (ha)", 0.9,
     "Superficie déclarée, conservée pour comparaison : la plateforme calcule la superficie réelle à partir du polygone."),
    ("statut_foncier", "Parcelle", False, "texte", "Délibération",
     "Délibération, Titre foncier, Bail, Autre…"),
    ("mode_acquisition", "Parcelle", False, "texte", "Héritage", "Héritage, Emprunt, Location, Don, Achat…"),
    ("culture", "Campagne", True, "texte", "Maïs",
     "Spéculation de la campagne (nom du référentiel des cultures : Maïs, Mil, Arachide, Oignon…)."),
    ("filiere", "Campagne", False, "texte", "Maïs",
     "Filière (Maïs, Mil, Arachide, Riz, Niébé, Sorgho, Horticulture…). Si vide : déduite de la culture."),
    ("variete", "Campagne", False, "texte", "Mastrop", "Variété semée (nom du référentiel si possible)."),
    ("culture_2025", "Précédents", False, "texte", "Arachide",
     "Culture de l'hivernage 2025. Une colonne par année (culture_2024, culture_2023…) : "
     "elles forment l'historique cultural ; celle de l'année précédente donne le précédent cultural."),
    ("culture_2024", "Précédents", False, "texte", "Mil", "Culture de l'hivernage 2024."),
    ("date_semis", "Pratiques", False, "date AAAA-MM-JJ", "2026-07-10",
     "Semis ou repiquage : point de départ du calendrier et de l'itinéraire technique."),
    ("labour", "Pratiques", False, "Oui / Non", "Oui", ""),
    ("matiere_organique", "Pratiques", False, "Oui / Non", "Non", "Fumier ou compost apporté."),
    ("npk_sacs", "Pratiques", False, "nombre", 2, "Sacs de 50 kg de NPK apportés sur la parcelle."),
    ("uree_sacs", "Pratiques", False, "nombre", 1, "Sacs de 50 kg d'urée apportés sur la parcelle."),
    ("methode_rendement", "Rendement", False, "texte", "Pose carré rendement",
     "« Pose carré rendement » (mesure au champ, sert à calibrer le modèle) ou « Estimation producteur »."),
    ("poids_carre_kg", "Rendement", False, "nombre (kg)", 2.4, "Poids récolté dans le carré de rendement."),
    ("surface_carre_m2", "Rendement", False, "nombre (m²)", 6.25, "Surface du carré (ex. 2,5 m × 2,5 m = 6,25)."),
    ("rendement_t_ha", "Rendement", False, "nombre (t/ha)", None,
     "Rendement s'il est déjà calculé ; sinon il est calculé à partir du carré."),
    ("date_recolte", "Rendement", False, "date AAAA-MM-JJ", None, ""),
    ("observations", "Suivi", False, "texte", "", "Remarques libres."),
    # champs de l'export complet (sauvegarde / transfert entre plateformes) : facultatifs à la collecte
    ("rendement_potentiel_kg_ha", "Export complet", False, "nombre (kg/ha)", None,
     "Rendement potentiel de la parcelle (sinon attribué par le référentiel)."),
    ("sol_type", "Export complet", False, "texte", None, "Sol : type, sol_ph, sol_mo (%), sol_fertilite, sol_source, sol_note."),
    ("indices", "Export complet", False, "liste", None,
     "Indices de végétation à chaque passage : [{date, ndvi, ndwi, ndmi, savi, evi}, …]. Rempli par l'export complet."),
]

NOMS = [c[0] for c in CHAMPS]
EXPORT_SEUL = {c[0] for c in CHAMPS if c[1] == "Export complet"}

# Deux parcelles d'exemple (coordonnées WGS 84, près de Nioro du Rip)
_EXEMPLES = [
    {"id_parcelle": "EX-0001", "programme": "Grenier du maïs", "campagne": 2026, "region": "Kaolack",
     "departement": "Nioro du Rip", "commune": "Ndrame Escale", "village": "Ngayenne",
     "op_cooperative": "FEPROMAS-CAC-BOUBOU DEME", "producteur": "Mamadou BALDE",
     "telephone_producteur": "771234567", "sexe_producteur": "Homme", "relais": "Modou FALL",
     "telephone_relais": "773548076", "date_enregistrement": "2026-07-15", "superficie_ha": 0.9, "superficie_declaree_ha": 0.9,
     "statut_foncier": "Délibération", "mode_acquisition": "Héritage", "culture": "Maïs", "filiere": "Maïs", "variete": "Mastrop",
     "culture_2025": "Arachide", "culture_2024": "Mil", "date_semis": "2026-07-10", "labour": "Oui",
     "matiere_organique": "Non", "npk_sacs": 2, "uree_sacs": 1, "methode_rendement": "Pose carré rendement",
     "poids_carre_kg": 2.4, "surface_carre_m2": 6.25, "rendement_t_ha": None, "date_recolte": "2026-10-20",
     "observations": ""},
    {"id_parcelle": "EX-0002", "programme": "Grenier du maïs", "campagne": 2026, "region": "Kaolack",
     "departement": "Nioro du Rip", "commune": "Ndrame Escale", "village": "Ngayenne",
     "op_cooperative": "FEPROMAS-CAC-BOUBOU DEME", "producteur": "Awa NDIAYE",
     "telephone_producteur": "781234567", "sexe_producteur": "Femme", "relais": "Modou FALL",
     "telephone_relais": "773548076", "date_enregistrement": "2026-07-15", "superficie_ha": 0.5, "superficie_declaree_ha": 0.5,
     "statut_foncier": "Autre", "mode_acquisition": "Emprunt", "culture": "Maïs", "filiere": "Maïs", "variete": "LG 336",
     "culture_2025": "Mil", "culture_2024": "Arachide", "date_semis": None, "labour": "Non",
     "matiere_organique": "Oui", "npk_sacs": 1, "uree_sacs": None, "methode_rendement": "Estimation producteur",
     "poids_carre_kg": None, "surface_carre_m2": None, "rendement_t_ha": 2.5, "date_recolte": None,
     "observations": "Semis tardif"},
]


def _carre(lon, lat, cote_m):
    d_lat = cote_m / 111320.0
    d_lon = cote_m / (111320.0 * 0.9781)          # cos(13,6°)
    return [[[lon, lat], [lon + d_lon, lat], [lon + d_lon, lat + d_lat], [lon, lat + d_lat], [lon, lat]]]


def geojson_modele():
    """FeatureCollection d'exemple (WGS 84), à ouvrir dans QGIS ou à reproduire dans QField / KoBo."""
    feats = []
    for k, props in enumerate(_EXEMPLES):
        cote = (props["superficie_declaree_ha"] * 10000) ** 0.5
        feats.append({"type": "Feature", "properties": {n: props.get(n) for n in NOMS if n not in EXPORT_SEUL},
                      "geometry": {"type": "MultiPolygon",
                                   "coordinates": [_carre(-15.7905 + k * 0.0015, 13.7342, cote)]}})
    return {"type": "FeatureCollection", "name": "modele_parcelles_ANCAR",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
            "features": feats}
