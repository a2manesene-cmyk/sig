"""
Génération de l'audio d'une alerte.

- Langues gérées par synthèse vocale (gTTS) : le texte est lu automatiquement.
- Langues locales (wolof, pulaar…) : on utilise un audio PRÉ-ENREGISTRÉ,
  placé dans app/static/audio/<lang>/<type_alerte>.mp3
  (à défaut, on se rabat sur une lecture française).
"""
import os
import tempfile

GTTS_LANGS = {"fr", "en", "es", "pt", "ar"}


def _prereg_path(base_static, lang, type_alerte):
    return os.path.join(base_static, "audio", lang, f"{type_alerte}.mp3")


def generer_audio(texte_fr, texte_local, lang, type_alerte, base_static):
    """
    Retourne (chemin_fichier_mp3, temporaire?).
    texte_fr  : version française (pour la synthèse de repli)
    texte_local : version dans la langue cible (utilisée si gTTS gère la langue)
    """
    # 1) Langue locale avec audio pré-enregistré
    p = _prereg_path(base_static, lang, type_alerte)
    if lang not in GTTS_LANGS and os.path.exists(p):
        return p, False

    # 2) Synthèse vocale (gTTS) si langue supportée, sinon repli français
    from gtts import gTTS  # import tardif : l'app démarre sans gTTS
    if lang in GTTS_LANGS:
        texte, code = (texte_local or texte_fr), lang
    else:
        texte, code = texte_fr, "fr"
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    gTTS(text=texte, lang=code).save(tmp.name)
    return tmp.name, True
