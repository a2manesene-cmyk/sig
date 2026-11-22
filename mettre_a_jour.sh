#!/bin/bash
# Mise à jour de la plateforme sur le serveur (Docker), en une commande :
#     cd /var/www/sig && sudo bash mettre_a_jour.sh
# Avec --restaurer-base : remplace AUSSI la base en ligne par ancar_db.backup du dépôt
# (2885 parcelles Grenier du maïs corrigées). La base actuelle est sauvegardée avant.
set -e
cd "$(dirname "$0")"
[ -f .env ] && { set -a; . ./.env; set +a; }
U=${DB_USER:-ancar}; B=${DB_NAME:-ancar_db}
mkdir -p instance sauvegardes

echo "1/4  Sauvegarde de la base actuelle"
SAUVE="sauvegardes/${B}_$(date +%Y%m%d_%H%M).dump"
docker compose exec -T db pg_dump -U "$U" -Fc "$B" > "$SAUVE" && echo "     -> $SAUVE"

echo "2/4  Récupération du nouveau code et reconstruction"
git pull
docker compose build web

if [ "$1" = "--restaurer-base" ]; then
  echo "     Remplacement de la base par ancar_db.backup"
  docker compose stop web
  docker compose exec -T db psql -q -U "$U" -d postgres -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE \"$B\" WITH (FORCE)" -c "CREATE DATABASE \"$B\" OWNER \"$U\""
  docker compose exec -T db pg_restore -U "$U" -d "$B" --no-owner --no-privileges < ancar_db.backup
fi

echo "3/4  Démarrage et mise à jour des tables"
docker compose up -d
sleep 5
for s in init_enquete init_sol init_climat init_referentiel; do
  docker compose exec -T web python "$s.py"
done
docker compose restart web

echo "4/4  Contrôle"
docker compose exec -T db psql -U "$U" -d "$B" -tAc \
  "SELECT 'Parcelles : ' || COUNT(*) || ' - ' || ROUND(SUM(surface_ha)::numeric,1) || ' ha' FROM parcelles"
echo "Terminé. Ancienne base sauvegardée dans $SAUVE"
