"""
Alertes agricoles basées sur l'impact (approche du NCM, Émirats) :

    DANGER (météo ou satellite)  ->  IMPACT sur la culture, selon son stade  ->  ACTION conseillée

Dangers évalués à une date de référence, pour chaque parcelle :
  - séquence sèche en cours, fortes pluies, fortes chaleurs (module Climat : CHIRPS / ERA5-Land) ;
  - stress hydrique observé, chute brutale de vigueur, vigueur faible pour le stade (Sentinel-2).

Stade de la culture : jours depuis le semis (enquête), sinon depuis le démarrage de la saison des
pluies de la cellule climat, sinon estimé sur la courbe NDVI de la parcelle ; découpé selon les
4 stades FAO du référentiel des cultures.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text

from app.extensions import db

SOURCE_COMPOSITE = "GEE Sentinel-2 (composite)"
J0_DEFAUT = (7, 10)          # 10 juillet si rien ne permet de dater le semis

NIVEAUX = {1: ("Vigilance", "warning", "#F4D03F"), 2: ("Alerte", "orange", "#EF6C00"), 3: ("Alerte forte", "danger", "#C62828")}

STADES = {
    "levee": "Levée / installation",
    "vegetatif": "Croissance végétative",
    "floraison": "Floraison",
    "remplissage": "Remplissage des grains",
    "maturation": "Maturation",
}

DANGERS = {
    "secheresse": ("Séquence sèche", "bi-sun"),
    "stress": ("Stress hydrique observé", "bi-droplet-half"),
    "pluie": ("Fortes pluies", "bi-cloud-rain-heavy"),
    "chaleur": ("Fortes chaleurs", "bi-thermometer-sun"),
    "chute": ("Chute brutale de vigueur", "bi-graph-down-arrow"),
    "faible": ("Vigueur faible pour le stade", "bi-flower3"),
}

# (impact, action) par danger et par stade
IMPACTS = {
    "secheresse": {
        "levee": ("Mauvaise levée, jeunes plants qui flétrissent : risque de ressemis.",
                  "Vérifier la levée ; ressemer si plus de 30 % de poquets vides ; biner pour limiter l'évaporation."),
        "vegetatif": ("Croissance ralentie, feuilles enroulées en milieu de journée.",
                      "Biner ou sarcler ; reporter l'apport d'urée jusqu'à la prochaine pluie ; irriguer si possible."),
        "floraison": ("Stade le plus sensible : mauvaise fécondation, épis mal remplis, forte perte de rendement.",
                      "Irriguer en priorité si possible ; ne pas apporter d'engrais à sec ; prévenir le producteur."),
        "remplissage": ("Grains plus légers : baisse du rendement.",
                        "Irrigation d'appoint si possible ; suivre l'évolution au prochain passage du satellite."),
        "maturation": ("Impact limité : maturité accélérée.", "Préparer la récolte."),
    },
    "stress": {
        "levee": ("Jeunes plants en manque d'eau : levée irrégulière.",
                  "Vérifier l'humidité du sol et la levée ; ressemer les manquants après la prochaine pluie."),
        "vegetatif": ("Plants en manque d'eau : croissance ralentie.",
                      "Visiter la parcelle ; biner ; différer l'urée jusqu'à une pluie ; irriguer si possible."),
        "floraison": ("Manque d'eau à la floraison : risque de forte perte de rendement.",
                      "Irriguer en priorité si possible ; visite de la parcelle sous 48 h."),
        "remplissage": ("Manque d'eau pendant le remplissage : grains plus légers.",
                        "Irrigation d'appoint si possible ; estimer la perte au carré de rendement."),
        "maturation": ("Dessèchement normal en fin de cycle ou maturité forcée.", "Préparer la récolte."),
    },
    "pluie": {
        "levee": ("Semences emportées, croûte de battance, plants asphyxiés.",
                  "Vérifier la levée et ressemer les poquets vides ; ouvrir des rigoles de drainage."),
        "vegetatif": ("Engrais lessivés, parcelle inondée, feuilles qui jaunissent.",
                      "Drainer ; refaire l'apport d'urée après ressuyage du sol."),
        "floraison": ("Verse des plants, pollen lessivé, maladies favorisées.",
                      "Drainer ; redresser et butter les plants versés ; surveiller les maladies foliaires."),
        "remplissage": ("Verse et maladies des épis.", "Drainer ; surveiller les pourritures ; butter si nécessaire."),
        "maturation": ("Pourriture des épis, grains qui germent sur pied.",
                       "Récolter dès que le sol le permet ; bien sécher les épis avant stockage."),
    },
    "chaleur": {
        "levee": ("Brûlure des jeunes plants.", "Pailler si possible ; vérifier la levée."),
        "vegetatif": ("Forte évaporation, stress des plants.", "Biner ou pailler ; irriguer de préférence le matin."),
        "floraison": ("Pollen stérile : mauvaise fécondation, épis incomplets.",
                      "Irriguer si possible, le matin ; prévenir le producteur."),
        "remplissage": ("Remplissage écourté : grains plus petits.", "Irrigation d'appoint si possible."),
        "maturation": ("Impact limité.", "Préparer la récolte."),
    },
    "chute": {
        "_": ("Dégât récent possible : chenille légionnaire, verse, sécheresse, inondation ou récolte anticipée.",
              "Visite de la parcelle sous 48 h ; inspecter les cornets (chenille légionnaire d'automne) ; "
              "traiter selon la fiche technique si l'attaque est confirmée."),
    },
    "faible": {
        "_": ("Végétation en retard : levée faible, enherbement ou carence en azote.",
              "Vérifier la densité de plants ; désherber ; apporter l'urée si le sol est humide."),
    },
}

# NDVI minimal attendu selon le stade (sinon « vigueur faible »)
NDVI_ATTENDU = {"vegetatif": 0.25, "floraison": 0.35, "remplissage": 0.30}
NDMI_A, NDMI_B = -0.27, 0.65     # NDMI habituel ≈ NDMI_A + NDMI_B × NDVI


def _stade(c, j0, au):
    a = (c.l_ini if c else None) or 25
    b = (c.l_dev if c else None) or 40
    m = (c.l_mid if c else None) or 45
    f = (c.l_fin if c else None) or 30
    j = (au - j0).days
    if j < 0:
        return None, j
    if j < a:
        return "levee", j
    if j < a + b:
        return "vegetatif", j
    if j < a + b + m // 2:
        return "floraison", j
    if j < a + b + m:
        return "remplissage", j
    if j < a + b + m + f:
        return "maturation", j
    return "termine", j


def _j0_ndvi(serie, c, annee):
    """Semis estimé : premier passage où le NDVI dépasse durablement 0,30 (fin de l'installation)."""
    l_ini = (c.l_ini if c and c.l_ini else 25)
    for (d1, v1), (d2, v2) in zip(serie, serie[1:]):
        if v1 is not None and v2 is not None and v1 >= 0.30 and v2 >= 0.28 and d1 >= date(annee, 6, 15):
            j0 = d1 - timedelta(days=l_ini)
            return max(date(annee, 6, 1), min(j0, date(annee, 8, 31)))
    return None


def _v(x):
    return f"{x:.2f}".replace(".", ",")


def _niveau(n, stade):
    """La floraison est le stade critique : un cran de plus."""
    return min(3, n + 1) if stade == "floraison" and n < 3 else n


def evaluer(au=None, pids=None):
    """Alertes à la date `au` : (alertes, contexte)."""
    from app.models.parcelle import Parcelle
    from app.referentiel.service import culture_ref
    au = au or date.today()
    annee = au.year if au.month >= 5 else au.year - 1
    debut = date(annee, 5, 15)
    filtre = "" if pids is None else " AND p.id = ANY(:ids)"
    params = {"d1": debut, "au": au, "comp": SOURCE_COMPOSITE, "ids": list(pids or [])}

    # parcelles
    q = db.session.query(Parcelle)
    if pids is not None:
        q = q.filter(Parcelle.id.in_(list(pids) or [-1]))
    parcelles = q.all()

    # séries NDVI / NDMI depuis le 15 mai jusqu'à la date de référence
    series = defaultdict(list)
    for pid, d, ndvi, ndmi in db.session.execute(text(f"""
            SELECT iv.parcelle_id, iv.date, iv.ndvi, iv.ndmi FROM indices_vegetation iv
              JOIN parcelles p ON p.id = iv.parcelle_id
             WHERE iv.date BETWEEN :d1 AND :au AND iv.source IS DISTINCT FROM :comp {filtre}
             ORDER BY iv.parcelle_id, iv.date"""), params):
        series[pid].append((d, ndvi, ndmi))

    # semis connus (enquête)
    semis = {}
    try:
        with db.session.begin_nested():
            semis = dict(db.session.execute(text("""
                SELECT DISTINCT ON (parcelle_id) parcelle_id, date_semis FROM pratiques_campagne
                 WHERE date_semis IS NOT NULL AND annee = :a ORDER BY parcelle_id, date_semis"""), {"a": annee}).all())
    except Exception:
        pass

    # climat : cellule de chaque parcelle, démarrage de saison, 30 derniers jours
    cellule, demarrage, meteo = {}, {}, defaultdict(list)
    climat_ok = False
    try:
        with db.session.begin_nested():
            cellule = dict(db.session.execute(text("SELECT parcelle_id, cellule_id FROM climat_parcelles")).all())
            demarrage = dict(db.session.execute(text(
                "SELECT cellule_id, debut FROM climat_saisons WHERE annee = :a AND debut IS NOT NULL"), {"a": annee}).all())
            for cid, d, pl, tx in db.session.execute(text("""
                    SELECT cellule_id, date, pluie_mm, tmax FROM climat_journalier
                     WHERE date BETWEEN :d0 AND :au ORDER BY cellule_id, date"""),
                    {"d0": au - timedelta(days=30), "au": au}):
                meteo[cid].append((d, pl, tx))
            climat_ok = bool(meteo)
    except Exception:
        pass

    cache_c, alertes = {}, []
    stats = {"parcelles": len(parcelles), "en_cycle": 0, "terminees": 0, "avant_semis": 0, "sans_image": 0,
             "origine_j0": defaultdict(int)}
    for p in parcelles:
        if p.culture not in cache_c:
            cache_c[p.culture] = culture_ref(p.culture)
        c = cache_c[p.culture]
        s = series.get(p.id, [])
        # date de semis
        if p.id in semis:
            j0, origine = semis[p.id], "semis (enquête)"
        elif cellule.get(p.id) in demarrage:
            j0, origine = demarrage[cellule[p.id]], "démarrage des pluies"
        else:
            j0 = _j0_ndvi([(d, v) for d, v, _ in s], c, annee)
            origine = "courbe NDVI" if j0 else "date par défaut (10 juillet)"
            j0 = j0 or date(annee, *J0_DEFAUT)
        fiable = not origine.startswith("date par défaut")
        stade, jours = _stade(c, j0, au)
        if stade is None:
            stats["avant_semis"] += 1
            continue
        if stade == "termine":
            stats["terminees"] += 1
            continue
        stats["en_cycle"] += 1
        stats["origine_j0"][origine] += 1
        base = {"parcelle": p, "culture": p.culture or (c.nom if c else ""), "stade": stade,
                "stade_lib": STADES[stade], "jours": jours, "j0": j0, "origine_j0": origine}
        trouves = []

        # --- satellite
        recents = [(d, v, m) for d, v, m in s if (au - d).days <= 12 and v is not None]
        if not recents:
            stats["sans_image"] += 1
        else:
            d, ndvi, ndmi = recents[-1]
            # stress hydrique : humidité de la végétation (NDMI) nettement sous la valeur habituelle pour
            # sa vigueur. Le NDMI suit le NDVI (médiane ≈ -0,27 + 0,65 × NDVI sur les parcelles du
            # Grenier du maïs 2026) ; évalué seulement quand le couvert est installé (NDVI ≥ 0,30).
            if ndmi is not None and ndvi >= 0.30 and stade != "maturation":
                deficit = (NDMI_A + NDMI_B * ndvi) - ndmi
                if deficit >= 0.10:
                    n = 3 if deficit >= 0.15 else 2
                    trouves.append(("stress", _niveau(n, stade),
                                    f"NDMI {_v(ndmi)} le {d:%d/%m}, nettement sous la normale pour cette vigueur ({_v(NDMI_A + NDMI_B * ndvi)})"))
            # chute de vigueur pendant la croissance (en remplissage, la baisse peut être la sénescence)
            avant = [(d2, v2) for d2, v2, _ in s if 4 <= (d - d2).days <= 20 and v2 is not None]
            if avant and stade in ("vegetatif", "floraison"):
                vmax = max(v2 for _, v2 in avant)
                dernier_bas = [v for d3, v, _ in recents[-2:] if (d - d3).days <= 10]
                confirme = len(dernier_bas) >= 2 and max(dernier_bas) <= vmax - 0.12
                if vmax - ndvi >= 0.12:
                    n = (3 if vmax - ndvi >= 0.20 else 2) if confirme else 1
                    trouves.append(("chute", n, f"NDVI {_v(vmax)} → {_v(ndvi)} en moins de 3 semaines"
                                                + ("" if confirme else " (un seul passage, à confirmer)")))
            seuil = NDVI_ATTENDU.get(stade)
            if seuil and fiable and ndvi < seuil and not any(t[0] == "chute" for t in trouves):
                trouves.append(("faible", 1, f"NDVI {_v(ndvi)} au stade {STADES[stade].lower()} (attendu ≥ {_v(seuil)})"))

        # --- climat
        jours_m = meteo.get(cellule.get(p.id), [])
        if jours_m:
            sec = 0
            for d, pl, _ in reversed(jours_m):
                if d < j0:
                    break
                if pl is None or pl >= 1.0:
                    break
                sec += 1
            if sec >= 7 and stade != "maturation":
                n = 3 if sec >= 15 else (2 if sec >= 10 else 1)
                trouves.append(("secheresse", _niveau(n, stade), f"{sec} jours consécutifs sans pluie utile (< 1 mm)"))
            der7 = [(d, pl or 0, tx) for d, pl, tx in jours_m if (au - d).days < 7]
            if der7:
                pj = max(x[1] for x in der7)
                p3 = max(sum(x[1] for x in der7[i:i + 3]) for i in range(len(der7)))
                if pj >= 50 or p3 >= 80:
                    n = 3 if pj >= 80 or p3 >= 120 else 2
                    trouves.append(("pluie", n, f"{pj:.0f} mm en un jour, {p3:.0f} mm sur 3 jours"))
                chauds = sum(1 for x in der7 if x[2] is not None and x[2] >= 38)
                if chauds >= 2 and stade in ("levee", "vegetatif", "floraison", "remplissage"):
                    n = 2 if stade == "floraison" else 1
                    trouves.append(("chaleur", n, f"{chauds} jours à 38 °C ou plus cette semaine"))

        for danger, niveau, constat in trouves:
            imp = IMPACTS[danger].get(stade) or IMPACTS[danger].get("_")
            alertes.append(dict(base, danger=danger, danger_lib=DANGERS[danger][0], icone=DANGERS[danger][1],
                                niveau=niveau, niveau_lib=NIVEAUX[niveau][0], couleur=NIVEAUX[niveau][2],
                                constat=constat, impact=imp[0], action=imp[1]))
    alertes.sort(key=lambda a: (-a["niveau"], a["danger"], a["parcelle"].commune or ""))
    stats["origine_j0"] = dict(stats["origine_j0"])
    return alertes, {"au": au, "annee": annee, "climat": climat_ok, "stats": stats}


def message(a):
    """Message court (SMS / WhatsApp) pour le producteur ou le relais."""
    p = a["parcelle"]
    return (f"ANCAR - {a['niveau_lib']} : {a['danger_lib'].lower()} sur votre parcelle de {a['culture'].lower()}"
            f"{' à ' + p.village if p.village else ''} ({a['stade_lib'].lower()}). {a['impact']} Conseil : {a['action']}")
