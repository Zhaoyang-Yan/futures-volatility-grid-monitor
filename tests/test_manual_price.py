from app.db import AlertLog, MonitorTask, db


def test_task_form_shows_ifind_code_help_for_create_and_edit(client, make_task):
    make_task(code="CU2608")

    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "完整 iFind 代码" in page
    assert "CU2608.SHF、Y2609.DCE、LC2609.GFE" in page
    assert "不要填写无后缀代码，例如 CU2608" in page
    assert "合约代码可填写短代码" not in page
    assert page.count("task-code-help") >= 2


def test_task_form_accepts_full_ifind_code_and_uppercases(client):
    response = client.post(
        "/tasks",
        data={
            "code": " y2609.dce ",
            "base_price": "8000",
            "threshold": "100",
            "is_active": "on",
        },
    )

    task = MonitorTask.query.one()
    assert response.status_code == 302
    assert task.code == "Y2609.DCE"


def test_task_form_accepts_cu_full_ifind_code(client):
    response = client.post(
        "/tasks",
        data={
            "code": "CU2608.SHF",
            "base_price": "105600",
            "threshold": "1000",
            "is_active": "on",
        },
    )

    task = MonitorTask.query.one()
    assert response.status_code == 302
    assert task.code == "CU2608.SHF"


def test_task_form_accepts_exchange_suffix_examples(client):
    for code in ["Y2609.DCE", "LC2609.GFE"]:
        response = client.post(
            "/tasks",
            data={
                "code": code,
                "base_price": "8000",
                "threshold": "100",
                "is_active": "on",
            },
        )

        assert response.status_code == 302
        assert MonitorTask.query.filter_by(code=code).count() == 1


def test_task_form_allows_underscore_and_hyphen_before_suffix(client):
    response = client.post(
        "/tasks",
        data={
            "code": "AB_12-CD.SHF",
            "base_price": "100",
            "threshold": "10",
            "is_active": "on",
        },
    )

    task = MonitorTask.query.one()
    assert response.status_code == 302
    assert task.code == "AB_12-CD.SHF"


def test_edit_task_accepts_full_ifind_code(client, make_task):
    task = make_task(code="CU2608")

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "code": "cu2608.shf",
            "base_price": "105600",
            "threshold": "1000",
            "is_active": "on",
        },
    )

    db.session.refresh(task)
    assert response.status_code == 302
    assert task.code == "CU2608.SHF"


def test_task_form_rejects_dangerous_contract_code(client):
    dangerous_codes = [
        "CU2608",
        "Y2609",
        "lc2609",
        "CU2608.",
        ".SHF",
        "CU2608 SHF",
        "Y2609/ABC",
        "Y2609\\ABC",
        "Y2609:ABC",
        "Y2609;ABC",
        'Y2609"ABC',
        "Y2609'ABC",
        "Y2609<ABC",
        "Y2609>ABC",
        "",
        "   ",
    ]

    for code in dangerous_codes:
        response = client.post(
            "/tasks",
            data={
                "code": code,
                "base_price": "8000",
                "threshold": "100",
                "is_active": "on",
            },
            follow_redirects=True,
        )

        page = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "合约代码必须填写完整 iFind 代码，例如 CU2608.SHF、Y2609.DCE、LC2609.GFE。" in page
        assert MonitorTask.query.count() == 0


def test_edit_task_rejects_short_code_and_keeps_original_task(client, make_task):
    task = make_task(code="CU2608.SHF", base_price=105600)
    original_code = task.code
    original_base_price = task.base_price

    response = client.post(
        f"/tasks/{task.id}/edit",
        data={
            "code": "CU2608",
            "base_price": "999999",
            "threshold": "50",
            "is_active": "on",
        },
        follow_redirects=True,
    )
    page = response.get_data(as_text=True)

    db.session.refresh(task)
    assert response.status_code == 200
    assert "合约代码必须填写完整 iFind 代码，例如 CU2608.SHF、Y2609.DCE、LC2609.GFE。" in page
    assert task.code == original_code
    assert task.base_price == original_base_price
    assert AlertLog.query.count() == 0


def test_dashboard_displays_full_ifind_code_and_existing_controls(client, make_task):
    task = make_task(code="CU2608.SHF")

    response = client.get("/")
    page = response.get_data(as_text=True)
    health = client.get("/healthz").get_json()

    db.session.refresh(task)
    assert response.status_code == 200
    assert "CU2608.SHF" in page
    assert "测试 iFind 行情" in page
    assert "最近行情轮询" in page
    assert "导出 CSV" in page
    assert "notification-preview-dialog" in page or "暂无符合条件的报警记录" in page
    assert "last_poll_status" in health
    assert AlertLog.query.count() == 0
    assert task.base_price == 105600


def test_manual_price_inside_grid_does_not_create_alert(client, make_task):
    task = make_task()

    response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "105900"},
    )

    assert response.status_code == 302
    db.session.refresh(task)
    assert task.base_price == 105600
    assert AlertLog.query.count() == 0


def test_manual_price_break_updates_base_and_creates_alert(client, make_task):
    task = make_task()

    response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "106720"},
    )

    assert response.status_code == 302
    db.session.refresh(task)
    assert task.base_price == 106600

    alert = AlertLog.query.one()
    assert alert.direction == "UP"
    assert alert.old_base_price == 105600
    assert alert.new_base_price == 106600
    assert alert.grid_steps == 1
    assert "[手动测试]" in alert.message

    page = client.get("/")
    assert page.status_code == 200


def test_manual_price_rejects_invalid_input(client, make_task):
    task = make_task()

    response = client.post(
        f"/tasks/{task.id}/test-price",
        data={"current_price": "-1"},
    )

    assert response.status_code == 302
    db.session.refresh(task)
    assert task.base_price == 105600
    assert AlertLog.query.count() == 0
