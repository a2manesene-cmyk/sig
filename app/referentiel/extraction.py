"""
Extraction des rubriques d'une fiche technique (texte issu de Word, PDF ou OCR).

Le format suivi est celui des fiches ISRA / ANCAR (Généralités, Choix variétal, Techniques
culturales, Entretien, Ennemis et traitement phytosanitaire, Récolte et conservation), mais
chaque rubrique est cherchée par mots-clés : l'ordre des sections et la mise en page peuvent
varier. Tout ce qui est extrait est proposé à la validation de l'administrateur avant d'entrer
dans le référentiel ; les incohérences internes de la fiche sont signalées.
"""
import re
import unicodedata
from difflib import SequenceMatcher

SEP = "⟂"                    # séparateur de colonnes (tableaux Word)
NB = r"(\d+(?:[.,]\d+)?)"
A = r"\s*(?:à|a|-|–|/)\s*"   # « 10 à 20 », « 10-20 »


def sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def norm(s):
    return re.sub(r"[^a-z0-9+]+", " ", sans_accents(s).lower()).strip()


def num(s):
    if s is None:
        return None
    try:
        return float(str(s).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _formule(s):
    """« 10.10.20 », « NPK 10-10-20 », « 10 10 20 » → « 10-10-20 » ; urée reste « Urée »."""
    if s and "ur" in norm(s):
        return "Urée"
    m = re.search(r"(\d{1,2})\s*[.\-\s]\s*(\d{1,2})\s*[.\-\s]\s*(\d{1,2})", s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else (s or "").strip() or None


# --------------------------------------------------------------------------
# Dictionnaires : matières actives et ennemis des cultures
# --------------------------------------------------------------------------
MATIERES_ACTIVES = [
    # insecticides / nématicides / acaricides
    "Deltaméthrine", "Cyperméthrine", "Alpha-cyperméthrine", "Lambda-cyhalothrine", "Bifenthrine",
    "Abamectine", "Émamectine benzoate", "Acétamipride", "Imidaclopride", "Thiaméthoxame", "Spinosad",
    "Spinétorame", "Chlorantraniliprole", "Flubendiamide", "Indoxacarbe", "Profénofos",
    "Chlorpyrifos-éthyl", "Diméthoate", "Malathion", "Bacillus thuringiensis", "Azadirachtine",
    "Oxamyl", "Fosthiazate", "Éthoprophos", "Fluopyram", "Carbofuran", "Lufénuron", "Novaluron",
    "Pyriproxyfène", "Spirotétramate", "Fipronil", "Méthomyl", "Téflubenzuron", "Hexythiazox",
    "Propargite", "Fenpyroximate", "Dicofol",
    # fongicides
    "Mancozèbe", "Manèbe", "Propinèbe", "Azoxystrobine", "Chlorothalonil", "Difénoconazole",
    "Thiophanate-méthyl", "Iprodione", "Hydroxyde de cuivre", "Oxychlorure de cuivre", "Myclobutanil",
    "Propamocarbe", "Tébuconazole", "Trifloxystrobine", "Métalaxyl", "Fosétyl-aluminium", "Cymoxanil",
    "Soufre", "Carbendazime", "Bénomyl", "Captane", "Thirame", "Diméthomorphe", "Boscalid",
    "Pyraclostrobine",
    # herbicides
    "Glyphosate", "Pendiméthaline", "Oxadiazon", "Atrazine", "Nicosulfuron", "S-métolachlore",
    "Fluazifop-p-butyl", "2,4-D",
]
_MA = [(m, norm(m), len(norm(m).split())) for m in MATIERES_ACTIVES]

MALADIES = ["alternariose", "mildiou", "oidium", "fusariose", "fonte de semis", "pythium", "racines roses",
            "rouille", "bacteriose", "virose", "mosaique", "fletrissement", "pourriture", "cercosporiose",
            "anthracnose", "helminthosporiose", "charbon", "stemphyliose", "sclerotiniose", "botrytis",
            "maladie"]
ADVENTICES = ["mauvaises herbes", "adventices", "striga"]
RAVAGEURS = ["chenille", "thrips", "puceron", "mouche blanche", "mouches blanches", "aleurode", "acarien",
             "nematode", "mineuse", "tuta absoluta", "helicoverpa", "spodoptera", "legionnaire", "foreur",
             "criquet", "termite", "cochenille", "punaise", "mouche des fruits", "mouches des fruits",
             "coleoptere", "teigne", "plutella", "bruche", "oiseaux", "jasside", "cicadelle", "noctuelle",
             "courtiliere", "rongeur"]


def type_ennemi(nom):
    n = norm(nom)
    if any(k in n for k in ADVENTICES):
        return "Adventice"
    if any(k in n for k in MALADIES):
        return "Maladie"
    return "Ravageur"


def matieres_actives(texte, seuil=0.88):
    """Matières actives reconnues dans un texte (orthographe tolérante : « Lamda-cyhalotrine »,
    « Chorpyrifos-ethyl », « Bacillus thurengiensis »)."""
    mots = norm(texte).split()
    trouvees = []
    for canon, n, k in _MA:
        if canon in trouvees:
            continue
        for i in range(0, max(0, len(mots) - k + 1)):
            fen = " ".join(mots[i:i + k])
            if fen == n or (len(n) > 5 and abs(len(fen) - len(n)) <= 3
                            and SequenceMatcher(None, fen, n).ratio() >= seuil):
                trouvees.append(canon)
                break
    return trouvees


# --------------------------------------------------------------------------
# Outils de recherche dans le texte
# --------------------------------------------------------------------------
def _fenetre(texte, debut_re, longueur=600, fin_re=None, dernier=False):
    """Texte qui suit le titre correspondant à `debut_re` (jusqu'à `fin_re`). `debut_re` peut être une
    liste de motifs par ordre de priorité ; `dernier` : dernière occurrence plutôt que la première."""
    m = None
    for rx in (debut_re if isinstance(debut_re, (list, tuple)) else [debut_re]):
        occ = list(re.finditer(rx, texte, re.I))
        if occ:
            m = occ[-1] if dernier else occ[0]
            break
    if not m:
        return None
    t = texte[m.end():m.end() + longueur]
    if fin_re:
        f = re.search(fin_re, t, re.I)
        if f:
            t = t[:f.start()]
    return t


def _phrase(texte, mot_re):
    m = re.search(r"[^.\n/]*" + mot_re + r"[^.\n]*\.?", texte, re.I)
    return re.sub(r"\s+", " ", m.group(0)).strip(" -/") if m else None


def _ligne_apres(texte, titre_re):
    """Valeur d'un champ « Titre : valeur » (même ligne ou ligne suivante)."""
    m = re.search(titre_re + r"\s*:?\s*([^\n]*)(?:\n([^\n]*))?", texte, re.I)
    if not m:
        return None
    v = (m.group(1) or "").strip() or (m.group(2) or "").strip()
    return v.strip(" :/") or None


MOIS = r"(janv|f[eé]v|mars|avr|mai|juin|juil|ao[uû]t|sept|oct|nov|d[eé]c)"
SAISONS_RE = (r"^(primeur|pleine[\s-]*saison|arri[eè]re[\s-]*saison|hivernage|saison des pluies|"
              r"contre[\s-]*saison(?:\s+(?:froide|chaude))?|saison\s+s[eè]che(?:\s+(?:froide|chaude))?)\b")
TITRE_SECTION = re.compile(r"^[A-ZÉÈÀÙÂÊÎÔÛÇ][A-ZÉÈÀÙÂÊÎÔÛÇ' \-]{5,}:?$")


def _nom_variete(v):
    v = re.sub(r"\s+", " ", v).strip(" -–•.;,:*")
    v = re.sub(r"\s*\+\s*$", "+", v)
    if not v or len(v) > 40 or len(v) < 2 or not re.search(r"[A-Za-zÀ-ÿ]", v):
        return None
    if v.isupper() and len(v) > 2:
        mots = v.split()
        v = " ".join(w if re.fullmatch(r"F\d", w) else (w.lower() if 0 < i < len(mots) - 1 and w.lower() in
                     ("de", "du", "des", "la", "le", "d'", "et") else w.capitalize()) for i, w in enumerate(mots))
    return v


def _varietes(lignes, i0):
    """Variétés listées sous l'en-tête de saison (lignes à tirets), à partir de la ligne i0."""
    out, i, vides = [], i0, 0
    while i < len(lignes):
        l = lignes[i].strip()
        if not l:
            vides += 1
            if vides > 1 and out:
                break
            i += 1
            continue
        vides = 0
        if re.match(SAISONS_RE, l, re.I) or TITRE_SECTION.match(l):
            break
        if not re.match(r"^[-–•*]", l) and out and len(l) > 40:
            break
        if ":" in l or "|" in l:
            if out:
                break
            i += 1
            continue
        for morceau in re.split(r"[;,]|\s/\s", l):
            nv = _nom_variete(morceau)
            if nv:
                out.append(nv)
        i += 1
    return out


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def _normaliser_ocr(t):
    """Erreurs d'OCR fréquentes et lignes coupées au milieu d'une phrase."""
    t = re.sub(r"(\d)\s*[I|1l]\s*/\s*m\s*[2²]", r"\1 l/m2", t)
    t = re.sub(r"\b[I|1]\s*/\s*m\s*[2²]", "l/m2", t)
    t = re.sub(r"(?<=\d)\s*[Kk][Gg]\b", " kg", t)
    return t


def _joindre(t):
    return re.sub(r"([^\n.:;])\n(?=[a-zà-ÿ(0-9])", r"\1 ", t)


def extraire(texte):
    brut = _normaliser_ocr(texte or "")
    lignes = brut.split("\n")
    t = _joindre(brut)
    r = {"conflits": [], "non_trouves": []}

    r["nom_scientifique"] = _ligne_apres(t, r"nom\s+scientifique")
    r["famille_botanique"] = _ligne_apres(t, r"famille\s+botanique")
    m = re.search(r"fiche\s+technique\s*(?:\n|:|du|de la|de l'|de)?\s*([A-ZÉÈa-zéè' \-]{3,40})", t, re.I)
    r["titre"] = m.group(1).strip().title() if m else None

    # ---- saisons et variétés
    saisons, toutes = [], []
    for i, l in enumerate(lignes):
        ms = re.match(SAISONS_RE, l.strip(), re.I)
        if not ms or len(l.strip()) > 40:
            continue
        periode, j = None, i + 1
        while j < len(lignes) and not lignes[j].strip():
            j += 1
        if j < len(lignes) and re.search(MOIS, lignes[j], re.I) and not lignes[j].strip().startswith("-"):
            periode, j = lignes[j].strip(), j + 1
        vs = _varietes(lignes, j)
        if vs or periode:
            nom = ms.group(1).replace("  ", " ")
            saisons.append({"saison": nom[0].upper() + nom[1:].lower(), "periode": periode, "varietes": vs})
    vus = {}
    for s in saisons:
        for v in s["varietes"]:
            k = norm(v).replace(" ", "")
            if k not in vus:
                vus[k] = {"nom": v, "saisons": [s["saison"]]}
                toutes.append(vus[k])
            else:
                if s["saison"] not in vus[k]["saisons"]:
                    vus[k]["saisons"].append(s["saison"])
                if vus[k]["nom"].isupper() or (not v.isupper() and v[0].isupper() and v != vus[k]["nom"]
                                               and vus[k]["nom"].lower() == v.lower() and v.istitle()):
                    vus[k]["nom"] = v
    r["saisons"] = saisons
    r["varietes"] = toutes
    if saisons:
        r["periode"] = " ; ".join(f"{s['saison']} : {s['periode']}" for s in saisons if s["periode"])
    else:
        r["periode"] = _ligne_apres(t, r"\bp[ée]riode(?:\s+de\s+(?:semis|culture))?")

    # ---- pépinière et semis
    pep = _fenetre(t, r"p[ée]pini[eè]re\s*:", 250)
    if pep:
        m = re.search(NB + A + NB + r"\s*kg\s*de\s*graines", pep, re.I)
        r["semences_pepiniere"] = f"{m.group(1)}–{m.group(2)} kg de graines/ha" if m else None
    duree = _fenetre(t, r"dur[ée]e\s*:", 60)
    m = re.search(NB + A + NB + r"\s*jours", duree or "", re.I)
    r["pepiniere_j"] = [int(num(m.group(1))), int(num(m.group(2)))] if m and pep else None
    sd = _fenetre(t, r"semis\s+direct\s*:", 120)
    m = re.search(NB + A + NB + r"\s*kg", sd or "", re.I)
    r["semis_direct"] = f"{m.group(1)}–{m.group(2)} kg/ha" if m else None

    r["ecartements"] = _phrase(t, r"[ée]cartement")
    r["reference"] = "repiquage" if re.search(r"apr[eè]s\s+(?:le\s+)?repiquage|repiquage\s*:", t, re.I) else "semis"

    # ---- préparation du sol
    prep = _phrase(t, r"travail du sol")
    r["travail_sol"] = prep
    r["travail_sol_j"] = None
    if prep:
        if re.search(r"un mois avant", prep, re.I):
            r["travail_sol_j"] = -30
        else:
            m = re.search(r"(\d+)\s*(jours|semaines)\s*avant", prep, re.I)
            if m:
                r["travail_sol_j"] = -int(m.group(1)) * (7 if m.group(2).startswith("sem") else 1)
    r["traitement_sol"] = _phrase(t, r"traitement du sol")

    # ---- fumure organique
    m = re.search(NB + A + NB + r"\s*(?:tonnes|t)\s*(?:/\s*ha\s*)?(?:de|d')\s*(?:fumier|compost|fumure|mati)", t, re.I)
    r["fumure_org"] = [num(m.group(1)), num(m.group(2))] if m else None

    # ---- fumure de fond
    fond = _fenetre(t, r"fertilisation\s+de\s+fond|fumure\s+de\s+fond", 700,
                    r"traitement du sol|fertilisation de couverture|repiquage\s*:")
    r["fond"] = None
    if fond:
        m = re.search(NB + r"\s*kg\s*(?:/\s*ha\s*)?d.engrais[^(\n]{0,30}\(([^)]+)\)", fond, re.I) or \
            re.search(NB + r"\s*kg\s*(?:/\s*ha\s*)?(?:de\s+)?(?:NPK\s*)?(\d{1,2}[.\-]\d{1,2}[.\-]\d{1,2})", fond, re.I)
        if m:
            r["fond"] = {"kg": num(m.group(1)), "engrais": _formule(m.group(2))}
    tab_fond = re.search(r"(?m)^(?:[^\n]*/\s*)?fond\s*(?:" + SEP + r")?\s*(?:NPK\s*)?(\d{1,2}[.\-]\d{1,2}[.\-]\d{1,2}|ur[ée]e)\s*(?:"
                         + SEP + r")?\s*" + NB, brut, re.I)
    if tab_fond:
        kg = num(tab_fond.group(2))
        if r["fond"] and abs(r["fond"]["kg"] - kg) > 0.5:
            r["conflits"].append(f"Fumure de fond : {r['fond']['kg']:g} kg/ha dans le texte, {kg:g} kg/ha dans le tableau.")
        if not r["fond"]:
            r["fond"] = {"kg": kg, "engrais": _formule(tab_fond.group(1))}

    # ---- fumure de couverture
    couv = _fenetre(t, [r"fertilisation\s+de\s+couverture|fumure\s+de\s+couverture", r"fumure\s+d.entretien"], 1500,
                    r"irrigation\s*:|sarclo|ENNEMIS|RAVAGEURS")
    apports, uree = [], None
    if couv:
        m = re.search(NB + r"\s*kg\s*(?:/\s*ha\s*)?d.engrais[^(\n]{0,30}\(([^)]+)\)", couv, re.I) or \
            re.search(NB + r"\s*kg\s*(?:de\s+)?(?:NPK\s*)?(\d{1,2}[.\-]\d{1,2}[.\-]\d{1,2})", couv, re.I)
        kg_txt, eng = (num(m.group(1)), _formule(m.group(2))) if m else (None, None)
        jours = [int(x) for x in re.findall(r"(\d+)\s*(?:jours|j)\s*apr[eè]s", couv, re.I)]
        tab = re.findall(r"couverture\s*\d*\s*\((\d+)\s*(?:JAR|JAS|j)[^)]*\)\s*(?:" + SEP + r")?\s*(?:NPK\s*)?"
                         r"(\d{1,2}[.\-]\d{1,2}[.\-]\d{1,2}|ur[ée]e)\s*(?:" + SEP + r")?\s*" + NB, couv, re.I)
        if tab:
            kg_tab = {num(x[2]) for x in tab}
            if kg_txt is not None and any(abs(k - kg_txt) > 0.5 for k in kg_tab):
                r["conflits"].append(
                    f"Fumure de couverture : le texte indique {kg_txt:g} kg/ha par apport, le tableau "
                    f"{' / '.join(f'{k:g}' for k in sorted(kg_tab))} kg/ha. Valeur du tableau retenue : à vérifier.")
            for j, e, kg in tab:
                apports.append({"jour": int(j), "engrais": _formule(e), "kg": num(kg)})
            jt = [a["jour"] for a in apports]
            if jours and sorted(jours) != sorted(jt):
                r["conflits"].append(f"Jours des apports : {jours} dans le texte, {jt} dans le tableau.")
        elif kg_txt is not None:
            apports = [{"jour": j, "engrais": eng, "kg": kg_txt} for j in jours]
        mu = re.search(NB + r"\s*kg\s*(?:/\s*ha\s*)?d.ur[ée]e", couv, re.I)
        if mu:
            kg_u = num(mu.group(1))
            mf = re.search(r"fractionner[^.]{0,40}?en\s+(deux|2|trois|3)[^.]{0,30}?entre\s+(?:le\s+)?(\d+)\D{1,12}?(\d+)", couv, re.I)
            if mf:
                n = 3 if mf.group(1) in ("trois", "3") else 2
                j1, j2 = int(mf.group(2)), int(mf.group(3))
                js = [j1, j2] if n == 2 else [j1, (j1 + j2) // 2, j2]
                uree = [{"jour": j, "engrais": "Urée", "kg": round(kg_u / n, 1)} for j in js]
            else:
                j0 = apports[0]["jour"] if apports else None
                uree = [{"jour": j0, "engrais": "Urée", "kg": kg_u}] if j0 is not None else None
    r["couverture"] = apports
    r["uree"] = uree or []

    # ---- irrigation
    systemes = [("Gravitaire", r"gravitaire|[àa] la raie"), ("Aspersion", r"aspersion"),
                ("Goutte-à-goutte", r"goutte\s*[àa]\s*goutte")]
    irr_zone = _fenetre(t, [r"\birrigation\s*:", r"(?m)^\s*irrigation\s*$"], 2500, r"sarclo|ENNEMIS|RAVAGEURS") or ""
    positions = []
    for nom, rx in systemes:
        m = re.search(r"pour\s+le\s+syst[eè]me\s+(?:par\s+)?(?:" + rx + ")", irr_zone, re.I)
        if m:
            positions.append((m.start(), nom))
    positions.sort()
    irrigation = []
    ORD = {"premi": 1, "1": 1, "deuxi": 2, "2": 2, "second": 2, "troisi": 3, "3": 3, "quatri": 4, "4": 4}
    for k, (pos, nom) in enumerate(positions):
        bloc = irr_zone[pos:positions[k + 1][0] if k + 1 < len(positions) else len(irr_zone)]
        for m in re.finditer(NB + r"(?:" + A + NB + r")?\s*l\s*/\s*m\s*[2²]\s*(?:/\s*j(?:our)?\s*)?dans\s+la\s+"
                             r"(premi\w*|deuxi\w*|second\w*|troisi\w*|quatri\w*|\d)\w*\s*phase\s*(?:\(([^)]*)\))?",
                             bloc, re.I):
            ph = next((v for kk, v in ORD.items() if m.group(3).lower().startswith(kk)), None)
            d1 = num(m.group(1))
            d2 = num(m.group(2)) if m.group(2) else d1
            irrigation.append({"systeme": nom, "phase": ph, "libelle": (m.group(4) or "").strip(),
                               "debut": d1, "fin": d2})
    r["irrigation"] = irrigation
    r["arret_irrigation"] = (_phrase(irr_zone, r"arr[eê]ter\s+l.(?:irrigation|arrosage)")
                             or _phrase(t, r"arr[eê]ter\s+l.(?:irrigation|arrosage)\s+quand"))

    r["sarclage"] = _phrase(irr_zone + "\n" + (_fenetre(t, r"sarclo[-\s]*binages?\s*:", 300) or ""),
                            r"(?:veille|au besoin)")
    r["sarclage_veille_apports"] = bool(re.search(r"veille\s+des\s+apports", t, re.I))

    # ---- cycle
    zone_cycle = _fenetre(t, r"occupation\s+du\s+terrain|dur[ée]e\s+du\s+cycle|\bcycle\s*:", 500,
                          r"r[ée]colte\s*:|RENDEMENT") or ""
    cycles = []
    for m in re.finditer(NB + A + NB + r"\s*jours", zone_cycle, re.I):
        avant = norm(zone_cycle[max(0, m.start() - 45):m.start()])
        mode = ("bulbilles" if "bulbille" in avant else "semis direct" if "semis direct" in avant
                else "repiquage" if "repiquage" in avant else "semis" if "semis" in avant else "cycle")
        cycles.append({"mode": mode, "min": int(num(m.group(1))), "max": int(num(m.group(2)))})
    r["cycles"] = cycles
    choix = next((c for c in cycles if c["mode"] == r["reference"]), None) or next(
        (c for c in cycles if c["mode"] in ("cycle", "semis")), None) or (cycles[0] if cycles else None)
    r["cycle_j"] = round((choix["min"] + choix["max"]) / 2) if choix else None
    r["cycle_mode"] = choix["mode"] if choix else None

    # ---- récolte
    rec = _fenetre(t, r"r[ée]colte\s*:", 400, r"RENDEMENT|CONSERVATION\s*:|\n[A-Z][a-zé]+\s*:", dernier=True)
    r["recolte"] = re.sub(r"\s+", " ", re.sub(r"\s*/\s*", " ", rec)).strip(" /") if rec else None
    m = re.search(NB + A + NB + r"\s*jours\s+apr[eè]s\s+l.arr[eê]t\s+des\s+(?:arrosages|irrigations)", t, re.I)
    r["recolte_apres_arret"] = [int(num(m.group(1))), int(num(m.group(2)))] if m else None

    # ---- rendement
    m = (re.search(r"rendement.{0,700}?" + NB + A + NB + r"\s*(?:tonnes|t\b|t/ha)", t, re.I | re.S)
         or re.search(NB + A + NB + r"\s*tonnes\s+(?:pour|par|à)\s+(?:un\s+|l')?hectare", t, re.I))
    r["rendement"] = [num(m.group(1)), num(m.group(2))] if m else None

    # ---- ennemis et traitements
    r["ennemis"] = _ennemis(brut)
    r["nb_traitement"] = _phrase(t, r"mouillant")

    # ---- auteurs
    m = re.search(r"\bauteurs?\s*:?\s*\n(.{0,600})", t, re.I | re.S)
    r["auteurs"] = re.sub(r"\s*\n\s*", " ", m.group(1)).strip() if m else None

    for cle, lib in (("cycle_j", "durée du cycle"), ("rendement", "rendement"), ("fond", "fumure de fond"),
                     ("couverture", "fumure de couverture"), ("fumure_org", "fumure organique"),
                     ("varietes", "variétés"), ("ennemis", "ennemis et traitements"),
                     ("ecartements", "écartements")):
        if not r.get(cle):
            r["non_trouves"].append(lib)
    return r


def _ennemis(t):
    zone = _fenetre(t, r"ravageurs\s+et\s+maladies|ennemis\s+(?:et|des)|traitements?\s+phytosanitaires?", 5000,
                    r"\n\s*NB\s*:|R[ÉE]COLTE ET CONSERVATION|OCCUPATION DU TERRAIN|RENDEMENT|FIN DU TABLEAU")
    if not zone:
        return []
    out = []
    lignes = [l for l in zone.split("\n") if l.strip()]
    if any(SEP in l for l in lignes):                     # tableau Word : nom ⟂ (illustration) ⟂ traitement
        for l in lignes:
            if SEP in l:
                cel = [c.strip(" /") for c in l.split(SEP)]
                nom = re.sub(r"\s*/\s*", " – ", cel[0]).strip(" :–")
                nom = re.sub(r"\s*:\s*–\s*", " – ", nom)
                if not nom or "traitement" in norm(cel[-1])[:12] and "ravageur" in norm(nom):
                    continue
                trait = re.sub(r"\s*/\s*", " ; ", cel[-1]) if len(cel) > 1 else ""
                out.append(_ennemi(nom, trait))
            elif out:
                out[-1]["precisions"] = ((out[-1]["precisions"] + " ; ") if out[-1]["precisions"] else "") + \
                    re.sub(r"\s*/\s*", ", ", l.strip(" /"))
                out[-1]["matieres_actives"] = list(dict.fromkeys(out[-1]["matieres_actives"] + matieres_actives(l)))
        return [e for e in out if e["nom"]]
    # texte (PDF, OCR) : un ennemi commence par un nom connu ou un intitulé en capitales
    connus = RAVAGEURS + MALADIES[:-1] + ADVENTICES
    courant = None
    for l in lignes:
        n = norm(l)
        debut = (re.match(r"^[A-ZÉÈ][A-ZÉÈ \-]{3,30}\s*:?$", l.strip())
                 or any(n.startswith(k) for k in connus))
        if debut and not matieres_actives(l[:40]):
            nom = l.strip(" :")
            courant = _ennemi(nom.capitalize() if nom.isupper() else nom, "")
            out.append(courant)
        elif courant is not None:
            courant["traitement"] = (courant["traitement"] + " " + l.strip()).strip()
            courant["matieres_actives"] = list(dict.fromkeys(courant["matieres_actives"] + matieres_actives(l)))
    return out


def _ennemi(nom, traitement):
    parts = [x.strip() for x in nom.strip().split(" – ")]
    nom = " – ".join(x.capitalize() if x.isupper() else x for x in parts)
    return {"nom": nom, "type": type_ennemi(nom), "precisions": "",
            "matieres_actives": matieres_actives(traitement), "traitement": traitement.strip()}


# --------------------------------------------------------------------------
# Itinéraire technique proposé (jours comptés depuis le semis ou le repiquage)
# --------------------------------------------------------------------------
def itineraire(r):
    ops = []
    ref = r.get("reference") or "semis"

    def op(cat, lib, jour, dose=None, produit=None, details=None, optionnel=False):
        ops.append({"categorie": cat, "libelle": lib, "jour": jour, "dose": dose, "produit": produit,
                    "details": details or "", "optionnel": optionnel})

    if ref == "repiquage" and r.get("pepiniere_j"):
        a, b = r["pepiniere_j"]
        op("Semis", "Semis en pépinière", -round((a + b) / 2), None, None,
           f"{r.get('semences_pepiniere') or ''} ; durée en pépinière {a}–{b} jours".strip(" ;"))
    if r.get("travail_sol"):
        op("Préparation", "Travail du sol (désherbage, pré-irrigation, labour)",
           r.get("travail_sol_j") if r.get("travail_sol_j") is not None else -30, details=r["travail_sol"])
    if r.get("traitement_sol"):
        op("Protection", "Traitement du sol (insecticide-nématicide), au besoin",
           r.get("travail_sol_j") if r.get("travail_sol_j") is not None else -30,
           details=r["traitement_sol"], optionnel=True)
    if r.get("fumure_org"):
        a, b = r["fumure_org"]
        op("Fumure", "Fumure organique (fumier ou compost) enfouie", 0, a * 1000, "Fumier / compost",
           f"{a:g} à {b:g} t/ha selon la richesse du sol")
    if r.get("fond"):
        op("Fumure", "Fumure de fond", 0, r["fond"]["kg"], r["fond"]["engrais"])
    op("Installation", "Repiquage" if ref == "repiquage" else "Semis", 0, details=r.get("ecartements") or "")
    apports = sorted((r.get("couverture") or []) + (r.get("uree") or []), key=lambda x: (x["jour"] or 0))
    deja = set()
    for x in apports:
        if r.get("sarclage_veille_apports") and x["jour"] and x["jour"] - 1 not in deja:
            op("Entretien", "Sarclo-binage (veille de l'apport d'engrais)", x["jour"] - 1)
            deja.add(x["jour"] - 1)
        op("Fumure", f"Fumure de couverture : {x['engrais']}", x["jour"], x["kg"], x["engrais"])
    cyc = r.get("cycle_j")
    if cyc:
        if r.get("recolte_apres_arret"):
            a, b = r["recolte_apres_arret"]
            op("Irrigation", "Arrêt de l'irrigation", cyc - round((a + b) / 2), details=r.get("arret_irrigation") or "")
        op("Récolte", "Récolte", cyc, details=(r.get("recolte") or "")[:300])
    ops.sort(key=lambda o: (o["jour"] if o["jour"] is not None else 0))
    return ops
