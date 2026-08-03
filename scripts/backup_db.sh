#!/usr/bin/env bash
# Backup de la base DVU. Pensado para cron en el VPS:
#
#   0 3 * * * /srv/dvu/scripts/backup_db.sh >> /var/log/dvu-backup.log 2>&1
#
# Sin Kubernetes no hay operador que respalde por nosotros (ver docs/adr/0002).
# Un backup que nunca se restauró no es un backup: `pg_restore --list` verifica que
# el dump se puede leer antes de dar la corrida por buena.
set -Eeuo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="${DVU_BACKUP_DIR:-$RAIZ/data/backups}"
RETENCION_DIAS="${DVU_BACKUP_RETENCION_DIAS:-14}"
SERVICIO="${DVU_BACKUP_SERVICIO:-db}"

cd "$RAIZ"
mkdir -p "$DESTINO"

# shellcheck disable=SC1091
[[ -f .env ]] && set -a && source .env && set +a

USUARIO="${POSTGRES_USER:-dvu}"
BASE="${POSTGRES_DB:-dvu}"
ARCHIVO="$DESTINO/dvu-$(date +%Y%m%d-%H%M%S).dump"

docker compose exec -T "$SERVICIO" pg_dump -U "$USUARIO" -Fc "$BASE" > "$ARCHIVO"

if ! pg_restore --list "$ARCHIVO" > /dev/null 2>&1; then
  # Si el dump no es legible aquí, se intenta dentro del contenedor (puede no haber
  # cliente de Postgres instalado en el host).
  if ! docker compose exec -T "$SERVICIO" pg_restore --list < "$ARCHIVO" > /dev/null 2>&1; then
    echo "ERROR: el dump $ARCHIVO no es legible; se conserva para diagnóstico" >&2
    exit 1
  fi
fi

find "$DESTINO" -name 'dvu-*.dump' -type f -mtime "+$RETENCION_DIAS" -delete

echo "$(date -Is) OK $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"
