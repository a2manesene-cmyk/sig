"""Client minimal WhatsApp Cloud API (Meta) : upload média + envoi audio/texte."""
import os

BASE = "https://graph.facebook.com"


def normaliser_numero(tel, indicatif="221"):
    if not tel:
        return None
    d = "".join(ch for ch in str(tel) if ch.isdigit())
    if not d:
        return None
    if d.startswith("00"):
        d = d[2:]
    if d.startswith(indicatif):
        return d
    if len(d) == 10 and d[0] == "0":   # 0XXXXXXXXX -> indicatif + 9 chiffres
        return indicatif + d[1:]
    if len(d) == 9:                    # numéro local sénégalais (77xxxxxxx…)
        return indicatif + d
    return d


class WhatsAppClient:
    def __init__(self, token, phone_number_id, api_version="v21.0"):
        self.token = token
        self.pid = phone_number_id
        self.ver = api_version

    def _url(self, chemin):
        return f"{BASE}/{self.ver}/{chemin}"

    def upload_media(self, chemin_fichier, mime="audio/mpeg"):
        import requests
        with open(chemin_fichier, "rb") as f:
            r = requests.post(
                self._url(f"{self.pid}/media"),
                headers={"Authorization": f"Bearer {self.token}"},
                files={"file": (os.path.basename(chemin_fichier), f, mime)},
                data={"messaging_product": "whatsapp", "type": mime},
                timeout=60,
            )
        r.raise_for_status()
        return r.json().get("id")

    def send_audio(self, to, media_id):
        import requests
        r = requests.post(
            self._url(f"{self.pid}/messages"),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "audio", "audio": {"id": media_id}},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    def send_text(self, to, body):
        import requests
        r = requests.post(
            self._url(f"{self.pid}/messages"),
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "text", "text": {"body": body[:1000]}},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
