import csv
import hmac
import io
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func

from app.db import AlertLog, MonitorTask, NotificationDelivery, db, get_notification_delivery_rollout_started_at
from app.monitor.burst import build_burst_context, parse_burst_config
from app.monitor.engine import MonitoringEngine
from app.monitor.ifind_token_manager import reset_ifind_token_managers
from app.monitor.ifind_permission import get_ifind_permission_status
from app.monitor.live_mode import get_live_mode_status
from app.monitor.market_provider import get_market_data_provider
from app.monitor.preflight import get_preflight_checks
from app.monitor.rehearsal import get_rehearsal_status
from app.market.trading_sessions import format_clock
from app.monitor.session_scheduler import (
    apply_auto_session_schedule,
    apply_manual_monitoring_command,
    get_session_schedule_view,
)
from app.monitor.status import get_monitor_status, get_runtime_diagnostics, pause_auto_monitoring, resume_auto_monitoring
from app.notify.notifier import build_alert_notification, get_notification_channel_status, get_notifier
from app.notify.delivery import delivery_display_view, delivery_summary
from app.ops.ifind_env import update_ifind_env_file
from app.settings import (
    AppSettingValidationError,
    MONITOR_POLL_INTERVAL_OPTIONS,
    get_monitor_poll_interval_seconds,
    get_auto_session_scheduler_settings,
    set_auto_session_scheduler_settings,
    set_monitor_poll_interval_seconds,
    validate_monitor_poll_interval_seconds,
)
from app.time_utils import format_china_time as china_time


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.before_request
def require_dashboard_login():
    if not current_app.config.get("DASHBOARD_AUTH_ENABLED"):
        return None

    if request.endpoint in {"dashboard.login", "dashboard.logout"}:
        return None

    if session.get("dashboard_authenticated") is True:
        return None

    return redirect(url_for("dashboard.login", next=_safe_next_url(request.full_path)))


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    if not current_app.config.get("DASHBOARD_AUTH_ENABLED"):
        return redirect(_login_redirect_target())

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        configured_username = current_app.config.get("DASHBOARD_USERNAME", "")
        configured_password = current_app.config.get("DASHBOARD_PASSWORD", "")

        if not configured_username or not configured_password:
            flash("登录配置未完成", "danger")
        elif _credentials_match(username, password, configured_username, configured_password):
            remember_me = request.form.get("remember_me") == "on"
            session.clear()
            session.permanent = remember_me
            session["dashboard_authenticated"] = True
            flash("登录成功", "success")
            return redirect(_login_redirect_target())
        else:
            flash("用户名或密码错误", "danger")

    return render_template("auth/login.html", next_url=_login_redirect_target())


@dashboard_bp.get("/logout")
def logout():
    session.clear()
    flash("已退出登录", "info")
    return redirect(url_for("dashboard.login"))


@dashboard_bp.get("/")
def index():
    tasks = MonitorTask.query.order_by(MonitorTask.updated_at.desc(), MonitorTask.id.desc()).all()
    alert_history = _alert_history_context(request.args)
    alert_logs = alert_history["items"]
    alert_notification_previews = {
        alert.id: _alert_notification_preview(alert, current_app.config) for alert in alert_logs
    }
    alert_deliveries = {
        delivery.alert_log_id: delivery
        for delivery in NotificationDelivery.query.filter(
            NotificationDelivery.alert_log_id.in_([alert.id for alert in alert_logs] or [-1])
        ).all()
    }
    delivery_rollout_started_at = get_notification_delivery_rollout_started_at()
    latest_alert = AlertLog.query.order_by(AlertLog.created_at.desc(), AlertLog.id.desc()).first()
    market_mode = current_app.config["MARKET_DATA_MODE"]
    monitor_status = get_monitor_status()
    ifind_permission_status = get_ifind_permission_status(config=current_app.config)
    notification_channel_status = get_notification_channel_status(
        current_app.config["NOTIFICATION_MODE"],
        current_app.config,
    )
    runtime_diagnostics = _runtime_diagnostics(ifind_permission_status, notification_channel_status)
    enabled_tasks_count = _enabled_tasks_count()
    enabled_tasks = _enabled_tasks()
    preflight_checks = _preflight_checks(
        runtime_diagnostics,
        ifind_permission_status,
        notification_channel_status,
        enabled_tasks_count,
    )
    live_mode_status = get_live_mode_status(
        current_app.config,
        runtime_diagnostics,
        preflight_checks,
        enabled_tasks_count,
        notification_channel_status,
    )
    rehearsal_status = get_rehearsal_status(
        current_app.config,
        runtime_diagnostics,
        enabled_tasks,
        notification_channel_status,
    )
    session_schedule = get_session_schedule_view(tasks)
    return render_template(
        "dashboard/index.html",
        tasks=tasks,
        alert_logs=alert_logs,
        latest_alert=latest_alert,
        alert_history=alert_history,
        alert_notification_previews=alert_notification_previews,
        alert_delivery_views={
            alert.id: delivery_display_view(
                alert,
                alert_deliveries.get(alert.id),
                delivery_rollout_started_at,
            )
            for alert in alert_logs
        },
        notification_delivery_summary=delivery_summary(),
        market_mode_view=_market_mode_view(market_mode),
        monitor_status=monitor_status,
        runtime_diagnostics=runtime_diagnostics,
        task_market_status_view=_task_market_status_view(tasks, runtime_diagnostics),
        market_poll_summary_view=_market_poll_summary_view(enabled_tasks, runtime_diagnostics),
        preflight_checks=preflight_checks,
        preflight_view=_preflight_view(preflight_checks),
        live_mode_status=live_mode_status,
        rehearsal_status=rehearsal_status,
        monitor_status_view=_monitor_status_view(monitor_status),
        ifind_permission_status=ifind_permission_status,
        notification_channel_status=notification_channel_status,
        monitor_poll_interval_seconds=get_monitor_poll_interval_seconds(),
        monitor_poll_interval_options=MONITOR_POLL_INTERVAL_OPTIONS,
        session_schedule=session_schedule,
        ifind_auth_settings=_ifind_auth_settings_view(),
    )


@dashboard_bp.get("/healthz")
def healthz():
    ifind_permission_status = get_ifind_permission_status(config=current_app.config)
    notification_channel_status = get_notification_channel_status(
        current_app.config["NOTIFICATION_MODE"],
        current_app.config,
    )
    runtime_diagnostics = _runtime_diagnostics(ifind_permission_status, notification_channel_status)
    enabled_tasks_count = _enabled_tasks_count()
    enabled_tasks = _enabled_tasks()
    preflight_checks = _preflight_checks(
        runtime_diagnostics,
        ifind_permission_status,
        notification_channel_status,
        enabled_tasks_count,
    )
    live_mode_status = get_live_mode_status(
        current_app.config,
        runtime_diagnostics,
        preflight_checks,
        enabled_tasks_count,
        notification_channel_status,
    )
    rehearsal_status = get_rehearsal_status(
        current_app.config,
        runtime_diagnostics,
        enabled_tasks,
        notification_channel_status,
    )
    session_schedule = get_session_schedule_view(enabled_tasks)
    return jsonify(
        _health_response(
            runtime_diagnostics,
            preflight_checks,
            live_mode_status,
            rehearsal_status,
            enabled_tasks,
            session_schedule,
        )
    )


@dashboard_bp.get("/alerts/export.csv")
def export_alerts_csv():
    filters = _alert_history_filters(request.args)
    alerts = _alert_history_query(filters).order_by(AlertLog.created_at.desc(), AlertLog.id.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "time",
            "contract_code",
            "direction",
            "old_base_price",
            "new_base_price",
            "current_price",
            "difference",
            "grid_steps",
            "threshold",
            "source",
            "description",
            "notification_preview",
        ]
    )
    for alert in alerts:
        writer.writerow(
            [
                china_time(alert.created_at),
                alert.code,
                _direction_label(alert.direction),
                _format_number(alert.old_base_price),
                _format_number(alert.new_base_price),
                _format_number(alert.current_price),
                _format_number(alert.diff),
                alert.grid_steps,
                _format_number(alert.threshold),
                "未记录（历史报警）",
                alert.message,
                _alert_notification_preview(alert, current_app.config),
            ]
        )

    filename = f"alert_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@dashboard_bp.post("/alerts/clear")
def clear_alerts():
    contract_code = (request.form.get("contract_code") or "").strip().upper()
    query = db.session.query(AlertLog)
    if contract_code:
        query = query.filter(func.upper(AlertLog.code) == contract_code)

    deleted_count = query.delete(synchronize_session=False)
    db.session.commit()

    if contract_code:
        flash(f"已清空 {contract_code} 的报警历史记录，共删除 {deleted_count} 条。", "warning")
    else:
        flash(f"已清空全部报警历史记录，共删除 {deleted_count} 条。", "warning")
    return redirect(url_for("dashboard.index") + "#alert-history")


@dashboard_bp.post("/settings/monitor-poll-interval")
def update_monitor_poll_interval():
    raw_interval = request.form.get("poll_interval_seconds")
    try:
        seconds = validate_monitor_poll_interval_seconds(raw_interval)
    except AppSettingValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("dashboard.index"))

    set_monitor_poll_interval_seconds(seconds)
    flash(f"轮询间隔已更新为 {seconds} 秒", "success")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/settings/auto-session-scheduler")
def update_auto_session_scheduler():
    try:
        set_auto_session_scheduler_settings(
            enabled=request.form.get("auto_session_scheduler_enabled") == "on",
            day_session_start=request.form.get("day_session_start"),
            day_morning_stop=request.form.get("day_morning_stop"),
            day_afternoon_start=request.form.get("day_afternoon_start"),
            day_session_stop=request.form.get("day_session_stop"),
            night_session_start=request.form.get("night_session_start"),
            night_close_buffer_minutes=request.form.get("night_close_buffer_minutes"),
            trading_session_overrides=request.form.get("trading_session_overrides", ""),
        )
        # Apply immediately after a settings change; regular worker ticks then
        # keep the same decision up to date without persistent tick writes.
        apply_auto_session_schedule()
    except AppSettingValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("dashboard.index") + "#auto-session-scheduler")

    status = "开启" if get_auto_session_scheduler_settings()["enabled"] else "关闭"
    flash(f"自动交易时段已{status}。", "success")
    return redirect(url_for("dashboard.index") + "#auto-session-scheduler")


@dashboard_bp.post("/settings/ifind-auth")
def update_ifind_auth_settings():
    refresh_token = (request.form.get("ifind_refresh_value") or "").strip()
    account_expire_date = (request.form.get("ifind_account_expire_date") or "").strip()
    token_renew_day = (request.form.get("ifind_token_renew_day") or "").strip()
    password = request.form.get("dashboard_password") or ""

    if current_app.config.get("DASHBOARD_AUTH_ENABLED"):
        configured_password = current_app.config.get("DASHBOARD_PASSWORD", "")
        if not configured_password or not hmac.compare_digest(password, configured_password):
            flash("Dashboard 密码确认失败，iFind 授权设置未更新。", "danger")
            return redirect(url_for("dashboard.index") + "#ops-settings")

    if not refresh_token:
        flash("新的 iFind refresh token 不能为空。", "danger")
        return redirect(url_for("dashboard.index") + "#ops-settings")

    try:
        update_ifind_env_file(
            current_app.config["ENV_FILE_PATH"],
            {
                "IFIND_REFRESH_TOKEN": refresh_token,
                "IFIND_ACCOUNT_EXPIRE_DATE": account_expire_date,
                "IFIND_TOKEN_RENEW_DAY": token_renew_day,
            },
        )
    except FileNotFoundError:
        flash("保存失败：服务器 .env 文件不存在。", "danger")
        return redirect(url_for("dashboard.index") + "#ops-settings")
    except Exception:
        current_app.logger.exception("Failed to update iFind auth settings.")
        flash("保存失败：无法更新 iFind 授权设置。", "danger")
        return redirect(url_for("dashboard.index") + "#ops-settings")

    current_app.config["IFIND_REFRESH_TOKEN"] = refresh_token
    current_app.config["IFIND_ACCOUNT_EXPIRE_DATE"] = account_expire_date
    current_app.config["IFIND_TOKEN_RENEW_DAY"] = token_renew_day
    current_app.config["IFIND_ACCESS_TOKEN"] = ""
    reset_ifind_token_managers(refresh_token)

    flash("iFind 授权设置已保存，请测试 iFind 行情确认授权。", "success")
    return redirect(url_for("dashboard.index") + "#ops-settings")


@dashboard_bp.post("/tasks")
def create_task():
    try:
        form_data = _task_form_data()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("dashboard.index"))
    task = MonitorTask(**form_data)
    db.session.add(task)
    db.session.commit()
    _refresh_auto_session_schedule()
    flash("监控任务已新增", "success")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/monitor/pause")
def pause_monitor():
    schedule = apply_manual_monitoring_command(paused=True)
    if schedule["enabled"]:
        flash("自动监控已手动暂停，将在下一个计划边界后恢复自动时段控制。", "warning")
    else:
        flash("自动监控已暂停", "warning")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/monitor/resume")
def resume_monitor():
    schedule = apply_manual_monitoring_command(paused=False)
    if schedule["enabled"]:
        flash("自动监控已手动恢复，将在下一个计划边界后恢复自动时段控制。", "success")
    else:
        flash("自动监控已恢复", "success")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/notifications/test")
def test_notification():
    mode = current_app.config["NOTIFICATION_MODE"]
    notifier = get_notifier(mode, current_app.config)
    try:
        result = notifier.send({"notification_content": _test_notification_content()})
    except Exception as exc:
        flash(f"测试通知发送失败：{_safe_flash_error(exc)}", "danger")
        return redirect(url_for("dashboard.index"))

    flash_message, category = _test_notification_flash(result)
    flash(flash_message, category)
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/tasks/<int:task_id>/test-ifind-quote")
def test_ifind_quote(task_id: int):
    task = db.get_or_404(MonitorTask, task_id)
    market_mode = (current_app.config["MARKET_DATA_MODE"] or "manual_only").strip().lower()
    if market_mode != "ifind":
        flash(
            f"当前行情模式为 {market_mode}。iFind 单次测试需要 MARKET_DATA_MODE=ifind。",
            "warning",
        )
        return redirect(url_for("dashboard.index"))

    provider = get_market_data_provider("ifind", current_app.config)
    if provider is None:
        flash("iFind 行情测试失败：iFind provider 未配置。", "danger")
        return redirect(url_for("dashboard.index"))

    try:
        price = provider.get_price(task.code)
    except Exception as exc:
        flash(f"iFind 行情测试失败：{_safe_flash_error(exc)}", "danger")
        return redirect(url_for("dashboard.index"))

    if price.success:
        flash(_ifind_quote_success_message(task.code, price), "success")
    else:
        flash(_ifind_quote_failure_message(task.code, price), "danger")

    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/tasks/<int:task_id>/edit")
def edit_task(task_id: int):
    task = db.get_or_404(MonitorTask, task_id)
    try:
        form_data = _task_form_data()
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("dashboard.index"))

    task.code = form_data["code"]
    task.base_price = form_data["base_price"]
    task.threshold = form_data["threshold"]
    task.is_active = form_data["is_active"]

    db.session.commit()
    _refresh_auto_session_schedule()
    flash("监控任务已更新", "success")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/tasks/<int:task_id>/delete")
def delete_task(task_id: int):
    task = db.get_or_404(MonitorTask, task_id)
    db.session.delete(task)
    db.session.commit()
    _refresh_auto_session_schedule()
    flash("监控任务已删除", "warning")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/tasks/<int:task_id>/toggle")
def toggle_task(task_id: int):
    task = db.get_or_404(MonitorTask, task_id)
    task.is_active = not task.is_active
    db.session.commit()
    _refresh_auto_session_schedule()
    flash("监控任务状态已切换", "info")
    return redirect(url_for("dashboard.index"))


@dashboard_bp.post("/tasks/<int:task_id>/test-price")
def test_price(task_id: int):
    task = db.get_or_404(MonitorTask, task_id)
    current_price = _parse_test_price()
    if current_price is None:
        flash("请输入有效的模拟价格。", "danger")
        return redirect(url_for("dashboard.index"))

    try:
        result = MonitoringEngine(
            notification_mode=current_app.config["NOTIFICATION_MODE"],
            notification_config=current_app.config,
        ).process_task_price(
            task,
            current_price,
            message_prefix="[手动测试]",
            source="manual_test",
        )
        if result.triggered:
            db.session.commit()
            direction_label = "上涨" if result.direction == "UP" else "下跌"
            flash(
                f"{task.code} 模拟价格触发{direction_label}突破，"
                f"基准价已从 {result.old_base_price:.2f} 更新为 {result.new_base_price:.2f}。",
                "success",
            )
        else:
            flash(f"{task.code} 模拟价格未突破当前网格。", "info")
    except Exception:
        db.session.rollback()
        flash("手动模拟价格测试失败，请检查输入后重试。", "danger")

    return redirect(url_for("dashboard.index"))


def _task_form_data() -> dict:
    return {
        "code": _normalize_task_code(request.form.get("code", "")),
        "base_price": float(request.form["base_price"]),
        "threshold": float(request.form["threshold"]),
        "is_active": request.form.get("is_active") == "on",
    }


def _normalize_task_code(raw_code: str) -> str:
    code = (raw_code or "").strip().upper()
    error_message = "合约代码必须填写完整 iFind 代码，例如 CU2608.SHF、Y2609.DCE、LC2609.GFE。"
    if not code:
        raise ValueError(error_message)
    if not re.fullmatch(r"[A-Z0-9_-]+\.[A-Z]{2,6}", code):
        raise ValueError(error_message)
    return code


def _parse_test_price() -> float | None:
    raw_price = request.form.get("current_price", "").strip()
    if not raw_price:
        return None

    try:
        current_price = float(raw_price)
    except ValueError:
        return None

    if current_price <= 0:
        return None

    return current_price


def _test_notification_content() -> str:
    return "\n".join(
        [
            "【网格监控测试】",
            "这是一条通知链路测试消息。",
            "不会创建报警记录，不会修改基准价。",
            f"时间：{china_time(datetime.now(timezone.utc))}",
        ]
    )


def _test_notification_flash(result) -> tuple[str, str]:
    if result.mode == "dry_run" and result.success:
        return "测试通知预览已生成。", "success"
    if result.mode == "feishu" and result.success:
        return "测试通知已发送到飞书。", "success"
    if result.mode == "off":
        return "通知已关闭，未发送测试通知。", "warning"
    if result.mode == "wecom_placeholder":
        return "企业微信真实发送尚未启用，未发送测试通知。", "warning"
    if result.mode == "feishu_placeholder":
        return "飞书当前为占位通道，未发送真实测试通知。", "warning"

    safe_error = result.safe_error_summary or result.error_message or result.message or "未知错误"
    return f"测试通知发送失败：{_safe_flash_error(safe_error)}", "danger"


def _safe_flash_error(error) -> str:
    text = str(error)
    forbidden_terms = [
        current_app.config.get("FEISHU_WEBHOOK_URL", ""),
        current_app.config.get("FEISHU_WEBHOOK_SECRET", ""),
        current_app.config.get("IFIND_REFRESH_TOKEN", ""),
        current_app.config.get("IFIND_ACCESS_TOKEN", ""),
        "Bearer",
        "Authorization",
        "headers",
        ".env",
    ]
    for term in forbidden_terms:
        if term:
            text = text.replace(term, "[已隐藏]")
    return text


def _ifind_auth_settings_view() -> dict:
    return {
        "refresh_token_configured": bool(current_app.config.get("IFIND_REFRESH_TOKEN")),
        "account_expire_date": current_app.config.get("IFIND_ACCOUNT_EXPIRE_DATE") or "",
        "token_renew_day": current_app.config.get("IFIND_TOKEN_RENEW_DAY") or "",
    }


def _credentials_match(username: str, password: str, configured_username: str, configured_password: str) -> bool:
    return hmac.compare_digest(username, configured_username) and hmac.compare_digest(
        password,
        configured_password,
    )


def _login_redirect_target() -> str:
    return _safe_next_url(request.args.get("next") or url_for("dashboard.index"))


def _safe_next_url(value: str | None) -> str:
    if not value:
        return url_for("dashboard.index")

    value = value.strip()
    if not value.startswith("/") or value.startswith("//"):
        return url_for("dashboard.index")

    return value


def _ifind_quote_success_message(code: str, price) -> str:
    resolved_code = _ifind_resolved_code(code, price)
    code_label = f"{code} → {resolved_code}" if resolved_code and resolved_code != code else code
    message = f"iFind 行情测试成功：{code_label} latest={price.current_price:g}"
    if price.timestamp:
        message = f"{message}，时间={china_time(price.timestamp)}"
    return message


def _ifind_quote_failure_message(code: str, price) -> str:
    resolved_code = _ifind_resolved_code(code, price)
    code_label = f"{code} → {resolved_code}" if resolved_code and resolved_code != code else code
    error = _safe_flash_error(price.error_message or "未获取到行情。")
    message = f"iFind 行情测试失败：{code_label}，{error}。"
    if resolved_code and resolved_code != code:
        message = f"{message}请检查 IFIND_CODE_MAP 或 IFIND_EXCHANGE_SUFFIX_MAP。"
    return message


def _ifind_resolved_code(code: str, price) -> str | None:
    raw = getattr(price, "raw", None) or {}
    return raw.get("ifind_code") or code


def _market_mode_view(mode: str) -> dict:
    mode_map = {
        "manual_only": {
            "label": "手动测试模式",
            "description": "当前为 manual_only 安全模式，自动监控不会请求真实行情。",
            "badge_class": "text-bg-secondary",
        },
        "simulated": {
            "label": "模拟行情模式",
            "description": "后台 worker 正在使用模拟行情，仅用于本地开发测试。",
            "badge_class": "text-bg-warning",
        },
        "real": {
            "label": "真实行情模式",
            "description": "真实行情源尚未配置，当前不会自动获取行情。",
            "badge_class": "text-bg-primary",
        },
        "ifind": {
            "label": "iFind 行情模式",
            "description": "当前为 iFind 真实行情模式，自动监控启用时会请求真实 iFind 行情。",
            "badge_class": "text-bg-primary",
        },
    }
    return mode_map.get(mode, mode_map["manual_only"])


def _monitor_status_view(status: dict) -> dict:
    poll_status_map = {
        "success": {"label": "正常", "badge_class": "text-bg-success"},
        "skipped": {"label": "已跳过", "badge_class": "text-bg-secondary"},
        "error": {"label": "异常", "badge_class": "text-bg-danger"},
    }
    system_status_map = {
        "running": {"label": "正常运行", "badge_class": "text-bg-success"},
        "manual_only": {"label": "自动监控关闭", "badge_class": "text-bg-secondary"},
        "real_not_configured": {"label": "真实行情未配置", "badge_class": "text-bg-warning"},
        "error": {"label": "有错误", "badge_class": "text-bg-danger"},
    }
    system_status = _system_status(status)
    return {
        "system": system_status_map[system_status],
        "poll": poll_status_map.get(
            status.get("last_poll_status"),
            {"label": "未知", "badge_class": "text-bg-secondary"},
        ),
        "worker_label": "已启动" if status.get("worker_started") else "未启动",
        "worker_badge_class": "text-bg-success" if status.get("worker_started") else "text-bg-secondary",
        "auto_label": "已启用" if status.get("auto_monitoring_enabled") else "未启用",
        "auto_badge_class": "text-bg-success" if status.get("auto_monitoring_enabled") else "text-bg-secondary",
    }


def _system_status(status: dict) -> str:
    if status.get("last_poll_status") == "error":
        return "error"
    if status.get("provider_status") in {"real_not_configured", "ifind_not_implemented"}:
        return "real_not_configured"
    if not status.get("auto_monitoring_enabled"):
        return "manual_only"
    return "running"


def _runtime_diagnostics(ifind_permission_status, notification_channel_status) -> dict:
    return get_runtime_diagnostics(
        {
            "notification_mode": current_app.config["NOTIFICATION_MODE"],
            "notification_channel": notification_channel_status.channel,
            "notification_channel_configured": notification_channel_status.is_configured,
            "notification_channel_placeholder": notification_channel_status.is_placeholder,
            "notification_can_send_real_message": notification_channel_status.can_send_real_message,
            "notification_channel_message": notification_channel_status.message,
            "notification_channel_error": notification_channel_status.error,
            "feishu_webhook_configured": bool(current_app.config.get("FEISHU_WEBHOOK_URL")),
            "feishu_secret_configured": bool(current_app.config.get("FEISHU_WEBHOOK_SECRET")),
            "ifind_permission_renewal_day": ifind_permission_status.renewal_day,
            "ifind_permission_next_renewal_date": _date_to_string(ifind_permission_status.next_renewal_date),
            "ifind_permission_days_until_renewal": ifind_permission_status.days_until_renewal,
            "ifind_permission_status": ifind_permission_status.status,
            "ifind_permission_message": ifind_permission_status.message,
            "ifind_permission_error": ifind_permission_status.error,
        }
    )


def _enabled_tasks_count() -> int:
    return MonitorTask.query.filter(MonitorTask.is_active.is_(True)).count()


def _enabled_tasks() -> list[MonitorTask]:
    return MonitorTask.query.filter(MonitorTask.is_active.is_(True)).order_by(MonitorTask.code.asc()).all()


def _refresh_auto_session_schedule() -> None:
    """Re-evaluate the persisted schedule after task configuration changes."""
    try:
        apply_auto_session_schedule()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to refresh automatic trading-session schedule.")


def _preflight_checks(
    status: dict,
    ifind_permission_status,
    notification_channel_status,
    enabled_tasks_count: int | None = None,
) -> dict:
    if enabled_tasks_count is None:
        enabled_tasks_count = _enabled_tasks_count()
    return get_preflight_checks(
        current_app.config,
        status,
        ifind_permission_status,
        notification_channel_status,
        enabled_tasks_count,
    )


def _preflight_view(preflight_checks: dict) -> dict:
    overall_map = {
        "ready": {"label": "准备就绪", "badge_class": "text-bg-success"},
        "warning": {"label": "存在警告", "badge_class": "text-bg-warning"},
        "blocked": {"label": "存在阻塞项", "badge_class": "text-bg-danger"},
    }
    status_map = {
        "pass": {"label": "通过", "badge_class": "text-bg-success"},
        "warning": {"label": "警告", "badge_class": "text-bg-warning"},
        "blocked": {"label": "阻塞", "badge_class": "text-bg-danger"},
    }
    return {
        "overall": overall_map.get(preflight_checks["overall_status"], overall_map["warning"]),
        "status_map": status_map,
    }


def _health_response(
    status: dict,
    preflight_checks: dict,
    live_mode_status: dict,
    rehearsal_status: dict,
    enabled_tasks: list[MonitorTask],
    session_schedule: dict,
) -> dict:
    market_poll_summary = _market_poll_summary_for_tasks(enabled_tasks, status)
    notification_deliveries = delivery_summary()
    return {
        "status": "error" if status.get("last_poll_status") == "error" else "ok",
        "worker_started": status.get("worker_started"),
        "market_data_mode": status.get("market_data_mode"),
        "auto_monitoring_enabled": status.get("auto_monitoring_enabled"),
        "auto_monitoring_polling_active": status.get("auto_monitoring_polling_active"),
        "auto_monitoring_paused": status.get("auto_monitoring_paused"),
        "last_poll_status": status.get("last_poll_status"),
        "last_poll_at": _isoformat_or_none(status.get("last_poll_at")),
        "last_poll_message": status.get("last_poll_message"),
        "last_poll_duration_ms": status.get("last_poll_duration_ms"),
        "monitor_poll_interval_seconds": get_monitor_poll_interval_seconds(),
        "last_poll_error": status.get("last_poll_error") or status.get("last_error"),
        "last_market_provider": status.get("last_market_provider"),
        "last_market_request_status": status.get("last_market_request_status"),
        "last_market_request_message": status.get("last_market_request_message"),
        "last_market_request_at": _isoformat_or_none(status.get("last_market_request_at")),
        "last_market_request_duration_ms": status.get("last_market_request_duration_ms"),
        "last_market_error": status.get("last_market_error"),
        "market_last_attempt_at": _isoformat_or_none(status.get("last_market_attempt_at")),
        "market_last_success_at": _isoformat_or_none(status.get("last_market_success_at")),
        "market_last_failure_at": _isoformat_or_none(status.get("last_market_failure_at")),
        "market_last_contract_code": status.get("last_market_contract_code"),
        "market_last_resolved_code": status.get("last_market_resolved_code"),
        "market_last_price": status.get("last_market_price"),
        "market_last_source": status.get("last_market_source"),
        "market_last_error_summary": status.get("last_market_error_summary"),
        "market_consecutive_failures": status.get("consecutive_market_failures"),
        "market_poll_status_by_contract": _serialize_market_poll_status(
            status.get("market_poll_status_by_contract")
        ),
        "market_poll_success_count": market_poll_summary["success_count"],
        "market_poll_failure_count": market_poll_summary["failure_count"],
        "market_poll_waiting_count": market_poll_summary["waiting_count"],
        "ifind_batch_size": _ifind_batch_size(),
        "market_poll_batch_enabled": True,
        "last_error": status.get("last_error"),
        "ifind_auth_state": status.get("ifind_auth_state"),
        "ifind_refresh_configured": status.get("ifind_has_refresh_token"),
        "ifind_access_ready": status.get("ifind_has_access_token"),
        "ifind_last_refresh_success": status.get("ifind_last_refresh_success"),
        "ifind_last_refresh_at": _isoformat_or_none(status.get("ifind_last_refresh_at")),
        "ifind_last_error": status.get("ifind_last_error"),
        "ifind_permission_renewal_day": status.get("ifind_permission_renewal_day"),
        "ifind_permission_next_renewal_date": status.get("ifind_permission_next_renewal_date"),
        "ifind_permission_days_until_renewal": status.get("ifind_permission_days_until_renewal"),
        "ifind_permission_status": status.get("ifind_permission_status"),
        "ifind_permission_message": status.get("ifind_permission_message"),
        "ifind_permission_error": status.get("ifind_permission_error"),
        "notification_mode": status.get("notification_mode"),
        "notification_channel": status.get("notification_channel"),
        "notification_channel_configured": status.get("notification_channel_configured"),
        "notification_channel_placeholder": status.get("notification_channel_placeholder"),
        "notification_can_send_real_message": status.get("notification_can_send_real_message"),
        "notification_channel_message": status.get("notification_channel_message"),
        "notification_channel_error": status.get("notification_channel_error"),
        "feishu_webhook_configured": status.get("feishu_webhook_configured"),
        "feishu_secret_configured": status.get("feishu_secret_configured"),
        "last_notification_status": status.get("last_notification_status"),
        "last_notification_success": status.get("last_notification_success"),
        "last_notification_error": status.get("last_notification_error"),
        "last_notification_at": _isoformat_or_none(status.get("last_notification_at")),
        "last_notification_channel": status.get("last_notification_channel"),
        "last_notification_delivery_status": status.get("last_notification_delivery_status"),
        "last_notification_duration_ms": status.get("last_notification_duration_ms"),
        "last_notification_safe_error_summary": status.get("last_notification_safe_error_summary"),
        "last_notification_can_retry": status.get("last_notification_can_retry"),
        "last_notification_attempt_count": status.get("last_notification_attempt_count"),
        "notification_delivery_pending_count": notification_deliveries["pending"],
        "notification_delivery_retrying_count": notification_deliveries["retrying"],
        "notification_delivery_failed_count": notification_deliveries["failed"],
        "notification_delivery_sent_count": notification_deliveries["sent"],
        "notification_delivery_channel_status": notification_deliveries["channel_status"],
        "last_alert_status": status.get("last_alert_status"),
        "last_alert_message": status.get("last_alert_message"),
        "last_alert_code": status.get("last_alert_code"),
        "last_alert_direction": status.get("last_alert_direction"),
        "last_alert_grid_steps": status.get("last_alert_grid_steps"),
        "last_alert_created": status.get("last_alert_created"),
        "last_alert_dedup_status": status.get("last_alert_dedup_status"),
        "last_duplicate_alert_skipped": status.get("last_duplicate_alert_skipped"),
        "last_duplicate_notification_skipped": status.get("last_duplicate_notification_skipped"),
        "notification_burst_enabled": status.get("notification_burst_enabled"),
        "last_burst_alert_detected": status.get("last_burst_alert_detected"),
        "last_burst_alert_count": status.get("last_burst_alert_count"),
        "last_burst_alert_window_seconds": status.get("last_burst_alert_window_seconds"),
        "last_burst_alert_message": status.get("last_burst_alert_message"),
        "auto_monitoring_pause_reason": status.get("auto_monitoring_pause_reason"),
        "auto_monitoring_updated_at": _isoformat_or_none(status.get("auto_monitoring_updated_at")),
        "auto_session_scheduler_enabled": session_schedule["enabled"],
        "auto_monitoring_control_source": status.get("auto_monitoring_control_source", "manual"),
        "manual_override_until": _isoformat_or_none(status.get("manual_override_until")),
        "session_scheduler_next_action": session_schedule["next_action"]["at"].isoformat(),
        "session_scheduler_next_action_type": session_schedule["next_action"]["action"],
        "session_scheduler_session_state": session_schedule["session_state_key"],
        "session_scheduler_session_title": session_schedule["session_state_title"],
        "session_scheduler_day_morning_start": format_clock(session_schedule["day_morning_start"]),
        "session_scheduler_day_morning_stop": format_clock(session_schedule["day_morning_stop"]),
        "session_scheduler_day_afternoon_start": format_clock(session_schedule["day_afternoon_start"]),
        "session_scheduler_day_afternoon_stop": format_clock(session_schedule["day_afternoon_stop"]),
        "session_scheduler_day_available": session_schedule["day_available"],
        "session_scheduler_night_available": session_schedule["night_available"],
        "session_scheduler_night_close_time": format_clock(session_schedule["night_close_time"]),
        "session_scheduler_unknown_contracts": [item.code for item in session_schedule["unknown_contracts"]],
        "preflight_overall_status": preflight_checks["overall_status"],
        "preflight_ready_count": preflight_checks["ready_count"],
        "preflight_warning_count": preflight_checks["warning_count"],
        "preflight_blocked_count": preflight_checks["blocked_count"],
        "preflight_checks": preflight_checks["checks"],
        "alert_history_total_count": AlertLog.query.count(),
        "live_mode_status": live_mode_status["live_mode_status"],
        "live_mode_title": live_mode_status["live_mode_title"],
        "live_mode_message": live_mode_status["live_mode_message"],
        "live_mode_suggestions": live_mode_status["live_mode_suggestions"],
        "is_live_market_enabled": live_mode_status["is_live_market_enabled"],
        "is_real_notification_enabled": live_mode_status["is_real_notification_enabled"],
        "is_auto_monitoring_active": live_mode_status["is_auto_monitoring_active"],
        "live_mode_blockers_count": live_mode_status["live_mode_blockers_count"],
        "live_mode_warnings_count": live_mode_status["live_mode_warnings_count"],
        "rehearsal_status": rehearsal_status["status_key"],
        "rehearsal_title": rehearsal_status["title"],
        "rehearsal_blockers_count": rehearsal_status["blockers_count"],
        "rehearsal_warnings_count": rehearsal_status["warnings_count"],
        "rehearsal_suggestions": rehearsal_status["suggestions"],
    }


def _isoformat_or_none(value):
    if value is None:
        return None
    return value.isoformat()


def _serialize_market_poll_status(status_by_contract: dict | None) -> dict:
    serialized = {}
    for code, item in (status_by_contract or {}).items():
        serialized[code] = {
            **item,
            "checked_at": _isoformat_or_none(item.get("checked_at")),
            "last_success_at": _isoformat_or_none(item.get("last_success_at")),
            "last_failure_at": _isoformat_or_none(item.get("last_failure_at")),
        }
    return serialized


def _task_market_status_view(tasks: list[MonitorTask], status: dict) -> dict:
    status_by_contract = status.get("market_poll_status_by_contract") or {}
    views = {}
    for task in tasks:
        entry = status_by_contract.get(task.code)
        if not task.is_active:
            views[task.id] = _disabled_market_status_view(task.code)
        elif _market_status_entry_is_stale_for_task(task, entry):
            views[task.id] = _market_status_entry_view(task.code, None)
        else:
            views[task.id] = _market_status_entry_view(task.code, entry)
    return views


def _disabled_market_status_view(code: str) -> dict:
    return {
        "label": "已停用",
        "badge_class": "text-bg-secondary",
        "note": "不参与行情轮询",
        "price": None,
        "checked_at": None,
        "error": None,
        "error_label": None,
        "consecutive_failures": 0,
        "resolved_code": code,
    }


def _market_status_entry_view(code: str, entry: dict | None) -> dict:
    if not entry:
        return {
            "label": "等待轮询",
            "badge_class": "text-bg-secondary",
            "note": None,
            "price": None,
            "checked_at": None,
            "error": None,
            "error_label": None,
            "consecutive_failures": 0,
            "resolved_code": code,
        }

    if entry.get("success"):
        return {
            "label": "行情正常",
            "badge_class": "text-bg-success",
            "note": None,
            "price": entry.get("price"),
            "checked_at": entry.get("checked_at"),
            "error": None,
            "error_label": None,
            "consecutive_failures": 0,
            "resolved_code": entry.get("resolved_code") or code,
        }

    error = entry.get("error") or "行情请求失败"
    return {
        "label": "行情失败",
        "badge_class": "text-bg-danger",
        "note": None,
        "price": entry.get("price"),
        "checked_at": entry.get("checked_at") or entry.get("last_failure_at"),
        "error": error,
        "error_label": _market_error_label(entry.get("error_category"), error),
        "consecutive_failures": entry.get("consecutive_failures") or 0,
        "resolved_code": entry.get("resolved_code") or code,
    }


def _market_error_label(category: str | None, error: str) -> str:
    labels = {
        "permission_denied": "权限被拒绝",
        "timeout": "请求超时",
        "invalid_parameter": "参数错误",
    }
    return labels.get(category or "", error)


def _market_poll_summary_view(tasks: list[MonitorTask], status: dict) -> dict:
    summary = _market_poll_summary_for_tasks(tasks, status)
    if summary["total"] == 0 or summary["waiting_count"] == summary["total"]:
        label = "等待轮询"
        badge_class = "text-bg-secondary"
    elif summary["failure_count"] and summary["success_count"]:
        label = "部分失败"
        badge_class = "text-bg-warning"
    elif summary["failure_count"] == summary["total"]:
        label = "全部失败"
        badge_class = "text-bg-danger"
    elif summary["success_count"] == summary["total"]:
        label = "全部正常"
        badge_class = "text-bg-success"
    else:
        label = "等待轮询"
        badge_class = "text-bg-secondary"
    return {**summary, "label": label, "badge_class": badge_class}


def _market_poll_summary_for_tasks(tasks: list[MonitorTask], status: dict) -> dict:
    status_by_contract = status.get("market_poll_status_by_contract") or {}
    active_tasks = [task for task in tasks if task.is_active]
    success_count = 0
    failure_count = 0
    waiting_count = 0
    for task in active_tasks:
        entry = status_by_contract.get(task.code)
        if not entry or _market_status_entry_is_stale_for_task(task, entry):
            waiting_count += 1
        elif entry.get("success"):
            success_count += 1
        else:
            failure_count += 1
    return {
        "total": len(active_tasks),
        "success_count": success_count,
        "failure_count": failure_count,
        "waiting_count": waiting_count,
    }


def _market_status_entry_is_stale_for_task(task: MonitorTask, entry: dict | None) -> bool:
    if not entry:
        return False
    if entry.get("success"):
        return False

    checked_at = entry.get("checked_at") or entry.get("last_success_at") or entry.get("last_failure_at")
    if not checked_at or not task.updated_at:
        return False

    return _as_utc_aware(checked_at) < _as_utc_aware(task.updated_at)


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ifind_batch_size() -> int:
    try:
        batch_size = int(current_app.config.get("IFIND_BATCH_SIZE", 5))
    except (TypeError, ValueError):
        return 5
    return batch_size if batch_size > 0 else 5


def _date_to_string(value):
    if value is None:
        return None
    return value.isoformat()


def _alert_history_context(args) -> dict:
    filters = _alert_history_filters(args)
    query = _alert_history_query(filters).order_by(AlertLog.created_at.desc(), AlertLog.id.desc())
    total_count = query.count()
    total_pages = max((total_count + filters["per_page"] - 1) // filters["per_page"], 1)
    page = min(filters["page"], total_pages)
    items = query.offset((page - 1) * filters["per_page"]).limit(filters["per_page"]).all()
    filters["page"] = page

    return {
        "items": items,
        "filters": filters,
        "active_filter_labels": _active_filter_labels(filters),
        "filter_summary": _alert_history_filter_summary(filters),
        "quick_contracts": _alert_history_quick_contracts(),
        "quick_contract_links": _alert_history_quick_contract_links(filters),
        "all_contract_args": _alert_history_contract_args(filters, None),
        "page": page,
        "per_page": filters["per_page"],
        "per_page_options": [10, 20, 50, 100],
        "total_count": total_count,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_args": _alert_history_url_args(filters, page - 1),
        "next_args": _alert_history_url_args(filters, page + 1),
        "export_args": _alert_history_export_args(filters),
    }


def _alert_history_filters(args) -> dict:
    return {
        "page": _positive_int(args.get("alert_page"), 1),
        "per_page": _per_page(args.get("alert_per_page")),
        "contract_code": args.get("contract_code", "").strip().upper(),
        "direction": args.get("direction", "").strip(),
        "direction_value": _direction_value(args.get("direction", "")),
        "alert_type": _alert_type(args.get("alert_type", "all")),
        "delivery_status": _delivery_status_filter(args.get("delivery_status", "")),
        "date_from": args.get("date_from", "").strip(),
        "date_from_value": _parse_date_start(args.get("date_from", "")),
        "date_to": args.get("date_to", "").strip(),
        "date_to_value": _parse_date_end(args.get("date_to", "")),
    }


def _alert_history_query(filters: dict):
    query = AlertLog.query
    if filters["contract_code"]:
        query = query.filter(func.upper(AlertLog.code) == filters["contract_code"])
    if filters["direction_value"]:
        query = query.filter(AlertLog.direction == filters["direction_value"])
    if filters["date_from_value"]:
        query = query.filter(AlertLog.created_at >= filters["date_from_value"])
    if filters["date_to_value"]:
        query = query.filter(AlertLog.created_at < filters["date_to_value"])

    alert_type = filters["alert_type"]
    if alert_type == "severe":
        query = query.filter(AlertLog.grid_steps >= 2)
    elif alert_type == "burst":
        query = query.filter(AlertLog.message.contains("高频报警"))
    elif alert_type == "duplicate_skipped":
        query = query.filter(AlertLog.message.contains("duplicate_skipped"))

    if filters["delivery_status"] == "undelivered":
        query = query.join(NotificationDelivery).filter(
            NotificationDelivery.status.in_(("pending", "retrying", "failed"))
        )

    return query


def _alert_history_quick_contracts() -> list[str]:
    task_codes = {
        code
        for (code,) in db.session.query(MonitorTask.code).filter(MonitorTask.code.isnot(None)).distinct().all()
        if code
    }
    alert_codes = {
        code
        for (code,) in db.session.query(AlertLog.code).filter(AlertLog.code.isnot(None)).distinct().all()
        if code
    }
    return sorted({code.strip().upper() for code in task_codes | alert_codes if code.strip()})


def _alert_history_quick_contract_links(filters: dict) -> list[dict]:
    return [
        {"code": code, "args": _alert_history_contract_args(filters, code)}
        for code in _alert_history_quick_contracts()
    ]


def _alert_history_contract_args(filters: dict, contract_code: str | None) -> dict:
    url_args = {}
    if contract_code:
        url_args["contract_code"] = contract_code
    for key in ("direction", "alert_type", "delivery_status", "date_from", "date_to"):
        value = filters.get(key)
        if value and not ((key == "alert_type" or key == "delivery_status") and value == "all"):
            url_args[key] = value
    if filters.get("per_page") and filters["per_page"] != 20:
        url_args["alert_per_page"] = filters["per_page"]
    return url_args


def _alert_history_url_args(filters: dict, page: int) -> dict:
    url_args = {
        "alert_page": page,
        "alert_per_page": filters["per_page"],
    }
    for key in ("contract_code", "direction", "alert_type", "delivery_status", "date_from", "date_to"):
        value = filters.get(key)
        if value and not ((key == "alert_type" or key == "delivery_status") and value == "all"):
            url_args[key] = value
    return url_args


def _alert_history_export_args(filters: dict) -> dict:
    url_args = {}
    for key in ("contract_code", "direction", "alert_type", "delivery_status", "date_from", "date_to"):
        value = filters.get(key)
        if value and not ((key == "alert_type" or key == "delivery_status") and value == "all"):
            url_args[key] = value
    return url_args


def _active_filter_labels(filters: dict) -> list[str]:
    labels = []
    if filters["contract_code"]:
        labels.append(f"合约 {filters['contract_code']}")
    if filters["direction_value"]:
        labels.append("方向 上涨" if filters["direction_value"] == "UP" else "方向 下跌")
    if filters["alert_type"] != "all":
        type_labels = {
            "burst": "类型 高频报警",
            "severe": "类型 剧烈波动",
            "duplicate_skipped": "类型 重复跳过",
        }
        labels.append(type_labels.get(filters["alert_type"], filters["alert_type"]))
    if filters["delivery_status"] == "undelivered":
        labels.append("通知 未送达")
    if filters["date_from"]:
        labels.append(f"起始 {filters['date_from']}")
    if filters["date_to"]:
        labels.append(f"结束 {filters['date_to']}")
    return labels


def _alert_history_filter_summary(filters: dict) -> dict:
    date_label = "全部"
    if filters["date_from"] and filters["date_to"]:
        date_label = f"{filters['date_from']} 至 {filters['date_to']}"
    elif filters["date_from"]:
        date_label = f"{filters['date_from']} 起"
    elif filters["date_to"]:
        date_label = f"截至 {filters['date_to']}"

    type_labels = {
        "all": "全部",
        "burst": "高频报警",
        "severe": "剧烈波动",
        "duplicate_skipped": "重复跳过",
    }
    return {
        "contract": filters["contract_code"] or "全部合约",
        "direction": _direction_label(filters["direction_value"]) if filters["direction_value"] else "全部",
        "alert_type": type_labels.get(filters["alert_type"], "全部"),
        "delivery_status": "未送达" if filters["delivery_status"] == "undelivered" else "全部",
        "date": date_label,
    }


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _per_page(value) -> int:
    parsed = _positive_int(value, 20)
    return parsed if parsed in {10, 20, 50, 100} else 20


def _direction_value(value: str) -> str | None:
    normalized = (value or "").strip().lower()
    if normalized in {"up", "上涨", "UP".lower()}:
        return "UP"
    if normalized in {"down", "下跌", "DOWN".lower()}:
        return "DOWN"
    return None


def _alert_type(value: str) -> str:
    normalized = (value or "all").strip().lower()
    return normalized if normalized in {"all", "burst", "severe", "duplicate_skipped"} else "all"


def _delivery_status_filter(value: str) -> str:
    return "undelivered" if (value or "").strip().lower() == "undelivered" else "all"


def _parse_date_start(value: str):
    parsed = _parse_date(value)
    return parsed


def _parse_date_end(value: str):
    parsed = _parse_date(value)
    return parsed + timedelta(days=1) if parsed else None


def _parse_date(value: str):
    raw_value = (value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d")
    except ValueError:
        return None


def _alert_notification_preview(alert: AlertLog, config) -> str:
    burst_context = _historical_burst_context(alert, config)
    return build_alert_notification(
        {
            "code": alert.code,
            "direction": alert.direction,
            "current_price": alert.current_price,
            "old_base_price": alert.old_base_price,
            "new_base_price": alert.new_base_price,
            "grid_steps": alert.grid_steps,
            "threshold": alert.threshold,
            "source": "unknown",
            "source_label": "未记录（历史报警）",
            "created_at": china_time(alert.created_at),
            "alert_number": f"ALERT-{alert.created_at.strftime('%Y%m%d')}-{alert.id:06d}",
            **burst_context,
        }
    )


def _historical_burst_context(alert: AlertLog, config) -> dict:
    burst_config = parse_burst_config(config)
    if not burst_config.enabled or alert.created_at is None:
        return build_burst_context(0, config)

    since = alert.created_at - timedelta(seconds=burst_config.window_seconds)
    alert_count = (
        AlertLog.query.filter(
            AlertLog.task_id == alert.task_id,
            AlertLog.code == alert.code,
            AlertLog.created_at >= since,
            AlertLog.created_at <= alert.created_at,
        ).count()
    )
    return build_burst_context(alert_count, config)


def _direction_label(direction: str) -> str:
    if direction == "UP":
        return "上涨"
    if direction == "DOWN":
        return "下跌"
    return direction or ""


def _format_number(value) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"
