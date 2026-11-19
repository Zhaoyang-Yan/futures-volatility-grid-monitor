from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine


db = SQLAlchemy()
NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY = "notification_delivery_rollout_started_at"


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MonitorTask(db.Model):
    __tablename__ = "monitor_tasks"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), nullable=False, index=True)
    base_price = db.Column(db.Float, nullable=False)
    threshold = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        index=True,
    )


class AlertLog(db.Model):
    __tablename__ = "alert_logs"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer,
        db.ForeignKey("monitor_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    code = db.Column(db.String(32), nullable=False, index=True)
    base_price = db.Column(db.Float, nullable=False)
    old_base_price = db.Column(db.Float, nullable=False)
    new_base_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, nullable=False)
    diff = db.Column(db.Float, nullable=False)
    threshold = db.Column(db.Float, nullable=False)
    grid_steps = db.Column(db.Integer, nullable=False)
    direction = db.Column(db.String(8), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive, index=True)

    task = db.relationship("MonitorTask", backref=db.backref("alert_logs", lazy=True))


class NotificationDelivery(db.Model):
    """Persistent delivery state for a real alert notification.

    AlertLog remains the source of truth for the grid event.  Keeping delivery
    state separately lets a transient webhook failure be retried without
    recreating the alert or re-running grid logic.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        db.UniqueConstraint("alert_log_id", "channel", name="uq_delivery_alert_channel"),
        db.UniqueConstraint("message_key", name="uq_delivery_message_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    alert_log_id = db.Column(
        db.Integer,
        db.ForeignKey("alert_logs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel = db.Column(db.String(32), nullable=False, default="feishu")
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=4)
    next_retry_at = db.Column(db.DateTime, nullable=True, index=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True, index=True)
    last_error_code = db.Column(db.String(64), nullable=True)
    last_error_message = db.Column(db.String(256), nullable=True)
    source = db.Column(db.String(64), nullable=True)
    message_key = db.Column(db.String(96), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        index=True,
    )

    alert_log = db.relationship(
        "AlertLog",
        backref=db.backref("notification_deliveries", lazy=True, cascade="all, delete-orphan"),
    )


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), nullable=False, unique=True, index=True)
    value = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now_naive)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now_naive,
        onupdate=utc_now_naive,
        index=True,
    )


@event.listens_for(Engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    if dbapi_connection.__class__.__module__ != "sqlite3":
        return

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def init_db() -> None:
    db.drop_all()
    db.create_all()


def ensure_app_settings_table(app) -> None:
    with app.app_context():
        AppSetting.__table__.create(db.engine, checkfirst=True)


def ensure_notification_delivery_table(app) -> None:
    """Add the delivery table for existing SQLite installations safely."""
    with app.app_context():
        NotificationDelivery.__table__.create(db.engine, checkfirst=True)
        if AppSetting.query.filter_by(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY).first() is not None:
            return

        # Prefer evidence already present in the delivery table.  If neither
        # evidence nor an explicit operator setting is available, leave the
        # marker absent so missing records remain visible as an anomaly.
        earliest_delivery = (
            NotificationDelivery.query.order_by(
                NotificationDelivery.created_at.asc(),
                NotificationDelivery.id.asc(),
            ).first()
        )
        rollout_at = (
            _as_utc_naive(earliest_delivery.created_at)
            if earliest_delivery is not None
            else _parse_configured_rollout_at(app.config.get("NOTIFICATION_DELIVERY_ROLLOUT_AT"))
        )
        if rollout_at is not None:
            db.session.add(
                AppSetting(
                    key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY,
                    value=_serialize_utc(rollout_at),
                )
            )
            db.session.commit()


def get_notification_delivery_rollout_started_at():
    setting = AppSetting.query.filter_by(key=NOTIFICATION_DELIVERY_ROLLOUT_AT_KEY).first()
    if setting is None or not setting.value:
        return None
    # The first display-only implementation stored UTC without an offset.
    # Keep that legacy value usable without replacing an operator's setting.
    return _parse_rollout_at(setting.value, assume_naive_utc=True)


def _parse_configured_rollout_at(value: str | None) -> datetime | None:
    """Parse an explicit, timezone-aware operator rollout timestamp."""
    return _parse_rollout_at(value, assume_naive_utc=False)


def _parse_rollout_at(value: str | None, *, assume_naive_utc: bool) -> datetime | None:
    if not value:
        return None
    raw_value = str(value).strip()
    named_timezone = None
    if raw_value.endswith(" Asia/Shanghai"):
        raw_value = raw_value.removesuffix(" Asia/Shanghai").strip()
        named_timezone = ZoneInfo("Asia/Shanghai")
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if named_timezone is not None:
            return _as_utc_naive(parsed.replace(tzinfo=named_timezone))
        return _as_utc_naive(parsed) if assume_naive_utc else None
    return _as_utc_naive(parsed)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _serialize_utc(value: datetime) -> str:
    return _as_utc_naive(value).replace(tzinfo=timezone.utc).isoformat()


@click.command("init-db")
def init_db_command() -> None:
    init_db()
    click.echo("Initialized the database.")


def init_app(app) -> None:
    database_uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if database_uri.startswith("sqlite:///"):
        database_path = database_uri.replace("sqlite:///", "", 1)
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    app.cli.add_command(init_db_command)
