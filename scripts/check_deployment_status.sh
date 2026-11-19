#!/usr/bin/env bash
set -euo pipefail

echo "==> grid-monitor systemd status"
systemctl status grid-monitor --no-pager

echo
echo "==> recent grid-monitor logs"
journalctl -u grid-monitor -n 100 --no-pager

echo
echo "==> local Gunicorn health check"
curl -f http://127.0.0.1:8000/healthz

echo
echo "==> local Caddy reverse proxy health check"
curl -f http://127.0.0.1/healthz
