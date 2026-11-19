import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def env_positive_int(name: str, default: int) -> int:
    return positive_int(os.environ.get(name, str(default)), default)


def env_retry_delays(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        values = tuple(int(value.strip()) for value in raw_value.split(",") if value.strip())
    except ValueError:
        return default
    return values if values and all(value > 0 for value in values) else default


IFIND_ENV_STRING_KEYS = (
    "IFIND_REFRESH_TOKEN",
    "IFIND_ACCESS_TOKEN",
    "IFIND_USERNAME",
    "IFIND_PASSWORD",
    "IFIND_ACCOUNT_EXPIRE_DATE",
    "IFIND_TOKEN_RENEW_DAY",
)

IFIND_ENV_INT_KEYS = {
    "IFIND_BATCH_SIZE": 5,
}


def apply_ifind_env_config(config: dict) -> None:
    """Refresh iFind-related config from the live process environment."""
    for key in IFIND_ENV_STRING_KEYS:
        if key in os.environ:
            config[key] = os.environ.get(key, "")
    for key, default in IFIND_ENV_INT_KEYS.items():
        if key in os.environ:
            config[key] = env_int(key, default)


class BaseConfig:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY", "dev-secret-key")
    ENV_FILE_PATH = BASE_DIR / os.environ.get("ENV_FILE_PATH", ".env")
    DATABASE_PATH = BASE_DIR / os.environ.get("DATABASE_PATH", "instance/grid_monitor.sqlite3")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", f"sqlite:///{DATABASE_PATH}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {
            "check_same_thread": False,
            "timeout": 30,
        },
        "pool_pre_ping": True,
    }
    MONITOR_INTERVAL_SECONDS = int(os.environ.get("MONITOR_INTERVAL_SECONDS", "30"))
    MARKET_DATA_MODE = os.environ.get("MARKET_DATA_MODE", "manual_only").strip().lower()
    AUTO_MONITORING_ENABLED = env_bool("AUTO_MONITORING_ENABLED", True)
    IFIND_BASE_URL = os.environ.get("IFIND_BASE_URL", "https://quantapi.51ifind.com/api/v1")
    IFIND_ACCESS_TOKEN = os.environ.get("IFIND_ACCESS_TOKEN", "")
    IFIND_REFRESH_TOKEN = os.environ.get("IFIND_REFRESH_TOKEN", "")
    IFIND_USERNAME = os.environ.get("IFIND_USERNAME", "")
    IFIND_PASSWORD = os.environ.get("IFIND_PASSWORD", "")
    IFIND_ACCOUNT_EXPIRE_DATE = os.environ.get("IFIND_ACCOUNT_EXPIRE_DATE", "")
    IFIND_TOKEN_RENEW_DAY = os.environ.get("IFIND_TOKEN_RENEW_DAY", "")
    IFIND_TOKEN_REFRESH_MARGIN_SECONDS = int(os.environ.get("IFIND_TOKEN_REFRESH_MARGIN_SECONDS", "3600"))
    IFIND_REALTIME_ENDPOINT = os.environ.get("IFIND_REALTIME_ENDPOINT", "/real_time_quotation")
    IFIND_REALTIME_INDICATORS = os.environ.get("IFIND_REALTIME_INDICATORS", "latest")
    IFIND_REQUEST_TIMEOUT_SECONDS = env_int("IFIND_REQUEST_TIMEOUT_SECONDS", 10)
    IFIND_BATCH_SIZE = env_int("IFIND_BATCH_SIZE", 5)
    IFIND_CODE_SUFFIX = os.environ.get("IFIND_CODE_SUFFIX", "")
    IFIND_CODE_MAP = os.environ.get("IFIND_CODE_MAP", "")
    IFIND_EXCHANGE_SUFFIX_MAP = os.environ.get("IFIND_EXCHANGE_SUFFIX_MAP", "")
    IFIND_PERMISSION_RENEWAL_DAY = os.environ.get("IFIND_PERMISSION_RENEWAL_DAY", "22")
    IFIND_PERMISSION_REMINDER_DAYS = os.environ.get("IFIND_PERMISSION_REMINDER_DAYS", "3")
    IFIND_PERMISSION_GRACE_DAYS = os.environ.get("IFIND_PERMISSION_GRACE_DAYS", "2")
    IFIND_PERMISSION_TIMEZONE = os.environ.get("IFIND_PERMISSION_TIMEZONE", "Asia/Shanghai")
    NOTIFICATION_MODE = os.environ.get("NOTIFICATION_MODE", "dry_run").strip().lower()
    NOTIFICATION_BURST_ENABLED = os.environ.get("NOTIFICATION_BURST_ENABLED", "true")
    NOTIFICATION_BURST_WINDOW_SECONDS = os.environ.get("NOTIFICATION_BURST_WINDOW_SECONDS", "300")
    NOTIFICATION_BURST_THRESHOLD = os.environ.get("NOTIFICATION_BURST_THRESHOLD", "3")
    FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
    FEISHU_WEBHOOK_SECRET = os.environ.get("FEISHU_WEBHOOK_SECRET", "")
    FEISHU_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("FEISHU_REQUEST_TIMEOUT_SECONDS", "10"))
    FEISHU_RETRY_ENABLED = env_bool("FEISHU_RETRY_ENABLED", True)
    FEISHU_MAX_ATTEMPTS = env_positive_int("FEISHU_MAX_ATTEMPTS", 4)
    FEISHU_RETRY_DELAYS_SECONDS = env_retry_delays("FEISHU_RETRY_DELAYS_SECONDS", (30, 60, 120))
    FEISHU_RETRY_BATCH_SIZE = env_positive_int("FEISHU_RETRY_BATCH_SIZE", 20)
    NOTIFICATION_DELIVERY_ROLLOUT_AT = os.environ.get("NOTIFICATION_DELIVERY_ROLLOUT_AT", "")
    WECOM_WEBHOOK_URL = os.environ.get("WECOM_WEBHOOK_URL", "")
    DASHBOARD_AUTH_ENABLED = env_bool("DASHBOARD_AUTH_ENABLED", False)
    DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "")
    DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
    DASHBOARD_REMEMBER_ME_DAYS = env_positive_int("DASHBOARD_REMEMBER_ME_DAYS", 7)
    DEBUG = False
    TESTING = False


class DevelopmentConfig(BaseConfig):
    DEBUG = False


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DASHBOARD_AUTH_ENABLED = False
    DASHBOARD_USERNAME = ""
    DASHBOARD_PASSWORD="YOUR_DASHBOARD_PASSWORD"


class ProductionConfig(BaseConfig):
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY")


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(config_name: str | None = None) -> type[BaseConfig]:
    selected = config_name or os.environ.get("FLASK_ENV", "development")
    config = CONFIG_MAP.get(selected, DevelopmentConfig)
    if config is ProductionConfig and not config.SECRET_KEY:
        raise RuntimeError("SECRET_KEY must be set when FLASK_ENV=production.")
    return config
