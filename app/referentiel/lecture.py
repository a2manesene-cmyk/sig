"""
Lecture des fiches techniques : texte brut d'un fichier Word, PDF ou image.

- .docx           : lecture directe du document (paragraphes, tableaux, zones de texte), sans OCR.
- .pdf            : couche texte du PDF ; si la page n'en a pas (scan), OCR page par page.
- .jpg/.png/.tif  : OCR (photo ou scan d'une fiche papier).
- .doc / .odt     : converti en .docx par LibreOffice s'il est installé.

OCR : Tesseract (langue française), appelé en ligne de commande.
Installation sur le serveur Ubuntu :  sudo apt install tesseract-ocr tesseract-ocr-fra poppler-utils
"""
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

FORMATS = {".docx", ".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".doc", ".odt"}
SEP_CELLULE = " ⟂ "          # séparateur des colonnes d'un tableau dans le texte extrait
MIN_CAR_PAGE = 80             # en dessous : page considérée comme scannée → OCR


class LectureErreur(Exception):
    pass


def ocr_disponible():
    return shutil.which("tesseract") is not None


def lire(chemin):
    """(texte, méthode, confiance OCR moyenne en % ou None)."""
    ext = os.path.splitext(chemin)[1].lower()
    if ext not in FORMATS:
        raise LectureErreur(f"Format {ext or 'inconnu'} non pris en charge (Word .docx, PDF ou image).")
    if ext in (".doc", ".odt"):
        return lire(_convertir_docx(chemin))
    if ext == ".docx":
        return _lire_docx(chemin), "Word (lecture directe)", None
    if ext == ".pdf":
        return _lire_pdf(chemin)
    texte, conf = _ocr_image(chemin)
    return texte, "OCR (image)", conf


# --------------------------------------------------------------------------
# Word
# --------------------------------------------------------------------------
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
VML_TEXTBOX = "{urn:schemas-microsoft-com:vml}textbox"


def _lire_docx(chemin):
    try:
        from lxml import etree
        with zipfile.ZipFile(chemin) as z:
            racine = etree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise LectureErreur("Fichier Word illisible ou endommagé.") from exc
    lignes = []
    corps = racine.find(f"{W}body")
    for el in corps:
        _bloc(el, lignes)
    return _nettoyer("\n".join(lignes))


def _texte_paragraphe(p, zones):
    """Texte d'un paragraphe ; les zones de texte qu'il contient sont mises de côté dans `zones`."""
    morceaux = []

    def parcourir(el):
        for e in el:
            if e.tag == MC_FALLBACK or e.tag == VML_TEXTBOX:
                continue                               # double de la zone de texte (ancien format)
            if e.tag == f"{W}r" and _barre(e):
                continue                               # texte barré ou masqué = supprimé par les relecteurs
            if e.tag == f"{W}txbxContent":
                zones.append(e)
                continue
            if e.tag == f"{W}t":
                morceaux.append(e.text or "")
            elif e.tag in (f"{W}tab",):
                morceaux.append(" ")
            elif e.tag in (f"{W}br", f"{W}cr"):
                morceaux.append("\n")
            else:
                parcourir(e)
    parcourir(p)
    return "".join(morceaux)


def _barre(run):
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    for tag in ("strike", "dstrike", "vanish"):
        e = rpr.find(f"{W}{tag}")
        if e is not None and e.get(f"{W}val", "true").lower() not in ("0", "false", "off"):
            return True
    return False


def _bloc(el, lignes):
    if el.tag == f"{W}p":
        zones = []
        t = _texte_paragraphe(el, zones)
        if t.strip():
            lignes.extend(x for x in t.split("\n"))
        for z in zones:
            for e in z:
                _bloc(e, lignes)
    elif el.tag == f"{W}tbl":
        for tr in el.iter(f"{W}tr"):
            if tr.getparent() is not el:
                continue                               # lignes des tableaux imbriqués : traitées dans la cellule
            cellules = []
            for tc in tr.findall(f"{W}tc"):
                sous = []
                for e in tc:
                    _bloc(e, sous)
                txt = " / ".join(x.strip() for x in sous if x.strip())
                if txt and (not cellules or cellules[-1] != txt):   # cellules fusionnées
                    cellules.append(txt)
            if cellules:
                lignes.append(SEP_CELLULE.join(cellules))
    elif el.tag == f"{W}sdt":
        c = el.find(f"{W}sdtContent")
        for e in (c if c is not None else []):
            _bloc(e, lignes)


def _convertir_docx(chemin):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise LectureErreur("Les fichiers .doc/.odt doivent être enregistrés en .docx (LibreOffice absent du serveur).")
    out = tempfile.mkdtemp(prefix="fiche_")
    subprocess.run([soffice, "--headless", "--convert-to", "docx", "--outdir", out, chemin],
                   capture_output=True, timeout=180)
    res = os.path.join(out, os.path.splitext(os.path.basename(chemin))[0] + ".docx")
    if not os.path.exists(res):
        raise LectureErreur("Conversion en .docx impossible.")
    return res


# --------------------------------------------------------------------------
# PDF et OCR
# --------------------------------------------------------------------------
def _lire_pdf(chemin):
    if not shutil.which("pdftotext"):
        raise LectureErreur("pdftotext absent du serveur (sudo apt install poppler-utils).")
    r = subprocess.run(["pdftotext", "-layout", chemin, "-"], capture_output=True, timeout=300)
    pages = r.stdout.decode("utf-8", "replace").split("\f")
    pages = [p for p in pages if p is not None]
    a_ocr = [i for i, p in enumerate(pages) if len(p.strip()) < MIN_CAR_PAGE]
    if pages and pages[-1].strip() == "" and len(pages) > 1:
        a_ocr = [i for i in a_ocr if i != len(pages) - 1]
        pages = pages[:-1]
    if not a_ocr:
        return _nettoyer("\n".join(pages)), "PDF (couche texte)", None
    if not ocr_disponible():
        if len(a_ocr) == len(pages):
            raise LectureErreur("PDF scanné : l'OCR (Tesseract) n'est pas installé sur le serveur.")
        return _nettoyer("\n".join(pages)), "PDF (couche texte, pages scannées ignorées)", None
    confs = []
    with tempfile.TemporaryDirectory(prefix="fiche_") as d:
        for i in a_ocr:
            subprocess.run(["pdftoppm", "-r", "300", "-f", str(i + 1), "-l", str(i + 1), "-png",
                            chemin, os.path.join(d, "p")], capture_output=True, timeout=300)
            img = next((os.path.join(d, f) for f in sorted(os.listdir(d)) if f.startswith("p")), None)
            if img:
                t, c = _ocr_image(img)
                pages[i] = t
                if c is not None:
                    confs.append(c)
                os.remove(img)
    methode = "OCR (PDF scanné)" if len(a_ocr) == len(pages) else "PDF (texte + OCR des pages scannées)"
    return (_fusionner_tableaux(_nettoyer("\n".join(pages))), methode,
            (sum(confs) / len(confs) if confs else None))


def _fusionner_tableaux(texte):
    """Un tableau des ennemis réparti sur plusieurs pages scannées → un seul tableau."""
    rx = re.compile(r"RAVAGEURS ET MALADIES\s*⟂\s*TRAITEMENTS \(tableau reconstruit[^\n]*\n(.*?)\nFIN DU TABLEAU\n?", re.S)
    blocs = list(rx.finditer(texte))
    if len(blocs) < 2:
        return texte
    lignes = "\n".join(b.group(1) for b in blocs)
    entete = texte[blocs[0].start():blocs[0].start() + texte[blocs[0].start():].index("\n") + 1]
    reste = rx.sub("", texte)
    pos = blocs[0].start()
    return reste[:pos] + entete + lignes + "\nFIN DU TABLEAU\n" + reste[pos:]


def _ocr_image(chemin):
    if not ocr_disponible():
        raise LectureErreur("L'OCR (Tesseract) n'est pas installé sur le serveur : "
                            "sudo apt install tesseract-ocr tesseract-ocr-fra")
    with tempfile.TemporaryDirectory(prefix="ocr_") as d:
        src = _preparer_image(chemin, d)
        langue = "fra" if "fra" in _langues() else "eng"
        base = os.path.join(d, "out")
        r = subprocess.run(["tesseract", src, base, "-l", langue, "--psm", "3", "txt", "tsv"],
                           capture_output=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(base + ".txt"):
            raise LectureErreur("Échec de l'OCR : " + r.stderr.decode("utf-8", "replace")[-200:])
        texte = open(base + ".txt", encoding="utf-8").read()
        confs, mots = [], []
        try:
            for ligne in open(base + ".tsv", encoding="utf-8").read().splitlines()[1:]:
                c = ligne.split("\t")
                if len(c) >= 12 and c[11].strip() and float(c[10]) >= 0:
                    confs.append(float(c[10]))
                    mots.append({"cle": (c[2], c[3], c[4]), "x": int(c[6]), "y": int(c[7]),
                                 "w": int(c[8]), "h": int(c[9]), "t": c[11]})
        except (ValueError, IndexError):
            pass
    tableau = _tableau_ennemis(mots)
    if tableau:
        texte = tableau + "\n" + texte
    return _nettoyer(texte), (sum(confs) / len(confs) if confs else None)


# --------------------------------------------------------------------------
# Tableau des ennemis sur une page scannée : le nom (colonne de gauche) est centré
# verticalement dans sa ligne de tableau, les traitements (colonne de droite) occupent toute
# la hauteur. L'OCR les lit dans le désordre ; on les réassocie par position.
# --------------------------------------------------------------------------
ENNEMIS_CLES = ("chenille", "thrips", "puceron", "mouche", "aleurode", "acarien", "nematode", "mineuse",
                "tuta", "helicoverpa", "spodoptera", "foreur", "criquet", "termite", "cochenille",
                "punaise", "alternariose", "mildiou", "oidium", "fusariose", "fonte", "pythium",
                "racines roses", "rouille", "bacteriose", "virose", "mosaique", "fletrissement",
                "pourriture", "cercosporiose", "anthracnose", "mauvaises herbes", "adventice", "striga")


def _sans_acc(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def _tableau_ennemis(mots):
    if not mots:
        return None
    lignes = {}
    for m in mots:
        lignes.setdefault(m["cle"], []).append(m)
    largeur = max(m["x"] + m["w"] for m in mots)
    segments = []                       # une ligne OCR qui traverse le tableau est coupée aux grands blancs
    for ms in lignes.values():
        ms = sorted(ms, key=lambda m: m["x"])
        seg = [ms[0]]
        for m in ms[1:]:
            if m["x"] - (seg[-1]["x"] + seg[-1]["w"]) > largeur * 0.06:
                segments.append(seg)
                seg = []
            seg.append(m)
        segments.append(seg)
    L = []
    for ms in segments:
        x0 = min(m["x"] for m in ms); y0 = min(m["y"] for m in ms)
        x1 = max(m["x"] + m["w"] for m in ms); y1 = max(m["y"] + m["h"] for m in ms)
        L.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "yc": (y0 + y1) / 2,
                  "t": " ".join(m["t"] for m in sorted(ms, key=lambda m: m["x"]))})
    # en-tête du tableau (absent sur une page de suite : colonne des traitements à 55 % de la largeur)
    cles_entete = ("TRAITEMENT", "RECOMMANDATION", "ILLUSTRATION", "RAVAGEURS", "MATIÈRE", "MATIERE", "ACTIVE)")
    entete = [l for l in L if any(k in l["t"] for k in cles_entete)]          # titres en capitales
    y_entete = max((l["y1"] for l in entete), default=0)
    x_trait = next((m["x"] for m in mots if "TRAITEMENT" in m["t"]), largeur * 0.55) - 15
    gauche = sorted((l for l in L if l["x0"] < largeur * 0.4 and l["x1"] < largeur * 0.45 and l["y0"] > y_entete),
                    key=lambda l: l["y0"])
    ancres = []
    for l in gauche:
        n = _sans_acc(l["t"])
        est_ennemi = any(k in n for k in ENNEMIS_CLES)
        if ancres and ancres[-1]["t"].rstrip().endswith(":") and l["y0"] - ancres[-1]["y1"] < 1.6 * (l["y1"] - l["y0"]):
            a = ancres[-1]
            a["t"] = a["t"] + " / " + l["t"]
            a["y1"] = l["y1"]; a["yc"] = (a["y0"] + a["y1"]) / 2
        elif est_ennemi:
            ancres.append(dict(l))
    if len(ancres) < 2:
        return None
    droite = [l for l in L if l["x0"] >= x_trait and l["y0"] > y_entete]
    # Limite entre deux lignes du tableau : le plus grand blanc vertical entre les traitements situés
    # entre les centres des deux ennemis (en comptant la ligne juste avant et juste après).
    droite.sort(key=lambda l: l["yc"])
    ys = [l["yc"] for l in droite]
    limites = []
    for a, b in zip(ancres, ancres[1:]):
        seq = [y for y in ys if a["yc"] < y < b["yc"]]
        avant = [y for y in ys if y <= a["yc"]]
        apres = [y for y in ys if y >= b["yc"]]
        seq = avant[-1:] + seq + apres[:1]
        if len(seq) >= 2:
            g = max(range(len(seq) - 1), key=lambda k: seq[k + 1] - seq[k])
            limites.append((seq[g] + seq[g + 1]) / 2)
        else:
            limites.append((a["yc"] + b["yc"]) / 2)
    rangs = {i: [] for i in range(len(ancres))}
    for l in droite:
        i = sum(1 for lim in limites if l["yc"] > lim)
        rangs[i].append(l["t"])
    out = ["RAVAGEURS ET MALADIES" + SEP_CELLULE + "TRAITEMENTS (tableau reconstruit depuis le scan)"]
    for i, a in enumerate(ancres):
        out.append(a["t"].strip() + SEP_CELLULE + re.sub(r"(\w)- (\w)", r"\1-\2", " ".join(rangs[i])))
    out.append("FIN DU TABLEAU")
    return "\n".join(out)


def _preparer_image(chemin, d):
    """Niveaux de gris et agrandissement des petites images (photos de téléphone, scans 150 dpi)."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(chemin)
        im = ImageOps.exif_transpose(im).convert("L")
        if im.width < 2000:
            f = 2000 / im.width
            im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
        out = os.path.join(d, "src.png")
        im.save(out)
        return out
    except Exception:
        return chemin


_LANGUES = None


def _langues():
    global _LANGUES
    if _LANGUES is None:
        r = subprocess.run(["tesseract", "--list-langs"], capture_output=True)
        _LANGUES = set((r.stdout + r.stderr).decode("utf-8", "replace").split())
    return _LANGUES


def _nettoyer(t):
    t = t.replace(" ", " ").replace("’", "'").replace("‘", "'").replace("­", "")
    t = re.sub(r"[ \t]+", " ", t)
    lignes = [x.strip() for x in t.split("\n")]
    out = []
    for x in lignes:
        if x or (out and out[-1]):
            out.append(x)
    return "\n".join(out).strip()
