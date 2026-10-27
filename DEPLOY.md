# Guide de Déploiement — Plateforme ANCAR sur VPS Hetzner

Ce guide détaille pas-à-pas la méthode recommandée pour déployer l'application sur un serveur **Hetzner Cloud** (Ubuntu 22.04 ou 24.04 LTS) avec **Docker Compose**, un reverse-proxy **Nginx** et un certificat SSL **Let's Encrypt (HTTPS)**.

---

## 1. Création du VPS Hetzner

1. Connectez-vous à la [Console Hetzner Cloud](https://console.hetzner.cloud/).
2. Créez un nouveau projet, puis cliquez sur **Add Server** :
   - **Localisation** : Falkenstein, Nuremberg ou Helsinki (selon votre préférence).
   - **Image** : **Ubuntu 24.04** (ou 22.04 LTS).
   - **Type** : Type standard x86 (ex: **CX22** 2 vCPU / 4 Go RAM ou **CPX21** 3 vCPU / 4 Go RAM suffisent largement).
   - **Clé SSH** : Ajoutez votre clé publique SSH pour vous connecter en toute sécurité.
   - **Firewalls (Pare-feu Hetzner)** : Ouvrez uniquement les ports :
     - `22` (SSH)
     - `80` (HTTP)
     - `443` (HTTPS)
     *(Ne jamais exposer le port 5432 de PostgreSQL vers l'extérieur).*

---

## 2. Préparation du serveur

Connectez-vous en SSH à votre serveur :
```bash
ssh root@<IP_DE_VOTRE_SERVEUR>
```

Mettez à jour les paquets système et installez Docker, Nginx et Certbot :
```bash
apt update && apt upgrade -y
apt install -y git curl ufw nginx certbot python3-certbot-nginx

# Installation officielle de Docker et Docker Compose
curl -fsSL https://get.docker.com | sh
```

Activez le pare-feu local UFW :
```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
```

---

## 3. Déploiement de l'application

### A. Cloner le dépôt
```bash
git clone https://github.com/a2manesene-cmyk/sig.git /var/www/sig
cd /var/www/sig
```

### B. Configurer les variables d'environnement
Créez le fichier `.env` en copiant l'exemple :
```bash
cp .env.example .env
nano .env
```
Renseignez des valeurs sécurisées :
```ini
SECRET_KEY=generer_une_longue_chaine_aleatoire_tres_securisee
DB_USER=ancar
DB_PASSWORD=choisir_un_mot_de_passe_robuste_ici
DB_NAME=ancar_db
GEE_PROJECT=ancarsi-f07fd
```
*(Sauvegardez avec `Ctrl+O` puis quittez avec `Ctrl+X`).*

### C. Démarrer les conteneurs Docker
Lancez simplement Docker Compose :
```bash
docker compose up -d --build
```

> **Restauration automatique** : Le conteneur PostGIS charge automatiquement le dump complet de la base de données (`ancar_db.backup`) lors de sa première initialisation grâce au script `docker-initdb.sh`.

Vérifiez que les 2 conteneurs tournent et que les données sont bien présentes :
```bash
docker compose ps
docker compose exec web python -c "from app.models.parcelle import Parcelle; from app import create_app; app = create_app();
with app.app_context():
    print('Parcelles en base :', Parcelle.query.count())
"
```

---

## 4. Configuration de Nginx et HTTPS (SSL)

### A. Configurer le Reverse Proxy Nginx
Créez le fichier de configuration de votre site :
```bash
nano /etc/nginx/sites-available/sig
```

Collez la configuration suivante (remplacez `votre-domaine.com` par votre vrai nom de domaine ou l'adresse IP si temporaire) :
```nginx
server {
    listen 80;
    server_name votre-domaine.com www.votre-domaine.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Activez le site et testez la syntaxe :
```bash
ln -s /etc/nginx/sites-available/sig /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### B. Installer le certificat SSL Let's Encrypt (HTTPS gratuit)
```bash
certbot --nginx -d votre-domaine.com -d www.votre-domaine.com
```
Certbot va configurer automatiquement le certificat SSL et son renouvellement automatique.

---

## 5. Maintenance courante

- **Voir les logs de l'application en direct** :
  ```bash
  docker compose logs -f web
  ```
- **Redémarrer les services** :
  ```bash
  docker compose restart
  ```
- **Mettre à jour l'application après un `git pull`** :
  ```bash
  cd /var/www/sig
  git pull
  docker compose up -d --build web
  ```
- **Faire une sauvegarde manuelle de la base de données** :
  ```bash
  docker compose exec db pg_dump -U ancar -Fc ancar_db > /root/backup_$(date +%F).dump
  ```
