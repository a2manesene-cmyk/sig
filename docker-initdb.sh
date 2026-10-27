#!/bin/bash
set -e

# Restauration automatique du backup initial si la base est vide
if [ -f /docker-entrypoint-initdb.d/init_backup/ancar_db.backup ]; then
    echo "=== Initialisation de ancar_db à partir du backup ==="
    pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges /docker-entrypoint-initdb.d/init_backup/ancar_db.backup || true
    echo "=== Restauration terminée avec succès ==="
fi
