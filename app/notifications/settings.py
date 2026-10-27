"""
Paramètres des notifications WhatsApp (Meta Cloud API) et des langues.
À ADAPTER puis conserver hors du dépôt public (contient un jeton).
"""

WHATSAPP = {
    # Jeton d'accès Meta (de préférence permanent, via une app System User)
    "token": "CHANGE_MOI_TOKEN_META",
    # Identifiant du numéro expéditeur (Phone Number ID, tableau de bord Meta)
    "phone_number_id": "CHANGE_MOI_PHONE_NUMBER_ID",
    "api_version": "v21.0",
}

# Indicatif pays par défaut (Sénégal = 221)
INDICATIF_PAYS = "221"

# Langue par zone/région (code parcelle -> code langue). Ex : {"KAOLACK": "wo"}
# Codes langue : "fr" (français), "wo" (wolof), "ff" (pulaar)…
REGION_LANGUE = {
    # "KAOLACK": "wo",
    # "TAMBACOUNDA": "ff",
}
LANGUE_DEFAUT = "fr"

# Envoyer aussi un message texte d'accompagnement (détails parcelles) ?
ENVOYER_TEXTE = True
