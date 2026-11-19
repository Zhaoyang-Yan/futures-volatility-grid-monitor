from datetime import timedelta

from flask import Flask

from app.config import apply_ifind_env_config, get_config, positive_int
from app.db import ensure_app_settings_table, ensure_notification_delivery_table, init_app as init_db_app
from app.monitor.burst import parse_burst_config
from app.monitor.status import configure_auto_monitoring, reset_monitor_status, update_monitor_status
from app.monitor_worker import start_monitor_worker
from app.routes.dashboard import dashboard_bp
from app.template_filters import register_template_filters


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    apply_ifind_env_config(app.config)
    remember_days = positive_int(app.config.get("DASHBOARD_REMEMBER_ME_DAYS", 7), 7)
    app.config["DASHBOARD_REMEMBER_ME_DAYS"] = remember_days
    app.permanent_session_lifetime = timedelta(days=remember_days)
    reset_monitor_status()
    burst_config = parse_burst_config(app.config)
    update_monitor_status(
        notification_mode=app.config["NOTIFICATION_MODE"],
        notification_burst_enabled=burst_config.enabled,
        last_burst_alert_window_seconds=burst_config.window_seconds,
    )
    configure_auto_monitoring(app.config["AUTO_MONITORING_ENABLED"])

    init_db_app(app)
    ensure_app_settings_table(app)
    ensure_notification_delivery_table(app)
    register_template_filters(app)
    app.register_blueprint(dashboard_bp)
    start_monitor_worker(app)

    return app
