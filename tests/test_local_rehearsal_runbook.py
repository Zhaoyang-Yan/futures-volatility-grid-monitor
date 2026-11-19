from pathlib import Path


RUNBOOK_PATH = Path("docs/local_rehearsal_runbook.md")
SENSITIVE_TERMS = [
    "access_token",
    "refresh_token",
    "Bearer",
    "Authorization",
    "webhook",
    "secret",
    "IFIND_ACCESS_TOKEN",
    "IFIND_REFRESH_TOKEN",
    "FEISHU_WEBHOOK_URL",
    "FEISHU_WEBHOOK_SECRET",
]
RUNTIME_SENSITIVE_TERMS = [
    "secret-access-token",
    "secret-refresh-token",
    "https://open.feishu.cn/open-apis/bot",
    "Bearer ",
    "Authorization:",
    "IFIND_ACCESS_TOKEN=",
    "IFIND_REFRESH_TOKEN=",
    "FEISHU_WEBHOOK_URL=",
    "FEISHU_WEBHOOK_SECRET=",
]


def assert_safe_text(text: str):
    for term in SENSITIVE_TERMS:
        assert term not in text


def assert_runtime_safe_text(text: str):
    for term in RUNTIME_SENSITIVE_TERMS:
        assert term not in text


def test_local_rehearsal_runbook_exists_and_has_required_steps():
    assert RUNBOOK_PATH.exists()
    text = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert "本地真实短时演练" in text
    assert "MARKET_DATA_MODE=ifind" in text
    assert "NOTIFICATION_MODE=feishu" in text
    assert "测试 iFind 行情" in text
    assert "测试飞书通知" in text
    assert "Control + C" in text
    assert "5-10 分钟" in text
    assert "/healthz" in text
    assert "AlertLog" in text
    assert_safe_text(text)


def test_dashboard_links_to_local_rehearsal_runbook(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "查看运行建议与检查顺序" in page
    assert "本地真实演练手册已保存到" in page
    assert "docs/local_rehearsal_runbook.md" in page
    assert "测试 iFind 行情" in page
    assert "测试飞书通知" in page or "预览测试通知" in page
    assert "报警历史" in page
    assert_runtime_safe_text(page)


def test_healthz_still_available_after_runbook_entry(client):
    response = client.get("/healthz")
    data = response.get_json()

    assert response.status_code == 200
    assert "rehearsal_status" in data
    assert "live_mode_status" in data
    assert "last_poll_status" in data
    assert_runtime_safe_text(response.get_data(as_text=True))
