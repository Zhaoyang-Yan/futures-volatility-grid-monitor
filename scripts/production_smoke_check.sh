#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

pass() {
  echo "OK: $*"
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || fail "missing file: ${path}"
  pass "found ${path}"
}

require_dir() {
  local path="$1"
  [[ -d "${path}" ]] || fail "missing directory: ${path}"
  pass "found ${path}/"
}

require_text() {
  local path="$1"
  local pattern="$2"
  local description="$3"
  grep -q -- "${pattern}" "${path}" || fail "${description}"
  pass "${description}"
}

echo "grid_monitor production smoke check"
echo "Checking repository files only. This script does not read .env, start services, or call external APIs."
echo

require_file "main.py"
require_dir "app"
require_file "requirements.txt"
require_file ".env.example"
require_file "gunicorn.conf.py"
require_file "deploy/systemd/grid-monitor.service.example"
require_file "deploy/systemd/grid-monitor.service"
require_file "deploy/caddy/Caddyfile.example"
require_file "deploy/caddy/Caddyfile.aliyun-http.example"
require_file "docs/DEPLOYMENT.md"
require_file "scripts/backup_sqlite.sh"
require_file "scripts/check_deployment_status.sh"

require_text "requirements.txt" "gunicorn" "requirements.txt contains gunicorn"
require_text "gunicorn.conf.py" "workers = 1" "gunicorn.conf.py sets workers=1"
require_text "gunicorn.conf.py" "127.0.0.1:8000" "gunicorn.conf.py binds to 127.0.0.1:8000"
require_text "deploy/systemd/grid-monitor.service.example" "-c gunicorn.conf.py main:app" "systemd example uses gunicorn.conf.py"
require_text "deploy/systemd/grid-monitor.service" "-c gunicorn.conf.py main:app" "systemd service uses gunicorn.conf.py"
require_text "deploy/caddy/Caddyfile.example" "reverse_proxy 127.0.0.1:8000" "Caddy example proxies to 127.0.0.1:8000"
require_text "deploy/caddy/Caddyfile.aliyun-http.example" ":80" "Aliyun Caddy example listens on port 80"
require_text "scripts/check_deployment_status.sh" "curl -f http://127.0.0.1:8000/healthz" "deployment check tests local Gunicorn healthz"

echo
echo "Safety reminders:"
echo "- Before real deployment, copy .env.example to .env and fill local secrets on the server."
echo "- Do not commit .env."
echo "- Do not expose ports 5001 or 8000 to the public internet."
echo "- Production currently recommends gunicorn workers=1 to avoid duplicate background workers."
echo
echo "Production smoke check passed."
