import csv
import html
import io
import re
from datetime import datetime, timedelta

from app.db import AlertLog, MonitorTask, db
from app.settings import get_monitor_poll_interval_seconds, set_monitor_poll_interval_seconds


SENSITIVE_TERMS = [
    "access_token",
    "refresh_token",
    "Bearer",
    "IFIND_REFRESH_TOKEN",
    "IFIND_ACCESS_TOKEN",
    "FEISHU_WEBHOOK_URL",
    "FEISHU_WEBHOOK_SECRET",
    "WECOM_WEBHOOK_URL",
    ".env",
    "headers",
]


def assert_safe_text(text: str):
    for term in SENSITIVE_TERMS:
        assert term not in text


def csv_rows(response):
    text = response.data.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def csv_text(response):
    return response.data.decode("utf-8-sig")


def create_alert(
    code="CU2608",
    direction="UP",
    grid_steps=1,
    created_at=None,
    message="alert-marker",
):
    alert = AlertLog(
        task_id=None,
        code=code,
        base_price=105600,
        old_base_price=105600,
        new_base_price=106600 if direction == "UP" else 104600,
        current_price=106720 if direction == "UP" else 104480,
        diff=1120 if direction == "UP" else -1120,
        threshold=1000,
        grid_steps=grid_steps,
        direction=direction,
        message=message,
        created_at=created_at or datetime(2026, 6, 30, 21, 0, 0),
    )
    db.session.add(alert)
    return alert


def create_numbered_alerts(count=25, code="CU2608"):
    base_time = datetime(2026, 6, 1, 21, 0, 0)
    for index in range(count):
        create_alert(
            code=code,
            created_at=base_time + timedelta(minutes=index),
            message=f"ALERT-{index:02d}",
        )
    db.session.commit()


def create_task(code="CU2608"):
    task = MonitorTask(code=code, base_price=105600, threshold=1000, is_active=True)
    db.session.add(task)
    db.session.commit()
    return task


def test_clear_alert_history_button_is_visible_with_post_and_confirm(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "清空全部历史" in page
    assert 'action="/alerts/clear"' in page
    assert 'method="post"' in page
    assert "确定要清空全部报警历史记录吗？此操作不会删除监控任务，但不可撤销。" in page


def test_clear_current_contract_history_button_when_contract_filter_is_active(client):
    create_alert(code="CU2608.SHF", message="CLEAR-CU-BUTTON")
    db.session.commit()

    response = client.get("/?contract_code=CU2608.SHF#alert-history")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "清空当前合约历史" in page
    assert 'title="仅清空 CU2608.SHF"' in page
    assert 'name="contract_code" value="CU2608.SHF"' in page
    assert "确定要清空 CU2608.SHF 的报警历史记录吗？此操作不可撤销。" in page


def test_clear_alert_history_without_auth_only_deletes_alert_logs(client):
    task = create_task("CU2608")
    original_base_price = task.base_price
    original_active = task.is_active
    create_alert(code="CU2608", message="CLEAR-ONE")
    create_alert(code="Y2609", message="CLEAR-TWO")
    db.session.commit()

    response = client.post("/alerts/clear", follow_redirects=True)
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "已清空全部报警历史记录" in page
    assert AlertLog.query.count() == 0
    assert MonitorTask.query.count() == 1
    assert task.base_price == original_base_price
    assert task.is_active == original_active
    assert "CLEAR-ONE" not in page


def test_clear_alert_history_redirects_back_to_alert_history_anchor(client):
    create_alert(code="CU2608.SHF", message="CLEAR-ANCHOR")
    db.session.commit()

    response = client.post("/alerts/clear")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/#alert-history")


def test_clear_alert_history_for_single_contract_preserves_other_alerts_and_settings(client):
    task = create_task("CU2608.SHF")
    original_base_price = task.base_price
    original_active = task.is_active
    set_monitor_poll_interval_seconds(120)
    create_alert(code="CU2608.SHF", message="CLEAR-CU-ONE")
    create_alert(code="CU2608.SHF", message="CLEAR-CU-TWO")
    create_alert(code="LC2609.GFE", message="KEEP-LC")
    create_alert(code="Y2609.DCE", message="KEEP-Y")
    db.session.commit()

    response = client.post(
        "/alerts/clear",
        data={"contract_code": " cu2608.shf "},
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "已清空 CU2608.SHF 的报警历史记录，共删除 2 条。" in page
    assert AlertLog.query.filter_by(code="CU2608.SHF").count() == 0
    assert AlertLog.query.filter_by(code="LC2609.GFE").count() == 1
    assert AlertLog.query.filter_by(code="Y2609.DCE").count() == 1
    assert MonitorTask.query.count() == 1
    assert task.base_price == original_base_price
    assert task.is_active == original_active
    assert get_monitor_poll_interval_seconds() == 120


def test_default_alert_history_shows_twenty_items(client):
    create_numbered_alerts(25)

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="alert-history"' in page
    assert "共 25 条报警，每页 20 条" in page
    assert "下一页" in page
    assert 'href="/?alert_page=2&amp;alert_per_page=20#alert-history"' in page
    assert "ALERT-24" in page
    assert "ALERT-05" in page
    assert "ALERT-04" not in page
    assert_safe_text(page)


def test_second_alert_history_page_shows_older_items(client):
    create_numbered_alerts(25)

    response = client.get("/?alert_page=2")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前页 2 / 2" in page
    assert "上一页" in page
    assert 'href="/?alert_page=1&amp;alert_per_page=20#alert-history"' in page
    assert "ALERT-04" in page
    assert "ALERT-00" in page
    assert "ALERT-24" not in page


def test_alert_per_page_ten(client):
    create_numbered_alerts(25)

    response = client.get("/?alert_per_page=10")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "共 25 条报警，每页 10 条" in page
    assert "当前页 1 / 3" in page
    assert "ALERT-24" in page
    assert "ALERT-15" in page
    assert "ALERT-14" not in page


def test_invalid_alert_pagination_params_fall_back_safely(client):
    create_numbered_alerts(25)

    response = client.get("/?alert_page=abc&alert_per_page=9999")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前页 1 / 2" in page
    assert "每页 20 条" in page

    response = client.get("/?alert_page=-1&alert_per_page=abc")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "当前页 1 / 2" in page
    assert "每页 20 条" in page


def test_filter_by_contract_code(client):
    create_alert(code="CU2608", message="CU-ONLY")
    create_alert(code="AU2608", message="AU-ONLY")
    db.session.commit()

    response = client.get("/?contract_code=CU2608")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "CU-ONLY" in page
    assert "AU-ONLY" not in page
    assert "合约 CU2608｜" in page


def test_task_rows_have_view_alert_links(client):
    create_task("CU2608")
    create_task("Y2609")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "查看报警" in page
    assert 'href="/?contract_code=CU2608#alert-history"' in page
    assert 'href="/?contract_code=Y2609#alert-history"' in page
    assert_safe_text(page)


def test_alert_history_quick_contract_filters_are_visible(client):
    create_task("CU2608")
    create_alert(code="Y2609", message="Y-QUICK")
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "快捷合约：" in page
    assert "全部" in page
    assert "CU2608" in page
    assert "Y2609" in page
    assert 'href="/?contract_code=CU2608#alert-history"' in page
    assert 'href="/?contract_code=Y2609#alert-history"' in page
    assert 'data-alert-history-jump' in page
    assert_safe_text(page)


def test_alert_history_anchor_jump_uses_instant_scroll_hooks(client):
    create_task("CU2608")
    create_alert(code="Y2609", message="Y-QUICK")
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)
    css = open("app/static/css/app.css", encoding="utf-8").read()
    js = open("app/static/js/app.js", encoding="utf-8").read()

    assert response.status_code == 200
    assert 'href="/?contract_code=CU2608#alert-history"' in page
    assert 'href="/?contract_code=Y2609#alert-history"' in page
    assert 'action="/#alert-history"' in page
    assert 'data-alert-history-jump' in page
    assert "no-smooth-scroll" in css
    assert "initAlertHistoryHashJump" in js
    assert 'window.location.hash === "#alert-history"' in js
    assert "hasDashboardAutoRefreshFlag" in js
    assert "restoreDashboardScrollAfterRefresh" in js
    assert "refreshDashboardWithoutHash" in js
    assert "window.location.pathname" in js
    assert "window.location.search" in js
    assert 'behavior: "auto"' in js
    assert "scrollTo" in js


def test_contract_filter_summary_is_explicit(client):
    create_alert(code="CU2608", message="CU-SUMMARY")
    db.session.commit()

    response = client.get("/?contract_code=CU2608")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前筛选：" in page
    assert "合约 CU2608｜" in page
    assert "方向 全部｜" in page
    assert "类型 全部｜" in page
    assert "日期 全部" in page


def test_contract_filter_is_accurate_for_multiple_contracts(client):
    create_alert(code="CU2608", message="CU-ACCURATE")
    create_alert(code="Y2609", message="Y-ACCURATE")
    db.session.commit()

    response = client.get("/?contract_code=CU2608")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "CU-ACCURATE" in page
    assert "Y-ACCURATE" not in page

    response = client.get("/?contract_code=Y2609")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Y-ACCURATE" in page
    assert "CU-ACCURATE" not in page


def test_contract_filter_normalizes_case_and_spaces(client):
    create_alert(code="CU2608", message="CU-NORMALIZED")
    create_alert(code="Y2609", message="Y-NORMALIZED")
    db.session.commit()

    response = client.get("/?contract_code=cu2608")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "CU-NORMALIZED" in page
    assert "Y-NORMALIZED" not in page

    response = client.get("/?contract_code=%20CU2608%20")
    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "CU-NORMALIZED" in page
    assert "Y-NORMALIZED" not in page


def test_filter_by_direction_up(client):
    create_alert(direction="UP", message="UP-ONLY")
    create_alert(direction="DOWN", message="DOWN-ONLY")
    db.session.commit()

    response = client.get("/?direction=up")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "UP-ONLY" in page
    assert "DOWN-ONLY" not in page
    assert "方向 上涨" in page


def test_filter_by_direction_chinese(client):
    create_alert(direction="UP", message="UP-CN")
    create_alert(direction="DOWN", message="DOWN-CN")
    db.session.commit()

    response = client.get("/?direction=下跌")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "DOWN-CN" in page
    assert "UP-CN" not in page


def test_filter_alert_type_severe(client):
    create_alert(grid_steps=1, message="NORMAL-GRID")
    create_alert(grid_steps=2, message="SEVERE-GRID")
    db.session.commit()

    response = client.get("/?alert_type=severe")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "SEVERE-GRID" in page
    assert "NORMAL-GRID" not in page


def test_filter_alert_type_burst(client):
    create_alert(message="普通报警")
    create_alert(message="高频报警：最近 5 分钟内该合约已触发 3 次报警")
    db.session.commit()

    response = client.get("/?alert_type=burst")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "高频报警" in page
    assert "普通报警" not in page


def test_date_filters(client):
    create_alert(created_at=datetime(2026, 6, 1, 21, 0, 0), message="DATE-OLD")
    create_alert(created_at=datetime(2026, 6, 15, 21, 0, 0), message="DATE-MID")
    create_alert(created_at=datetime(2026, 6, 30, 21, 0, 0), message="DATE-NEW")
    db.session.commit()

    response = client.get("/?date_from=2026-06-10&date_to=2026-06-20")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "DATE-MID" in page
    assert "DATE-OLD" not in page
    assert "DATE-NEW" not in page


def test_filter_and_pagination_preserve_query_params(client):
    create_numbered_alerts(25, code="CU2608")
    create_numbered_alerts(5, code="AU2608")

    response = client.get("/?contract_code=CU2608&alert_per_page=10&alert_page=2")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "当前页 2 / 3" in page
    assert "contract_code=CU2608" in page
    assert "alert_per_page=10" in page
    assert "ALERT-14" in page


def test_contract_filter_pagination_keeps_anchor_and_contract(client):
    create_numbered_alerts(25, code="CU2608")
    create_numbered_alerts(5, code="Y2609")

    response = client.get("/?contract_code=CU2608&alert_page=1&alert_per_page=10")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/?alert_page=2&amp;alert_per_page=10&amp;contract_code=CU2608#alert-history"' in page
    assert 'data-alert-history-jump' in page


def test_clear_filter_and_empty_result(client):
    create_alert(code="CU2608", message="CU-EXISTS")
    db.session.commit()

    response = client.get("/?contract_code=NOTFOUND")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "清除筛选" in page
    assert 'action="/#alert-history"' in page
    assert 'href="/#alert-history"' in page
    assert "暂无符合条件的报警记录" in page
    assert "CU-EXISTS" not in page


def test_alert_history_keeps_notification_preview_and_copy_button(client):
    create_alert(message="PREVIEW-COPY")
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "通知预览" in page
    assert "alert-history-table" in page
    assert "alert-details-row" in page
    assert "报警描述" in page
    assert "查看详情" in page
    assert "alert-preview-open-btn" in page
    assert "notification-preview-dialog" in page
    assert "notification-preview-dialog-footer" in page
    assert "查看通知预览" in page
    assert "复制通知" in page
    assert "notificationPreview" in page


def test_alert_export_csv_route_headers_and_columns(client):
    create_alert(message="CSV-HEADER")
    db.session.commit()

    response = client.get("/alerts/export.csv")
    text = csv_text(response)

    assert response.status_code == 200
    assert "text/csv" in response.headers["Content-Type"]
    assert "attachment" in response.headers["Content-Disposition"]
    assert "alert_history" in response.headers["Content-Disposition"]
    for column in [
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
    ]:
        assert column in text.splitlines()[0]
    assert_safe_text(text)


def test_alert_export_csv_exports_all_filtered_records_without_pagination_limit(client):
    create_numbered_alerts(25)

    response = client.get("/alerts/export.csv")
    rows = csv_rows(response)
    text = csv_text(response)

    assert response.status_code == 200
    assert len(rows) == 25
    assert "ALERT-24" in text
    assert "ALERT-00" in text


def test_alert_export_csv_filters_by_contract_code(client):
    create_alert(code="CU2608", message="CSV-CU")
    create_alert(code="AU2608", message="CSV-AU")
    db.session.commit()

    response = client.get("/alerts/export.csv?contract_code=CU2608")
    text = csv_text(response)

    assert response.status_code == 200
    assert "CSV-CU" in text
    assert "CSV-AU" not in text


def test_alert_export_csv_filters_by_direction(client):
    create_alert(direction="UP", message="CSV-UP")
    create_alert(direction="DOWN", message="CSV-DOWN")
    db.session.commit()

    response = client.get("/alerts/export.csv?direction=up")
    text = csv_text(response)

    assert response.status_code == 200
    assert "CSV-UP" in text
    assert "CSV-DOWN" not in text

    response = client.get("/alerts/export.csv?direction=下跌")
    text = csv_text(response)
    assert "CSV-DOWN" in text
    assert "CSV-UP" not in text


def test_alert_export_csv_filters_severe_and_burst(client):
    create_alert(grid_steps=1, message="CSV-NORMAL")
    create_alert(grid_steps=2, message="CSV-SEVERE")
    create_alert(message="高频报警：CSV-BURST")
    db.session.commit()

    severe_response = client.get("/alerts/export.csv?alert_type=severe")
    severe_text = csv_text(severe_response)
    assert severe_response.status_code == 200
    assert "CSV-SEVERE" in severe_text
    assert "CSV-NORMAL" not in severe_text

    burst_response = client.get("/alerts/export.csv?alert_type=burst")
    burst_text = csv_text(burst_response)
    assert burst_response.status_code == 200
    assert "CSV-BURST" in burst_text
    assert "CSV-NORMAL" not in burst_text


def test_alert_export_csv_filters_date_range(client):
    create_alert(created_at=datetime(2026, 6, 1, 21, 0, 0), message="CSV-DATE-OLD")
    create_alert(created_at=datetime(2026, 6, 15, 21, 0, 0), message="CSV-DATE-MID")
    create_alert(created_at=datetime(2026, 6, 30, 21, 0, 0), message="CSV-DATE-NEW")
    db.session.commit()

    response = client.get("/alerts/export.csv?date_from=2026-06-10&date_to=2026-06-20")
    text = csv_text(response)

    assert response.status_code == 200
    assert "CSV-DATE-MID" in text
    assert "CSV-DATE-OLD" not in text
    assert "CSV-DATE-NEW" not in text


def test_alert_export_csv_empty_result_and_invalid_params_are_safe(client):
    create_alert(code="CU2608", message="CSV-EXISTS")
    db.session.commit()

    empty_response = client.get("/alerts/export.csv?contract_code=NOTFOUND")
    assert empty_response.status_code == 200
    assert len(csv_rows(empty_response)) == 0
    assert "CSV-EXISTS" not in csv_text(empty_response)

    invalid_response = client.get(
        "/alerts/export.csv?date_from=bad&date_to=bad&direction=bad&alert_type=bad"
    )
    invalid_text = csv_text(invalid_response)
    assert invalid_response.status_code == 200
    assert "CSV-EXISTS" in invalid_text
    assert_safe_text(invalid_text)


def test_dashboard_export_csv_link_preserves_filters_without_pagination(client):
    create_numbered_alerts(25, code="CU2608")

    response = client.get("/?contract_code=CU2608&direction=up&alert_page=2&alert_per_page=10")
    page = response.get_data(as_text=True)
    match = re.search(r'href="([^"]*alerts/export\.csv[^"]*)"', page)

    assert response.status_code == 200
    assert "导出 CSV" in page
    assert match is not None

    export_href = html.unescape(match.group(1))
    assert "contract_code=CU2608" in export_href
    assert "direction=up" in export_href
    assert "alert_page" not in export_href
    assert "alert_per_page" not in export_href
    assert 'id="alert-history"' in page
    assert "复制通知" in page
    assert_safe_text(page)


def test_contract_filtered_csv_export_excludes_other_contracts(client):
    create_alert(code="CU2608", message="CSV-CU-ONLY")
    create_alert(code="Y2609", message="CSV-Y-EXCLUDED")
    db.session.commit()

    response = client.get("/alerts/export.csv?contract_code=CU2608")
    text = csv_text(response)

    assert response.status_code == 200
    assert "CU2608" in text
    assert "CSV-CU-ONLY" in text
    assert "Y2609" not in text
    assert "CSV-Y-EXCLUDED" not in text
    assert_safe_text(text)


def test_dashboard_hierarchy_stays_optimized(client):
    create_alert(message="HIERARCHY")
    db.session.commit()

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert page.index("dashboard-primary-tasks") < page.index("报警历史")
    assert page.index("报警历史") < page.index("runtime-diagnostics-panel")
    assert 'id="alert-history"' in page
    assert "展开运行诊断详情" in page
    assert "展开全部检查" in page


def test_healthz_still_works_with_alert_history_count(client):
    create_alert(message="HEALTHZ-COUNT")
    db.session.commit()

    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert "preflight_overall_status" in data
    assert "last_poll_status" in data
    assert data["alert_history_total_count"] == 1
