#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "==> $*"
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || fail "missing file: ${path}"
}

require_command() {
  local name="$1"
  command -v "${name}" >/dev/null 2>&1 || fail "${name} is not installed or not on PATH"
}

env_has_key() {
  local key="$1"
  grep -Eq "^[[:space:]]*${key}[[:space:]]*=" .env
}

env_value() {
  local key="$1"
  grep -E "^[[:space:]]*${key}[[:space:]]*=" .env | tail -n 1 | cut -d "=" -f 2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

info "Running local production rehearsal checks"

require_file "main.py"
[[ -d "app" ]] || fail "missing directory: app"
require_file "requirements.txt"
require_file "gunicorn.conf.py"
require_file ".env"

grep -q "gunicorn" requirements.txt || fail "requirements.txt must contain gunicorn"
grep -q "workers = 1" gunicorn.conf.py || fail "gunicorn.conf.py must set workers = 1"
grep -q "127.0.0.1:8000" gunicorn.conf.py || fail "gunicorn.conf.py must default to 127.0.0.1:8000"

info "Running compile check"
python -m compileall app main.py tests

info "Running pytest"
python -m pytest -q

info "Checking gunicorn command"
require_command "gunicorn"

info "Checking .env keys without printing secrets"
env_has_key "FLASK_SECRET_KEY" || fail ".env must define FLASK_SECRET_KEY"
env_has_key "DASHBOARD_AUTH_ENABLED" || fail ".env must define DASHBOARD_AUTH_ENABLED"
env_has_key "MARKET_DATA_MODE" || fail ".env must define MARKET_DATA_MODE"
env_has_key "NOTIFICATION_MODE" || fail ".env must define NOTIFICATION_MODE"

MARKET_MODE="$(env_value "MARKET_DATA_MODE")"
NOTIFICATION_MODE_VALUE="$(env_value "NOTIFICATION_MODE")"
DASHBOARD_AUTH_VALUE="$(env_value "DASHBOARD_AUTH_ENABLED")"

echo
echo "Current rehearsal mode summary:"
echo "- MARKET_DATA_MODE=${MARKET_MODE:-<empty>}"
echo "- NOTIFICATION_MODE=${NOTIFICATION_MODE_VALUE:-<empty>}"
echo "- DASHBOARD_AUTH_ENABLED=${DASHBOARD_AUTH_VALUE:-<empty>}"
echo "- FLASK_SECRET_KEY configured: yes"

echo
echo "Next start commands:"
echo "Safe rehearsal:"
echo "  MARKET_DATA_MODE=manual_only NOTIFICATION_MODE=dry_run gunicorn -c gunicorn.conf.py main:app"
echo
echo "Real iFind + Feishu rehearsal:"
echo "  MARKET_DATA_MODE=ifind NOTIFICATION_MODE=feishu gunicorn -c gunicorn.conf.py main:app"
echo
echo "Open: http://127.0.0.1:8000"
echo "Stop: Ctrl+C"
echo
echo "Safety reminders:"
echo "- Do not commit .env."
echo "- Real rehearsal may request iFind and may send Feishu notifications."
echo "- Keep gunicorn workers=1 while Web and Worker are combined."
