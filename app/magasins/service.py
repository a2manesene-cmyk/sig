"""
Magasins des OP : import GeoJSON, rattachement aux OP, synthèse capacité / production.

Formats acceptés à l'import :
  - fichier nettoyé (Magasins_OP_nettoye.geojson) : id_magasin, op, localite, commune, type,
    responsable, telephone, longueur_m, largeur_m, capacite_t, observations — WGS 84 ;
  - fichier de collecte brut (Magasins OP.geojson) : « Nom de l'OP », « Localites », « Commune »,
    « Nom Responsable », « Telephone », « Dimension en metre », « Capacite de stockage en Tonne »,
    en UTM 28N (EPSG:32628) ou WGS 84.
Un point en (0, 0) est considéré comme une position manquante.
"""
import json
import re
import unicodedata
from difflib import SequenceMatcher

from pyproj import Transformer
from sqlalchemy import func, text

from app.extensions import db
from app.models.magasin import MagasinOP
from app.models.organisation import OrganisationProducteur

_UTM = Transformer.from_crs(32628, 4326, always_xy=True)

# noms rencontrés dans les fiches de collecte -> nom de l'OP sur la plateforme
ALIAS_OP = {
    "COPROSEM MERETO": "COPROSEM MERETO",
    "COOPERATIVE DE MEDINA YERRA": "COOP-MEDINA YERA",
    "DIANKANCOUNDA": "COOP-DIANKACOUNDA",
    "COOPERATIVE DE MADIALY": "COOP-MISSIRA",
    "GIE ARAFAT": "ARAFAT",
    "CAC DE BOUBOU DEME": "FEPROMAS-CAC-BOUBOU DEME",
    "COPROSEM KAHI": "COPROSEM-KAHI",
    "SCOOP COLLEGE DES JEUNES": "FEPROMAS-COLLEGE DES JEUNES",
    "COPROSEM NGANDA": "COPROSEM-NGANDA",
}
_GENERIQUES = {"COOP", "COOPERATIVE", "COPROSEM", "FEPROMAS", "CAC", "DE", "DES", "DU", "GIE", "SCOOP", "LA", "LE"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", s).split())


def _coeur(s):
    return " ".join(m for m in norm(s).split() if m not in _GENERIQUES)


def trouver_op(nom, ops=None):
    """OrganisationProducteur correspondant à un nom de fichier (alias, nom exact, puis ressemblance)."""
    if not nom:
        return None
    ops = ops if ops is not None else OrganisationProducteur.query.all()
    par_nom = {norm(o.nom): o for o in ops}
    n = norm(nom)
    cible = ALIAS_OP.get(n)
    if cible and norm(cible) in par_nom:
        return par_nom[norm(cible)]
    if n in par_nom:
        return par_nom[n]
    c = _coeur(nom)
    meilleur, score = None, 0.0
    for o in ops:
        s = SequenceMatcher(None, c, _coeur(o.nom)).ratio()
        if s > score:
            meilleur, score = o, s
    return meilleur if score >= 0.85 else None


def _num(v):
    if v is None:
        return None
    m = re.search(r"\d+(?:[.,]\d+)?", str(v))
    return float(m.group().replace(",", ".")) if m else None


def _tel(v):
    d = re.sub(r"\D", "", str(v or ""))
    if d.startswith("221") and len(d) == 12:
        d = d[3:]
    return d or None


def _nom_personne(s):
    mots = " ".join(str(s or "").split()).split()
    if not mots:
        return None
    if len(mots) == 1:
        return mots[0]
    return " ".join([m.capitalize() for m in mots[:-1]] + [mots[-1].upper()])


def _lieu(s):
    """« DAROU SALAM » -> « Darou Salam » ; les noms déjà en casse mixte sont gardés."""
    if not s:
        return None
    s = " ".join(str(s).split())
    return s.title() if s.isupper() or s.islower() else s


def _premier(props, *noms):
    pn = {norm(k): v for k, v in props.items()}
    for n in noms:
        v = pn.get(norm(n))
        if v not in (None, ""):
            return v.strip() if isinstance(v, str) else v
    return None


def _est_utm(crs, x, y):
    nom = json.dumps(crs or {})
    if "32628" in nom:
        return True
    return abs(x) > 180 or abs(y) > 90


def lire_geojson(contenu):
    """Liste de dicts prêts à enregistrer (sans OP résolue)."""
    gj = json.loads(contenu)
    feats = gj.get("features") or []
    lignes = []
    for i, f in enumerate(feats, 1):
        p = f.get("properties") or {}
        g = f.get("geometry") or {}
        lon = lat = None
        if g.get("type") == "Point" and g.get("coordinates"):
            x, y = g["coordinates"][:2]
            if not (abs(x) < 1e-9 and abs(y) < 1e-9):
                lon, lat = _UTM.transform(x, y) if _est_utm(gj.get("crs"), x, y) else (x, y)
        commune = _premier(p, "commune", "Commune")
        type_ = _premier(p, "type")
        # fiche de collecte : la colonne « Commune » contient parfois la nature du magasin
        if commune and norm(commune).startswith(("MAGAZIN", "MAGASIN")):
            type_ = type_ or ("Magasin en location" if "LOCATION" in norm(commune) else "Magasin de la coopérative")
            commune = _premier(p, "localite", "Localites", "Localite")
        longueur = _num(_premier(p, "longueur_m"))
        largeur = _num(_premier(p, "largeur_m"))
        dim = _premier(p, "Dimension en metre", "dimension")
        if dim and longueur is None:
            v = re.findall(r"\d+(?:[.,]\d+)?", str(dim))
            if len(v) >= 2:
                longueur, largeur = float(v[0].replace(",", ".")), float(v[1].replace(",", "."))
        obs = [_premier(p, "observations")] if _premier(p, "observations") else []
        note = _premier(p, "Field11")
        if note and not any(note in o for o in obs):
            obs.append(f"Note de collecte : {note}")
        lignes.append({
            "code": str(_premier(p, "id_magasin", "code") or f"MAG-{i:02d}"),
            "op": _premier(p, "op"), "nom_op_source": _premier(p, "nom_op_source", "Nom de l'OP", "Nom OP"),
            "localite": _lieu(_premier(p, "localite", "Localites", "Localite")),
            "commune": _lieu(commune), "type": type_,
            "responsable": _nom_personne(_premier(p, "responsable", "Nom Responsable")),
            "telephone": _tel(_premier(p, "telephone", "Telephone")),
            "longueur_m": longueur, "largeur_m": largeur,
            "capacite_t": _num(_premier(p, "capacite_t", "Capacite de stockage en Tonne", "capacite")),
            "observations": "; ".join(o for o in obs if o) or None,
            "lon": lon, "lat": lat,
        })
    return lignes


def importer(contenu, remplacer=False):
    """Enregistre les magasins (mise à jour par code). Retourne un bilan."""
    lignes = lire_geojson(contenu)
    if not lignes:
        raise ValueError("Le fichier ne contient aucun magasin.")
    ops = OrganisationProducteur.query.all()
    if remplacer:
        MagasinOP.query.delete()
        db.session.flush()
    existants = {m.code: m for m in MagasinOP.query.all()}
    bilan = {"crees": 0, "maj": 0, "sans_op": [], "sans_position": 0}
    for l in lignes:
        op = trouver_op(l["op"] or l["nom_op_source"], ops)
        if op is None:
            bilan["sans_op"].append(l["nom_op_source"] or l["op"] or l["code"])
        m = existants.get(l["code"])
        if m is None:
            m = MagasinOP(code=l["code"]); db.session.add(m); existants[l["code"]] = m
            bilan["crees"] += 1
        else:
            bilan["maj"] += 1
        m.organisation_id = op.id if op else None
        for k in ("nom_op_source", "localite", "commune", "type", "responsable", "telephone",
                  "longueur_m", "largeur_m", "capacite_t", "observations"):
            setattr(m, k, l[k])
        if not m.nom_op_source:
            m.nom_op_source = l["op"]
        if l["lon"] is not None:
            m.geom = f"SRID=4326;POINT({l['lon']} {l['lat']})"
        else:
            m.geom = None
            bilan["sans_position"] += 1
    db.session.commit()
    return bilan


def placer(m, lat, lon):
    m.geom = f"SRID=4326;POINT({lon} {lat})" if lat is not None and lon is not None else None


def coordonnees(ids=None):
    """{magasin_id: (lat, lon)}"""
    q = "SELECT id, ST_Y(geom), ST_X(geom) FROM magasins_op WHERE geom IS NOT NULL"
    return {r[0]: (r[1], r[2]) for r in db.session.execute(text(q))}


def production_par_op(pids=None):
    """Production attendue (tonnes) par OP, à partir du rendement attendu de chaque parcelle."""
    from app.models.parcelle import Parcelle
    from app.models.producteur import Producteur
    from app.rendement.calcul import rendement_attendu_par_parcelle
    rdt = rendement_attendu_par_parcelle(pids)
    q = (db.session.query(Parcelle.id, Parcelle.surface_ha, Producteur.organisation_id)
         .join(Producteur, Producteur.id == Parcelle.producteur_id)
         .filter(Producteur.organisation_id.isnot(None)))
    if pids is not None:
        q = q.filter(Parcelle.id.in_(pids))
    out = {}
    for pid, ha, oid in q.all():
        r = rdt.get(pid)
        if r and ha:
            out[oid] = out.get(oid, 0.0) + r * ha / 1000.0
    return out


def synthese(org_ids=None, pids=None):
    """Par OP : nombre de magasins, capacité, production attendue, taux de couverture."""
    q = (db.session.query(MagasinOP.organisation_id, func.count(MagasinOP.id),
                          func.sum(MagasinOP.capacite_t),
                          func.count(MagasinOP.id).filter(MagasinOP.geom.is_(None)))
         .group_by(MagasinOP.organisation_id))
    if org_ids is not None:
        q = q.filter(MagasinOP.organisation_id.in_(org_ids or [-1]))
    mag = {oid: (n, cap or 0.0, sans) for oid, n, cap, sans in q.all()}
    prod = production_par_op(pids)
    ops = OrganisationProducteur.query
    if org_ids is not None:
        ops = ops.filter(OrganisationProducteur.id.in_(org_ids or [-1]))
    lignes = []
    for o in ops.order_by(OrganisationProducteur.nom).all():
        n, cap, sans = mag.get(o.id, (0, 0.0, 0))
        p = prod.get(o.id)
        if not n and not p:
            continue
        lignes.append({"id": o.id, "nom": o.nom, "magasins": n, "capacite": round(cap, 1),
                       "sans_position": sans, "production": round(p, 1) if p else None,
                       "couverture": round(100 * cap / p) if p and n else None})
    lignes.sort(key=lambda l: (l["couverture"] is None, l["couverture"] if l["couverture"] is not None else 0))
    return lignes
