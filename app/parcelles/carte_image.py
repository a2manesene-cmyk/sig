# -*- coding: utf-8 -*-
"""Génère une carte annotée téléchargeable (PNG) pour une parcelle :
image satellite (RGB / NDVI / NDMI) + titre + logo ANCAR + encadré d'infos + légende + échelle."""
import io
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont

VERT = (30, 86, 49)
INK = (31, 42, 36)
MUT = (91, 107, 96)

# Légendes (libellé, couleur) alignées sur l'échelle 5 classes
LEGENDES = {
    "ndvi": [
        ("Sol nu / Échec", (215, 48, 39)),
        ("Végétation critique", (243, 156, 18)),
        ("Retard / À surveiller", (244, 208, 63)),
        ("Bon comportement", (166, 217, 106)),
        ("Excellent développement", (26, 152, 80)),
    ],
    "ndmi": [
        ("Stress hydrique sévère", (222, 235, 247)),
        ("Stress hydrique modéré", (158, 202, 225)),
        ("Humidité intermédiaire", (107, 174, 214)),
        ("Humidité élevée", (49, 130, 189)),
        ("Végétation très humide", (8, 81, 156)),
    ],
    "rgb": [],
}
TITRES = {
    "ndvi": "Situation de la santé des cultures",
    "ndmi": "Carte du stress hydrique de la végétation",
    "rgb": "Image satellite (vraies couleurs)",
}


def _font(size, bold=False):
    noms = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf" if bold else "C:\\Windows\\Fonts\\arial.ttf",
    ]
    for n in noms:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _telecharger_png(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def composer(png_url, indice, meta, logo_path=None):
    """png_url : URL de l'image satellite (GEE). meta : dict d'infos. Retourne bytes PNG."""
    carte = _telecharger_png(png_url)
    # dimensionner la carte à 900 px de large max
    W_CARTE = 900
    ratio = W_CARTE / carte.width
    carte = carte.resize((W_CARTE, int(carte.height * ratio)))

    marge = 24
    haut_titre = 60
    larg_info = 340
    W = marge + W_CARTE + 16 + larg_info + marge
    H = haut_titre + max(carte.height, 520) + marge + 40

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # --- Titre ---
    titre = f"{TITRES.get(indice, '')} — {meta.get('producteur', '')}"
    d.rectangle([0, 0, W, haut_titre], fill=(240, 246, 241))
    d.text((marge, 18), titre, fill=VERT, font=_font(20, True))
    if meta.get("periode"):
        d.text((marge, 42), meta["periode"], fill=MUT, font=_font(13))

    # --- Logo ANCAR ---
    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            lh = 44
            logo = logo.resize((int(logo.width * lh / logo.height), lh))
            img.paste(logo, (W - marge - logo.width, 8), logo)
        except Exception:
            pass

    # --- Carte ---
    x_carte, y_carte = marge, haut_titre + 8
    img.paste(carte, (x_carte, y_carte))
    d.rectangle([x_carte, y_carte, x_carte + carte.width, y_carte + carte.height],
                outline=(120, 120, 120), width=1)

    # --- Encadré d'infos (droite) ---
    xi = x_carte + carte.width + 16
    yi = y_carte
    lignes = [
        ("Date de numérisation", meta.get("date", "—")),
        ("Nom producteur", meta.get("producteur", "—")),
        ("Culture", meta.get("culture", "—")),
        ("Superficie", meta.get("superficie", "—")),
        ("Commune", meta.get("commune", "—")),
        ("Cartographiée par", meta.get("animateur", "—")),
    ]
    fh = 26
    d.rectangle([xi, yi, xi + larg_info, yi + fh * len(lignes)], outline=(120, 120, 120), width=1)
    for i, (k, v) in enumerate(lignes):
        yy = yi + i * fh
        d.rectangle([xi, yy, xi + 150, yy + fh], fill=(240, 246, 241), outline=(200, 200, 200))
        d.rectangle([xi + 150, yy, xi + larg_info, yy + fh], outline=(200, 200, 200))
        d.text((xi + 6, yy + 6), k, fill=INK, font=_font(12, True))
        d.text((xi + 156, yy + 6), str(v), fill=INK, font=_font(12))

    # --- Légende (sous l'encadré) ---
    yl = yi + fh * len(lignes) + 20
    leg = LEGENDES.get(indice, [])
    if leg:
        titre_leg = "Santé des cultures" if indice == "ndvi" else "État du stress hydrique"
        d.text((xi, yl), titre_leg, fill=VERT, font=_font(13, True))
        yl += 22
        for lib, col in leg:
            d.rectangle([xi, yl, xi + 18, yl + 18], fill=col, outline=(120, 120, 120))
            d.text((xi + 26, yl + 2), lib, fill=INK, font=_font(12))
            yl += 24

    # --- Flèche Nord (haut-gauche de la carte) ---
    nx, ny = x_carte + 22, y_carte + 20
    d.polygon([(nx, ny - 14), (nx - 7, ny + 8), (nx, ny + 2), (nx + 7, ny + 8)],
              fill=(255, 255, 255), outline=(0, 0, 0))
    d.text((nx - 4, ny + 10), "N", fill=(255, 255, 255), font=_font(13, True))
    d.text((nx - 5, ny + 9), "N", fill=(0, 0, 0), font=_font(13, True))

    # --- Échelle exacte (m) ---
    yb = y_carte + carte.height + 12
    largeur_m = meta.get("largeur_m")
    if largeur_m and largeur_m > 0:
        m_par_px = largeur_m / W_CARTE          # mètres par pixel de l'image affichée
        cible_px = W_CARTE / 4                    # longueur visée ~ 1/4 de la carte
        brut = cible_px * m_par_px                # longueur brute en mètres
        # arrondir à un « joli » nombre (1,2,5 × 10^n)
        import math
        expo = math.floor(math.log10(brut)) if brut > 0 else 0
        base = brut / (10 ** expo)
        joli = (1 if base < 1.5 else 2 if base < 3.5 else 5 if base < 7.5 else 10) * (10 ** expo)
        barre_px = int(joli / m_par_px)
        d.rectangle([x_carte, yb, x_carte + barre_px, yb + 8], fill=(0, 0, 0), outline=(0, 0, 0))
        d.rectangle([x_carte, yb, x_carte + barre_px // 2, yb + 8], fill=(255, 255, 255), outline=(0, 0, 0))
        d.rectangle([x_carte, yb, x_carte + barre_px, yb + 8], outline=(0, 0, 0))
        d.text((x_carte, yb + 12), "0", fill=INK, font=_font(11))
        lib = f"{int(joli)} m" if joli < 1000 else f"{joli/1000:g} km"
        d.text((x_carte + barre_px - 12, yb + 12), lib, fill=INK, font=_font(11))
    d.text((x_carte + carte.width - 260, yb), "Sources : Sentinel-2 / Earth Engine · ANCAR SIG",
           fill=MUT, font=_font(11))

    out = io.BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out
