"""
Données initiales du référentiel des cultures.

Sources :
- [FAO56] Allen et al., 1998. Crop evapotranspiration, FAO Irrigation and Drainage Paper 56,
  tableaux 11 (durée des stades, lignes « Arid Region ») et 12 (Kc).
- [CDH] Beniest J. et al., 1987. Guide pratique du maraîchage au Sénégal. ISRA/FAO, Centre pour le
  Développement de l'Horticulture, Cambérène. Fiches techniques (doses converties à l'hectare :
  g/m² x 10 = kg/ha). Texte numérisé de qualité moyenne : valeurs à confirmer.
- [CA2019] de Bon et al., 2019. Rendements et pratiques des cultures maraîchères en agriculture
  biologique au Sénégal. Cahiers Agricultures 28 (rendements observés dans les Niayes).

Les stades FAO-56 sont ramenés à la durée de cycle locale (CDH) en gardant leurs proportions.
« Par analogie » : culture absente des tableaux FAO-56, coefficients d'une culture voisine.
"""

FAO = "FAO-56 (tableaux 11 et 12)"
CDH = "Guide pratique du maraîchage au Sénégal (CDH/ISRA, 1987)"


# Potentiels par défaut (t/ha) différents du haut de fourchette
POTENTIEL_DEFAUT = {"Maïs": 4.5}     # valeur utilisée jusqu'ici par la plateforme (Mastrop, 4–5 t/ha)


def stades(fao, cycle):
    """Ramène les 4 stades FAO (ini, dev, mid, fin) à la durée de cycle locale."""
    tot = sum(fao)
    if not cycle:
        return list(fao)
    s = [round(x * cycle / tot) for x in fao]
    s[2] += cycle - sum(s)          # l'arrondi est absorbé par la mi-saison
    return s


def _c(nom, synonymes, famille, systeme, cycle, fao_stades, kc, rdt=(None, None),
       fumure=(None, None), fond=(None, None), couverture=(None, None), apports="",
       ph_min=None, periode=None, ecartements=None, sources=(), notes=""):
    l = stades(fao_stades, cycle)
    return {
        "nom": nom, "synonymes": synonymes, "famille": famille, "systeme": systeme,
        "cycle_j": cycle or sum(l), "l_ini": l[0], "l_dev": l[1], "l_mid": l[2], "l_fin": l[3],
        "kc_ini": kc[0], "kc_mid": kc[1], "kc_fin": kc[2],
        "rdt_min": rdt[0], "rdt_max": rdt[1],
        # potentiel par défaut de la spéculation : haut de la fourchette de référence
        "rdt_potentiel_t": POTENTIEL_DEFAUT.get(nom, rdt[1]),
        "fumure_org_min_t": fumure[0], "fumure_org_max_t": fumure[1],
        "engrais_fond": fond[0], "fond_kg": fond[1],
        "engrais_couverture": couverture[0], "couverture_kg": couverture[1], "apports_j": apports,
        "ph_min": ph_min, "periode": periode, "ecartements": ecartements,
        "source": " ; ".join(sources) + (f". {notes}" if notes else ""),
        "a_valider": True,
    }


CULTURES = [
    _c("Maïs", "mais, maïs, mais grain", "Céréale", "Pluvial", 100, (25, 40, 45, 30), (0.30, 1.20, 0.35),
       rdt=(2.0, 5.0), periode="Hivernage, semis à l'installation des pluies",
       sources=[FAO + " (cycle ramené à 100 j, variétés précoces)",
                "Rendements : carrés de rendement Grenier du maïs 2026 (médiane ~3 t/ha)"],
       notes="Fumure à renseigner à partir de la fiche technique ISRA / ANCAR du maïs."),
    _c("Tomate", "tomate, tomates", "Légume-fruit", "Irrigué", 135, (30, 40, 40, 25), (0.60, 1.15, 0.80),
       rdt=(8.8, 25), fumure=(20, 30), fond=("10-10-20", 400), couverture=("10-10-20", 200),
       apports="15,30,50,80", ph_min=5.5,
       periode="Semis sept.–avril, plantation oct.–mai (variétés d'hivernage toute l'année)",
       ecartements="Lignes doubles 0,5 m (déterminée) ou 1 m (indéterminée), 0,4–0,5 m sur la ligne",
       sources=[FAO, CDH, "Rendements observés Niayes 8,8–12,9 t/ha (Cahiers Agricultures, 2019)"]),
    _c("Oignon", "oignon, oignons, oignon bulbe", "Racine / bulbe", "Irrigué", 145, (20, 35, 110, 45),
       (0.70, 1.05, 0.75), rdt=(10.7, 30), fumure=(10, 20), fond=("10-10-20", 250),
       couverture=("10-10-20", 250), apports="20,40,60", ph_min=6.0,
       periode="Semis nov.–janv., repiquage déc.–janv. (bulbilles oct.–nov.)",
       ecartements="5–7 lignes par planche, 20 cm entre lignes, 10 cm sur la ligne",
       sources=[FAO + " (oignon sec)", CDH, "Rendements Sénégal 10,7–32 t/ha (Cahiers Agricultures, 2019)"],
       notes="Culture sensible à l'acidité (pH 6–6,8). Cycle compté depuis le repiquage."),
    _c("Oignon vert", "oignon vert, ciboule, oignon feuille", "Légume-feuille", "Irrigué", 95, (20, 45, 20, 10),
       (0.70, 1.00, 1.00), sources=[FAO + " (oignon vert)"], notes="Fumure : se référer à l'oignon."),
    _c("Chou", "chou, choux, chou pomme, chou pommé, chou cabus", "Légume-feuille", "Irrigué", 90,
       (40, 60, 50, 15), (0.70, 1.05, 0.95), rdt=(14.7, 40), fumure=(20, 30), fond=("10-10-20", 275),
       couverture=("10-10-20", 200), apports="20,35", ph_min=6.0,
       periode="Semis sept.–avril (saison fraîche) ; mai–juillet en hivernage (rendement plus faible)",
       ecartements="3 lignes par planche, 0,35–0,40 m entre lignes et sur la ligne",
       sources=[FAO, CDH, "Rendement observé Niayes 14,7 t/ha (Cahiers Agricultures, 2019)"],
       notes="Cycle compté depuis le repiquage (80–100 j, 60 j en hivernage)."),
    _c("Carotte", "carotte, carottes", "Racine / bulbe", "Irrigué", 110, (20, 30, 30, 20),
       (0.70, 1.05, 0.95), rdt=(15, 40), fumure=(10, 20), fond=("10-10-20", 500),
       couverture=("10-10-20", 300), apports="20,40", ph_min=5.5,
       periode="Octobre–novembre à mars–avril",
       ecartements="Lignes 0,2–0,3 m, planches de 1 m ; éclaircir à 20–30 plants par mètre",
       sources=[FAO, CDH]),
    _c("Aubergine", "aubergine, aubergines", "Légume-fruit", "Irrigué", 130, (30, 40, 40, 20),
       (0.60, 1.05, 0.90), rdt=(25, 40), fumure=(20, 30), fond=("10-10-20", 500),
       couverture=("10-10-20", 250), apports="40,60,80", ph_min=5.5,
       periode="Toute l'année, meilleurs résultats en saison fraîche",
       ecartements="Lignes doubles espacées de 0,5 m, 0,75 m sur la ligne, en quinconce",
       sources=[FAO, CDH]),
    _c("Poivron", "poivron, poivrons", "Légume-fruit", "Irrigué", 150, (30, 40, 110, 30),
       (0.60, 1.05, 0.90), rdt=(15, 30), fumure=(15, 25), fond=("10-10-20", 500),
       couverture=("10-10-20", 200), apports="15,30,50,80", ph_min=5.5,
       periode="Semis sept.–févr., plantation oct.–mars",
       ecartements="Lignes doubles 0,4–0,6 m, environ 0,5 m sur la ligne",
       sources=[FAO, CDH]),
    _c("Piment", "piment, piments", "Légume-fruit", "Irrigué", 180, (30, 40, 110, 30),
       (0.60, 1.05, 0.90), rdt=(8, 15), fumure=(15, 20), fond=("10-10-20", 300),
       couverture=("10-10-20", 150), apports="21,42,63,84,105,126",
       periode="Toute l'année ; meilleur en hivernage et zones chaudes",
       ecartements="Lignes doubles 0,4–0,8 m, 0,4–0,8 m sur la ligne",
       sources=[FAO + " (par analogie avec le poivron)", CDH],
       notes="Durée de cycle peu lisible dans la source (6–8 mois) : à confirmer."),
    _c("Gombo", "gombo, gombos", "Légume-fruit", "Irrigué", 150, (30, 40, 40, 20),
       (0.60, 1.05, 0.90), fumure=(10, 20), couverture=("10-10-20", 200), apports="25,50,70",
       periode="Toute l'année", ecartements="Lignes simples 0,9 m, 0,4–0,5 m sur la ligne",
       sources=[FAO + " (par analogie avec l'aubergine)", CDH],
       notes="Rendement et fumure de fond illisibles dans la source : à renseigner."),
    _c("Pomme de terre", "pomme de terre, pommes de terre, patate", "Racine / bulbe", "Irrigué", 90,
       (25, 30, 45, 30), (0.50, 1.15, 0.75), rdt=(20, 40), fumure=(15, 20), fond=("10-10-20", 260),
       couverture=("10-10-20", 250), apports="14,35",
       periode="Oct.–nov. (hâtive), déc.–janv. (pleine saison), févr.–mars (tardive)",
       ecartements="Lignes 0,6 m, 0,3 m sur la ligne", sources=[FAO, CDH]),
    _c("Navet", "navet, navets, navet chinois", "Racine / bulbe", "Irrigué", 45, (20, 30, 30, 20),
       (0.50, 1.10, 0.95), rdt=(15, 25), fumure=(15, 25), fond=("10-10-20", 300),
       couverture=("10-10-20", 200), apports="18", periode="Toute l'année, surtout saison fraîche",
       ecartements="Lignes 0,4 m ; éclaircir à 0,2–0,3 m",
       sources=[FAO + " (Kc tableau 12 ; stades par analogie avec la carotte)", CDH]),
    _c("Persil", "persil", "Légume-feuille", "Irrigué", 90, (20, 30, 40, 10), (0.70, 1.00, 0.95),
       sources=[FAO + " (par analogie avec l'épinard)"],
       notes="Fumure et rendement à renseigner."),
    _c("Menthe", "menthe, nana, nana (menthe)", "Légume-feuille", "Irrigué", 60, (15, 15, 20, 10),
       (0.60, 1.15, 1.10), sources=[FAO + " (Kc menthe, tableau 12)"],
       notes="Culture pérenne récoltée par coupes : cycle indicatif entre deux coupes. Fumure à renseigner."),
]

# (culture, variété, potentiel t/ha, notes)
VARIETES = [
    ("Maïs", "Mastrop", 4.5, "Variété majoritaire du Grenier du maïs (enquête 2026). Potentiel 4–5 t/ha "
                             "repris de la valeur utilisée jusqu'ici par la plateforme : à confirmer."),
    ("Maïs", "LG 336", None, "Variété présente dans l'enquête Grenier du maïs 2026. Potentiel à renseigner."),
]
