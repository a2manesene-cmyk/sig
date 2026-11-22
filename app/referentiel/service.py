"""
Calculs du référentiel : reconnaissance de la culture, calendrier des stades,
besoins en eau (FAO-56 : ETc = Kc x ET0, pluie efficace déduite) et plan de fumure
ajusté au sol de la parcelle.
"""
import math
from datetime import date, timedelta

from sqlalchemy import func, text

from app.extensions import db
from app.models.referentiel import CultureRef, VarieteRef
from app.referentiel import donnees

ETP_DEFAUT_MM = 5.0
EFFICIENCES = {"Goutte-à-goutte": 0.90, "Aspersion": 0.75, "Arrosage manuel / raie": 0.60}
EFFICIENCE_DEFAUT = "Aspersion"
SAC_KG = 50


def _norm(s):
    from app.parcelles.enquete import norm
    return norm(s or "")


def culture_ref(nom_culture):
    """CultureRef correspondant au nom de culture d'une parcelle (synonymes, sans accents)."""
    if not nom_culture:
        return None
    n = _norm(nom_culture)
    for c in db.session.query(CultureRef).all():
        noms = {_norm(c.nom)} | {_norm(x) for x in (c.synonymes or "").split(",") if x.strip()}
        if n in noms:
            return c
    return None


def initialiser(reinitialiser=False):
    """Crée les cultures et variétés de référence absentes (ou les réécrit si demandé)."""
    n_c = n_v = 0
    for d in donnees.CULTURES:
        c = db.session.query(CultureRef).filter_by(nom=d["nom"]).first()
        if c is None:
            c = CultureRef(nom=d["nom"])
            db.session.add(c)
        elif not reinitialiser:
            continue
        for k, v in d.items():
            setattr(c, k, v)
        n_c += 1
    db.session.flush()
    # Potentiel par défaut des spéculations déjà présentes (sans écraser une valeur saisie)
    for d in donnees.CULTURES:
        c = db.session.query(CultureRef).filter_by(nom=d["nom"]).first()
        if c is not None and c.rdt_potentiel_t is None:
            c.rdt_potentiel_t = d.get("rdt_potentiel_t")
    for cult, nom, pot, notes in donnees.VARIETES:
        c = db.session.query(CultureRef).filter_by(nom=cult).first()
        if c is None:
            continue
        v = db.session.query(VarieteRef).filter_by(culture_id=c.id, nom=nom).first()
        if v is None:
            db.session.add(VarieteRef(culture_id=c.id, nom=nom, notes=notes, rdt_potentiel_t=pot,
                                      source="Enquête Grenier du maïs 2026"))
            n_v += 1
        elif v.rdt_potentiel_t is None and pot is not None:
            v.rdt_potentiel_t = pot
    db.session.commit()
    return n_c, n_v


# --------------------------------------------------------------------------
# Rendements potentiels des parcelles
# --------------------------------------------------------------------------
def _varietes_parcelles(pids=None):
    """{parcelle_id: variété déclarée la plus récente} (enquêtes)."""
    try:
        with db.session.begin_nested():
            rows = db.session.execute(text("""
                SELECT DISTINCT ON (parcelle_id) parcelle_id, variete
                  FROM pratiques_campagne
                 WHERE variete IS NOT NULL AND lower(variete) NOT IN ('autre', 'non')
                 ORDER BY parcelle_id, annee DESC
            """)).all()
    except Exception:
        return {}
    return {pid: v for pid, v in rows if pids is None or pid in pids}


def resoudre_potentiel(culture_nom, variete_nom, cache=None):
    """(potentiel t/ha, origine, CultureRef, VarieteRef) pour une culture et une variété."""
    cache = cache if cache is not None else {}
    cle_c = _norm(culture_nom)
    if cle_c not in cache:
        cache[cle_c] = culture_ref(culture_nom)
    c = cache[cle_c]
    if c is None:
        return None, None, None, None
    v = None
    if variete_nom:
        cle_v = (c.id, _norm(variete_nom))
        if cle_v not in cache:
            cache[cle_v] = next((x for x in c.varietes if _norm(x.nom) == _norm(variete_nom)), None)
        v = cache[cle_v]
    if v is not None and v.rdt_potentiel_t:
        return v.rdt_potentiel_t, "variete", c, v
    if c.rdt_potentiel_t:
        return c.rdt_potentiel_t, "speculation", c, v
    return None, None, c, v


def appliquer_potentiels(pids=None):
    """Recalcule le rendement potentiel (kg/ha) de toutes les parcelles (ou de `pids`) :
    potentiel de la variété déclarée, sinon potentiel par défaut de la spéculation.
    Les potentiels saisis à la main sur la fiche parcelle sont conservés."""
    from app.models.parcelle import Parcelle
    from app.models.referentiel import PotentielParcelle

    q = db.session.query(Parcelle.id, Parcelle.culture, Parcelle.rendement_potentiel)
    if pids is not None:
        q = q.filter(Parcelle.id.in_(list(pids)))
    parcelles = q.all()
    origines = {pp.parcelle_id: pp for pp in db.session.query(PotentielParcelle)}
    varietes = _varietes_parcelles(set(pids) if pids is not None else None)
    cache = {}
    stats = {"variete": 0, "speculation": 0, "manuel": 0, "sans_referentiel": 0,
             "sans_potentiel": 0, "modifiees": 0}
    cultures_absentes, maj_parc, maj_ref = {}, [], []
    for pid, culture, pot_actuel in parcelles:
        pp = origines.get(pid)
        if pp is not None and pp.origine == "manuel":
            stats["manuel"] += 1
            continue
        pot_t, origine, c, v = resoudre_potentiel(culture, varietes.get(pid), cache)
        if c is None:
            stats["sans_referentiel"] += 1
            if culture:
                cultures_absentes[culture] = cultures_absentes.get(culture, 0) + 1
            continue
        if pot_t is None:
            stats["sans_potentiel"] += 1
            continue
        stats[origine] += 1
        kg = round(pot_t * 1000)
        if pp is None:
            pp = PotentielParcelle(parcelle_id=pid, origine=origine)
            db.session.add(pp)
        pp.origine, pp.culture_id, pp.variete_id = origine, c.id, (v.id if v is not None else None)
        if pot_actuel is None or abs(pot_actuel - kg) > 0.5:
            stats["modifiees"] += 1
            maj_parc.append({"pid": pid, "kg": kg})
            maj_ref.append({"pid": pid, "kg": kg,
                            "src": f"Potentiel {v.nom if origine == 'variete' else c.nom}"[:40]})
    if maj_parc:
        db.session.execute(text("UPDATE parcelles SET rendement_potentiel = :kg WHERE id = :pid"), maj_parc)
        # références « potentiel » (pas les rendements mesurés) alignées sur la nouvelle valeur
        db.session.execute(text("""
            UPDATE rendements_reference SET rendement_kg_ha = :kg, source = :src
             WHERE parcelle_id = :pid AND source LIKE 'Potentiel%'
        """), maj_ref)
    db.session.commit()
    stats["cultures_absentes"] = sorted(cultures_absentes.items(), key=lambda x: -x[1])
    return stats


def varietes_non_referencees():
    """Variétés déclarées dans les enquêtes mais absentes du référentiel : [(culture, variété, nb)]."""
    from app.models.parcelle import Parcelle
    try:
        with db.session.begin_nested():
            rows = db.session.execute(text("""
                SELECT p.culture, pc.variete, COUNT(DISTINCT p.id)
                  FROM pratiques_campagne pc JOIN parcelles p ON p.id = pc.parcelle_id
                 WHERE pc.variete IS NOT NULL AND lower(pc.variete) NOT IN ('autre', 'non')
                 GROUP BY 1, 2 ORDER BY 3 DESC
            """)).all()
    except Exception:
        return []
    cache, out = {}, []
    for culture, var, n in rows:
        _p, _o, c, v = resoudre_potentiel(culture, var, cache)
        if c is not None and v is None:
            out.append((c, var, n))
    return out


def comptes_parcelles():
    """Nombre de parcelles par spéculation et par variété (pour l'espace d'administration)."""
    from app.models.parcelle import Parcelle
    cache, par_c, par_v = {}, {}, {}
    cultures = dict(db.session.query(Parcelle.id, Parcelle.culture).all())
    varietes = _varietes_parcelles()
    for pid, culture in cultures.items():
        _p, _o, c, v = resoudre_potentiel(culture, varietes.get(pid), cache)
        if c is not None:
            par_c[c.id] = par_c.get(c.id, 0) + 1
            if v is not None:
                par_v[v.id] = par_v.get(v.id, 0) + 1
    return par_c, par_v


# --------------------------------------------------------------------------
# Calendrier et coefficients culturaux
# --------------------------------------------------------------------------
def kc_jour(c, j):
    """Kc du jour j après semis/repiquage (courbe FAO-56 en 4 segments)."""
    a, b, m, f = c.l_ini or 0, c.l_dev or 0, c.l_mid or 0, c.l_fin or 0
    if j < a:
        return c.kc_ini
    if j < a + b:
        return c.kc_ini + (c.kc_mid - c.kc_ini) * (j - a) / max(b, 1)
    if j < a + b + m:
        return c.kc_mid
    return c.kc_mid + (c.kc_fin - c.kc_mid) * min(1.0, (j - a - b - m) / max(f, 1))


def calendrier(c, debut):
    stades, d = [], debut
    for lib, n in (("Initial", c.l_ini), ("Développement", c.l_dev),
                   ("Mi-saison", c.l_mid), ("Arrière-saison", c.l_fin)):
        n = n or 0
        stades.append({"stade": lib, "debut": d, "fin": d + timedelta(days=max(n - 1, 0)), "jours": n})
        d += timedelta(days=n)
    return stades


# --------------------------------------------------------------------------
# Climat de la parcelle : ET0 et pluie moyennes par jour de l'année
# --------------------------------------------------------------------------
def climatologie(parcelle_id):
    """{jour_de_l'année: (et0_moyenne, pluie_moyenne)} sur toutes les années en base, et nb d'années."""
    try:
        with db.session.begin_nested():
            rows = db.session.execute(text("""
                SELECT EXTRACT(DOY FROM j.date)::int AS doy, AVG(j.et0_mm), AVG(j.pluie_mm),
                       COUNT(DISTINCT EXTRACT(YEAR FROM j.date))
                  FROM climat_parcelles pc JOIN climat_journalier j ON j.cellule_id = pc.cellule_id
                 WHERE pc.parcelle_id = :pid
                 GROUP BY 1
            """), {"pid": parcelle_id}).all()
    except Exception:
        return {}, 0
    return ({int(d): (e, p) for d, e, p, _n in rows}, max((n for *_x, n in rows), default=0))


def besoins_eau(c, debut, surface_ha, parcelle_id, efficience=None):
    """Besoins en eau d'irrigation sur le cycle : par jour, par semaine et au total."""
    clim, n_ans = climatologie(parcelle_id)
    eff = EFFICIENCES.get(efficience or EFFICIENCE_DEFAUT, 0.75)
    jours, manque = [], 0
    for j in range(c.cycle_j or 0):
        d = debut + timedelta(days=j)
        doy = min(d.timetuple().tm_yday, 365)
        e, p = clim.get(doy, (None, None))
        if e is None:
            e = ETP_DEFAUT_MM
            manque += 1
        p = p or 0.0
        kc = kc_jour(c, j)
        etc = kc * e
        p_eff = 0.8 * p if p > 5 else 0.0            # pluie efficace (FAO, méthode simplifiée)
        net = max(0.0, etc - p_eff)
        jours.append({"date": d, "kc": kc, "et0": e, "etc": etc, "pluie": p, "net": net})
    tot_net = sum(x["net"] for x in jours)
    brut = tot_net / eff if eff else tot_net
    pic = max(jours, key=lambda x: x["net"]) if jours else None
    semaines = []
    for k in range(0, len(jours), 7):
        bloc = jours[k:k + 7]
        net = sum(x["net"] for x in bloc)
        semaines.append({"debut": bloc[0]["date"], "net_mm": net, "brut_mm": net / eff,
                         "m3": net / eff * 10 * (surface_ha or 0)})
    return {
        "efficience": eff, "efficience_nom": efficience or EFFICIENCE_DEFAUT,
        "etc_mm": sum(x["etc"] for x in jours), "pluie_eff_mm": sum(x["etc"] - x["net"] for x in jours),
        "net_mm": tot_net, "brut_mm": brut, "m3": brut * 10 * (surface_ha or 0),
        "pic_mm": (pic["net"] / eff) if pic else None, "pic_date": pic["date"] if pic else None,
        "pic_litres_jour": (pic["net"] / eff) * 10000 * (surface_ha or 0) if pic else None,
        "semaines": semaines, "jours_sans_climat": manque, "n_annees": n_ans,
    }


# --------------------------------------------------------------------------
# Fumure
# --------------------------------------------------------------------------
def plan_fumure(c, debut, surface_ha, sol=None):
    """Plan de fumure à la parcelle, ajusté qualitativement au sol (module Sol)."""
    s = surface_ha or 0
    g = (lambda k: getattr(sol, k, None)) if sol is not None else (lambda k: None)
    from app.sol.referentiel import classe
    mo_faible = classe("mo", g("mo")) == "Faible"
    p_faible = classe("p_olsen", g("p_olsen")) == "Faible"
    k_faible = classe("k", g("k")) == "Faible"
    ph = g("ph")
    remarques = []

    org = None
    if c.fumure_org_min_t is not None:
        dose = c.fumure_org_max_t if (mo_faible and c.fumure_org_max_t) else c.fumure_org_min_t
        if mo_faible:
            remarques.append("Matière organique faible : dose haute de fumure organique.")
        org = {"t_ha": dose, "t": dose * s, "plage": (c.fumure_org_min_t, c.fumure_org_max_t)}

    fond = None
    if c.fond_kg:
        kg = c.fond_kg * s
        fond = {"engrais": c.engrais_fond, "kg_ha": c.fond_kg, "kg": kg, "sacs": kg / SAC_KG,
                "date": debut}
        if p_faible or k_faible:
            remarques.append("Phosphore ou potassium faible : apporter la fumure de fond complète, "
                             "enfouie avant semis / repiquage, sans la réduire.")

    apports = []
    itin = _apports_itineraire(c)
    if itin:
        # fiche technique validée : tous les apports minéraux de l'itinéraire (NPK, urée…)
        for o in itin:
            kg = o.dose_kg_ha * s
            apports.append({"jour": o.jour, "date": debut + timedelta(days=o.jour), "engrais": o.produit,
                            "kg_ha": o.dose_kg_ha, "kg": kg, "sacs": kg / SAC_KG})
    elif c.couverture_kg:
        for j in c.jours_apports:
            kg = c.couverture_kg * s
            apports.append({"jour": j, "date": debut + timedelta(days=j), "engrais": c.engrais_couverture,
                            "kg_ha": c.couverture_kg, "kg": kg, "sacs": kg / SAC_KG})

    if ph is not None and c.ph_min and ph < c.ph_min:
        remarques.append(f"pH {ph:.1f} inférieur au minimum de la culture ({c.ph_min:.1f}) : "
                         "envisager un amendement calcique (chaulage) et privilégier la fumure organique.")
    if not (org or fond or apports):
        remarques.append("Fumure non renseignée dans le référentiel pour cette culture.")
    total_min = (fond["kg"] if fond else 0) + sum(a["kg"] for a in apports)
    return {"organique": org, "fond": fond, "apports": apports, "remarques": remarques,
            "total_kg": total_min, "total_sacs": total_min / SAC_KG}


def _apports_itineraire(c):
    """Apports minéraux de couverture de l'itinéraire technique (fiche validée), hors fumier."""
    try:
        with db.session.begin_nested():
            ops = c.itineraire.all() if c.id else []
    except Exception:
        return []
    from app.referentiel.extraction import norm
    return [o for o in ops if o.categorie == "Fumure" and o.jour > 0 and o.dose_kg_ha and o.produit
            and not any(k in norm(o.produit) for k in ("fumier", "compost"))]


def pratiques_observees(c):
    """Doses moyennes déclarées dans les enquêtes pour cette culture (kg/ha)."""
    try:
        with db.session.begin_nested():
            r = db.session.execute(text("""
                SELECT COUNT(*), AVG(pc.npk_sacs * 50 / NULLIF(p.surface_ha, 0)),
                       AVG(pc.uree_sacs * 50 / NULLIF(p.surface_ha, 0)),
                       AVG(pc.rendement_t_ha) FILTER (WHERE pc.rendement_valide AND pc.rendement_t_ha > 0),
                       COUNT(*) FILTER (WHERE pc.rendement_valide AND pc.rendement_t_ha > 0)
                  FROM pratiques_campagne pc JOIN parcelles p ON p.id = pc.parcelle_id
                 WHERE (pc.npk_sacs IS NOT NULL OR pc.uree_sacs IS NOT NULL OR pc.rendement_t_ha IS NOT NULL)
                   AND lower(pc.culture) = ANY(:noms)
            """), {"noms": [c.nom.lower()] + [x.strip().lower() for x in (c.synonymes or "").split(",")]}).one()
    except Exception:
        return None
    return {"n": r[0], "npk_kg_ha": r[1], "uree_kg_ha": r[2], "rdt": r[3], "n_rdt": r[4]} if r[0] else None


def date_debut(p, demande=None):
    """Date de semis / repiquage : celle demandée, sinon celle de l'enquête, sinon aujourd'hui."""
    if demande:
        return demande, "saisie"
    try:
        from app.models.enquete import PratiqueCampagne
        with db.session.begin_nested():
            pr = (db.session.query(PratiqueCampagne).filter_by(parcelle_id=p.id)
                  .filter(PratiqueCampagne.date_semis.isnot(None))
                  .order_by(PratiqueCampagne.annee.desc()).first())
        if pr:
            return pr.date_semis, "enquête"
    except Exception:
        pass
    return date.today(), "aujourd'hui"
