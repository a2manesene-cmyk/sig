"""
Calculs agro-climatiques (fonctions pures, sans base de données).

- Grille CHIRPS (0,05°) : identification de la cellule d'un point.
- ETP de référence (Hargreaves, FAO-56).
- Indicateurs de saison : démarrage (critère AGRHYMET), fin (bilan hydrique),
  séquences sèches, cumuls.

Les paramètres sont regroupés en tête de fichier pour être ajustés avec l'ANACIM / l'ISRA.
"""
import math
from datetime import date, timedelta

# --- Grille CHIRPS ---------------------------------------------------------
PAS = 0.05            # degrés
LON0, LAT0 = -180.0, -50.0

# --- Paramètres agro-climatiques --------------------------------------------
JOUR_SEC_MM = 1.0           # jour sec : pluie < 1 mm
DEBUT_MIN = (5, 1)          # recherche du démarrage à partir du 1er mai
DEBUT_CUMUL_MM = 20.0       # >= 20 mm en 1 à 3 jours consécutifs...
DEBUT_FENETRE_J = 3
DEBUT_CONTROLE_J = 30       # ... sans séquence sèche de plus de 7 jours dans les 30 jours suivants
DEBUT_SEC_MAX_J = 7
FIN_MIN = (9, 1)            # la fin est recherchée à partir du 1er septembre
ETP_DEFAUT_MM = 5.0         # si l'ETP manque
SEQ_SECHE_LONGUE_J = 10

# Réserve utile (mm) selon la texture du sol — valeurs indicatives pour le maïs
RU_PAR_TEXTURE = {
    "Sable": 50, "Sable limoneux": 70, "Limon sableux": 90, "Limon": 120,
    "Limon argilo-sableux": 110, "Limon argileux": 130, "Argile": 140,
}
RU_DEFAUT = 70


def cellule(lon, lat):
    """Indices (i, j), code et centre de la cellule CHIRPS contenant le point."""
    i = int(math.floor((lon - LON0) / PAS))
    j = int(math.floor((lat - LAT0) / PAS))
    return i, j, f"{i}_{j}", LON0 + (i + 0.5) * PAS, LAT0 + (j + 0.5) * PAS


def ru_texture(texture):
    return RU_PAR_TEXTURE.get(texture or "", RU_DEFAUT)


def et0_hargreaves(tmin, tmax, lat, jour):
    """ETP de référence (mm/j) — Hargreaves (FAO-56, éq. 52)."""
    if tmin is None or tmax is None:
        return None
    j = jour.timetuple().tm_yday
    phi = math.radians(lat)
    dr = 1 + 0.033 * math.cos(2 * math.pi * j / 365)
    delta = 0.409 * math.sin(2 * math.pi * j / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    ra = (24 * 60 / math.pi) * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws))
    tmoy = (tmin + tmax) / 2
    return round(max(0.0, 0.0023 * 0.408 * ra * (tmoy + 17.8) * math.sqrt(max(tmax - tmin, 0))), 2)


def _seq_seches(jours, pluie):
    """Séquences de jours secs consécutifs : liste de (début, longueur)."""
    seqs, deb, n = [], None, 0
    for d in jours:
        p = pluie.get(d)
        if p is not None and p < JOUR_SEC_MM:
            if n == 0:
                deb = d
            n += 1
        else:
            if n:
                seqs.append((deb, n))
            n = 0
    if n:
        seqs.append((deb, n))
    return seqs


def demarrage(pluie, annee, derniere):
    """Démarrage agronomique (critère AGRHYMET). Renvoie (date|None, nb faux départs, provisoire)."""
    d = date(annee, *DEBUT_MIN)
    faux = 0
    while d <= derniere:
        fen = [pluie.get(d + timedelta(k)) or 0 for k in range(DEBUT_FENETRE_J)]
        p0 = pluie.get(d)
        if p0 is not None and p0 >= JOUR_SEC_MM and sum(fen) >= DEBUT_CUMUL_MM:
            suite = [d + timedelta(k) for k in range(1, DEBUT_CONTROLE_J + 1)]
            suite_dispo = [x for x in suite if x <= derniere]
            seqs = _seq_seches(suite_dispo, pluie)
            if all(n <= DEBUT_SEC_MAX_J for _deb, n in seqs):
                return d, faux, len(suite_dispo) < DEBUT_CONTROLE_J
            faux += 1
            # on repart après la séquence sèche qui a invalidé ce départ
            longue = next(deb for deb, n in seqs if n > DEBUT_SEC_MAX_J)
            d = longue + timedelta(1)
            continue
        d += timedelta(1)
    return None, faux, False


def fin_saison(pluie, etp, debut, derniere, ru):
    """Fin de saison : 1er jour après le 1er septembre où la réserve en eau du sol est vide."""
    if debut is None:
        return None
    stock, d = 0.0, debut
    seuil = date(debut.year, *FIN_MIN)
    while d <= derniere:
        e = etp.get(d)
        stock = min(ru, max(0.0, stock + (pluie.get(d) or 0) - (e if e is not None else ETP_DEFAUT_MM)))
        if d >= seuil and stock <= 0:
            return d
        d += timedelta(1)
    return None


def indicateurs_saison(pluie, etp, annee, ru=RU_DEFAUT):
    """Indicateurs de la saison des pluies pour une série journalière {date: mm}."""
    jours_an = sorted(d for d in pluie if d.year == annee and pluie[d] is not None)
    if not jours_an:
        return None
    derniere = jours_an[-1]
    debut, faux, provisoire = demarrage(pluie, annee, derniere)
    fin = fin_saison(pluie, etp, debut, derniere, ru)
    res = {
        "debut": debut, "fin": fin, "faux_departs": faux, "derniere_donnee": derniere,
        "complete": fin is not None,
        "cumul_mai_oct": round(sum(pluie[d] for d in jours_an if 5 <= d.month <= 10), 1),
        "longueur_j": None, "cumul_mm": None, "nb_jours_pluie": None,
        "seq_seche_max_j": None, "seq_seche_max_debut": None, "nb_seq_seches_10j": None,
        "debut_provisoire": provisoire,
    }
    if debut is None:
        return res
    borne = fin or derniere
    periode = [d for d in jours_an if debut <= d <= borne]
    seqs = _seq_seches(periode, pluie)
    # une séquence sèche qui touche la fin de saison fait partie de la fin, pas de la saison
    if fin is not None:
        seqs = [(deb, n) for deb, n in seqs if deb + timedelta(n - 1) < fin]
    smax = max(seqs, key=lambda s: s[1]) if seqs else (None, 0)
    res.update(
        longueur_j=(fin - debut).days if fin else None,
        cumul_mm=round(sum(pluie[d] for d in periode), 1),
        nb_jours_pluie=sum(1 for d in periode if pluie[d] >= JOUR_SEC_MM),
        seq_seche_max_j=smax[1], seq_seche_max_debut=smax[0],
        nb_seq_seches_10j=sum(1 for _d, n in seqs if n >= SEQ_SECHE_LONGUE_J),
    )
    return res
