from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read_text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_requirements_include_gunicorn():
    requirements = read_text("requirements.txt")

    assert "gunicorn" in requirements


def test_systemd_example_uses_single_worker_gunicorn_and_env_file():
    service_path = ROOT / "deploy/systemd/grid-monitor.service.example"
    service = service_path.read_text(encoding="utf-8")

    assert service_path.exists()
    assert "WorkingDirectory=/opt/grid_monitor" in service
    assert "User=ubuntu" in service
    assert "opc" in service
    assert "EnvironmentFile=/opt/grid_monitor/.env" in service
    assert "gunicorn" in service
    assert "-c gunicorn.conf.py main:app" in service
    assert "Restart=always" in service
    assert "RestartSec=5" in service


def test_alicloud_systemd_service_uses_project_paths_and_gunicorn_config():
    service_path = ROOT / "deploy/systemd/grid-monitor.service"
    service = service_path.read_text(encoding="utf-8")

    assert service_path.exists()
    assert "WorkingDirectory=/opt/grid_monitor" in service
    assert "User=ubuntu" in service
    assert "EnvironmentFile=/opt/grid_monitor/.env" in service
    assert "ExecStart=/opt/grid_monitor/.venv/bin/gunicorn -c gunicorn.conf.py main:app" in service
    assert "Restart=always" in service
    assert "RestartSec=5" in service
    assert "IFIND_REFRESH_TOKEN" not in service
    assert "FEISHU_WEBHOOK_URL" not in service


def test_caddy_example_proxies_to_local_gunicorn():
    caddy = read_text("deploy/caddy/Caddyfile.example")

    assert "domain.com" in caddy
    assert "reverse_proxy 127.0.0.1:8000" in caddy
    assert "HTTPS" in caddy


def test_alicloud_caddy_example_listens_on_80_and_proxies_locally():
    caddy = read_text("deploy/caddy/Caddyfile.aliyun-http.example")

    assert ":80" in caddy
    assert "reverse_proxy 127.0.0.1:8000" in caddy
    assert "8000" in caddy
    assert "FEISHU_WEBHOOK_URL" not in caddy
    assert "IFIND_REFRESH_TOKEN" not in caddy


def test_deployment_status_check_script_has_safe_commands():
    script_path = ROOT / "scripts/check_deployment_status.sh"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.exists()
    assert "set -euo pipefail" in script
    assert "systemctl status grid-monitor --no-pager" in script
    assert "journalctl -u grid-monitor -n 100 --no-pager" in script
    assert "curl -f http://127.0.0.1:8000/healthz" in script
    assert "curl -f http://127.0.0.1/healthz" in script
    assert "IFIND_REFRESH_TOKEN" not in script
    assert "FEISHU_WEBHOOK_URL" not in script
    assert "Bearer " not in script


def test_sqlite_backup_script_is_safe_and_retains_recent_backups():
    backup = read_text("scripts/backup_sqlite.sh")

    assert "set -euo pipefail" in backup
    assert "/opt/grid_monitor/backups" in backup
    assert "date +%Y%m%d_%H%M%S" in backup
    assert "sqlite3" in backup
    assert ".backup" in backup
    assert "-mtime +14 -delete" in backup
    assert "/Users/" not in backup


def test_production_smoke_check_script_is_local_and_safe():
    script_path = ROOT / "scripts/production_smoke_check.sh"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.exists()
    assert "set -euo pipefail" in script
    assert "main.py" in script
    assert "requirements.txt" in script
    assert ".env.example" in script
    assert "deploy/systemd/grid-monitor.service.example" in script
    assert "deploy/systemd/grid-monitor.service" in script
    assert "deploy/caddy/Caddyfile.example" in script
    assert "deploy/caddy/Caddyfile.aliyun-http.example" in script
    assert "docs/DEPLOYMENT.md" in script
    assert "scripts/backup_sqlite.sh" in script
    assert "scripts/check_deployment_status.sh" in script
    assert "gunicorn" in script
    assert "gunicorn.conf.py" in script
    assert "workers = 1" in script
    assert "reverse_proxy 127.0.0.1:8000" in script
    assert "curl -f http://127.0.0.1:8000/healthz" in script
    assert "Do not commit .env" in script
    assert "Do not expose ports 5001 or 8000" in script
    assert "curl https://" not in script
    assert "curl http://open" not in script
    assert "ssh " not in script
    assert "IFIND_REFRESH_TOKEN" not in script
    assert "IFIND_ACCESS_TOKEN" not in script
    assert "FEISHU_WEBHOOK_URL" not in script
    assert "open-apis/bot/v2/hook/" not in script
    assert "Bearer " not in script


def test_gunicorn_config_is_single_worker_for_combined_web_worker():
    config_path = ROOT / "gunicorn.conf.py"
    config = config_path.read_text(encoding="utf-8")

    assert config_path.exists()
    assert "workers = 1" in config
    assert "GUNICORN_BIND" in config
    assert "127.0.0.1:8000" in config
    assert "More workers may duplicate" in config


def test_local_production_rehearsal_script_checks_required_items_safely():
    script_path = ROOT / "scripts/local_production_rehearsal.sh"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.exists()
    assert "set -euo pipefail" in script
    assert "python -m compileall app main.py tests" in script
    assert "python -m pytest -q" in script
    assert "require_command \"gunicorn\"" in script
    assert "require_file \".env\"" in script
    assert "FLASK_SECRET_KEY" in script
    assert "DASHBOARD_AUTH_ENABLED" in script
    assert "MARKET_DATA_MODE" in script
    assert "NOTIFICATION_MODE" in script
    assert "MARKET_DATA_MODE=manual_only NOTIFICATION_MODE=dry_run gunicorn -c gunicorn.conf.py main:app" in script
    assert "MARKET_DATA_MODE=ifind NOTIFICATION_MODE=feishu gunicorn -c gunicorn.conf.py main:app" in script
    assert "http://127.0.0.1:8000" in script
    assert "Do not commit .env" in script
    assert "IFIND_REFRESH_TOKEN" not in script
    assert "IFIND_ACCESS_TOKEN" not in script
    assert "FEISHU_WEBHOOK_URL" not in script
    assert "open-apis/bot/v2/hook/" not in script
    assert "Bearer " not in script


def test_deployment_doc_covers_cloud_runbook():
    deployment = read_text("docs/DEPLOYMENT.md")

    for term in [
        "Ubuntu 22.04",
        "Ubuntu 24.04",
        "Oracle Cloud",
        "阿里云香港 Ubuntu 22.04 ECS",
        "ARM",
        "gunicorn",
        "systemd",
        "workers 1",
        "workers=1",
        ".env",
        "backup",
        "Caddy",
        ":80",
        "127.0.0.1:8000",
        "/opt/grid_monitor",
        "/opt/grid_monitor/.venv",
        "gunicorn -c gunicorn.conf.py main:app",
        "grid-monitor.service",
        "EnvironmentFile=/opt/grid_monitor/.env",
        "curl -f http://127.0.0.1:8000/healthz",
        "curl -f http://127.0.0.1/healthz",
        "systemctl status grid-monitor --no-pager",
        "journalctl -u grid-monitor -n 100 --no-pager",
        "不要开放",
        "5001",
        "8000",
        "journalctl",
        "部署前本地自检",
        "production_smoke_check.sh",
        "本地生产演练",
        "local_production_rehearsal.sh",
        "MARKET_DATA_MODE=manual_only NOTIFICATION_MODE=dry_run gunicorn -c gunicorn.conf.py main:app",
        "MARKET_DATA_MODE=ifind NOTIFICATION_MODE=feishu gunicorn -c gunicorn.conf.py main:app",
        "Ctrl+C",
        ".env 不可提交",
        "阶段 41.3：真实模式本地生产演练",
        "http://127.0.0.1:8000/healthz",
        "10-30 分钟",
        "DASHBOARD_AUTH_ENABLED=true",
        "FLASK_SECRET_KEY",
        "只启用 1-2 个合约",
        "没有重复报警刷屏",
        "不要把 `.env` 提交到 Git",
        "Auto monitor worker started.",
        "Monitor tick started",
        "Monitor tick finished",
        "等待轮询",
        "立即刷新",
        "运维设置 / iFind 授权设置",
        "不会回显 token 原文",
        "测试 iFind 行情",
        "08:50-11:35",
        "13:20-15:05",
        "午间休盘",
        "10:15-10:30",
    ]:
        assert term in deployment


def test_env_example_has_safe_public_defaults_without_real_secrets():
    env_example = read_text(".env.example")

    for term in [
        "FLASK_ENV=development",
        "FLASK_DEBUG=0",
        "FLASK_SECRET_KEY=YOUR_SECRET_KEY",
        "DASHBOARD_AUTH_ENABLED=false",
        "DASHBOARD_USERNAME=admin",
        "DASHBOARD_PASSWORD=YOUR_DASHBOARD_PASSWORD",
        "MARKET_DATA_MODE=manual_only",
        "NOTIFICATION_MODE=dry_run",
        "IFIND_CLIENT_ID=YOUR_IFIND_CLIENT_ID",
        "IFIND_REFRESH_TOKEN=YOUR_IFIND_REFRESH_TOKEN",
        "FEISHU_WEBHOOK_URL=YOUR_FEISHU_WEBHOOK_URL",
        "IFIND_BATCH_SIZE=5",
    ]:
        assert term in env_example

    assert "open-apis/bot/v2/hook/" not in env_example
    assert "Bearer " not in env_example
    assert "sk-" not in env_example
    assert "actual-secret" not in env_example
    assert "YOUR_TOKEN_PLACEHOLDER" not in env_example

def test_deployment_doc_mentions_ifind_refresh_token_config_mapping():
    deployment = read_text("docs/DEPLOYMENT.md")

    assert 'os.getenv("IFIND_REFRESH_TOKEN")' in deployment
    assert 'app.config.get("IFIND_REFRESH_TOKEN")' in deployment
    assert "Flask `app.config`" in deployment
    assert "refresh token" in deployment


def test_readme_links_to_deployment_doc():
    readme = read_text("README.md")

    assert "docs/DEPLOYMENT.md" in readme
    assert "gunicorn" in readme
    assert ".env" in readme
    assert "workers=1" in readme
