#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/grid_monitor}"
DB_PATH="${DB_PATH:-${APP_DIR}/instance/grid_monitor.sqlite3}"
BACKUP_DIR="${BACKUP_DIR:-/opt/grid_monitor/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/grid_monitor_${TIMESTAMP}.sqlite3"

mkdir -p "${BACKUP_DIR}"

if [[ ! -f "${DB_PATH}" ]]; then
  echo "SQLite database not found: ${DB_PATH}" >&2
  exit 1
fi

sqlite3 "${DB_PATH}" ".backup '${BACKUP_FILE}'"
gzip "${BACKUP_FILE}"

find "${BACKUP_DIR}" -type f -name "grid_monitor_*.sqlite3.gz" -mtime +14 -delete

echo "Backup created: ${BACKUP_FILE}.gz"
