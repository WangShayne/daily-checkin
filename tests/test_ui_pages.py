"""Smoke-render every UI page with seeded data (catches template errors)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from checkin.models import CheckInResult, CheckInStatus, SiteConfig


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHECKIN_USER", "admin")
    monkeypatch.setenv("CHECKIN_PASSWORD", "changeme")
    monkeypatch.setenv("CHECKIN_SESSION_SECRET", "test-secret-key")
    from checkin.web import app as app_module

    application = app_module.create_app()
    with TestClient(application) as c:
        c.post("/login", data={"username": "admin", "password": "changeme", "next": "/"})
        yield c


def _seed() -> dict[str, int]:
    from checkin.db import get_db

    db = get_db()
    a = db.add_site(SiteConfig(name="论坛A", type="forum", mode="click", base_url="https://a.invalid",
                               cookies="k=v", form_data={"x": 1}, success_keywords=["ok"]))
    b = db.add_site(SiteConfig(name="门户B", type="portal", mode="visit", base_url="https://b.invalid",
                               schedule_type="cron", cron="30 8 * * *"))
    r = db.add_site(SiteConfig(name="录制C", type="browser", mode="recorded",
                               base_url="https://c.invalid", enabled=False))
    db.save_recorded_flow(r, final_url="https://c.invalid/x", cookies=[], storage_state={},
                          steps=[{"type": "goto"}])
    db.add_run_log(CheckInResult("论坛A", CheckInStatus.SUCCESS, "签到成功", 200, a, "click"),
                   triggered_by="manual")
    db.add_run_log(CheckInResult("门户B", CheckInStatus.FAILED, "timeout", None, b, "visit"))
    return {"a": a, "b": b, "r": r}


def test_all_pages_render(client: TestClient) -> None:
    ids = _seed()
    pages = {
        "/": ["站点列表", "论坛A", "badge-success", "badge-failed", "已录制 1 步", "默认密码"],
        "/sites/new": ['name="mode" value="visit"', 'value="recorded"', "录制回放"],
        f"/sites/{ids['a']}/edit": ["论坛A", 'value="k=v"'],
        "/logs": ["运行日志", "签到成功", "timeout"],
        "/logs?status=failed": ["timeout"],
        "/settings": ["notify_webhook_url", "continue_on_error"],
        f"/record?site_id={ids['r']}": ['action="/record/start"', 'action="/record/quick-site"'],
        "/record/session": ["当前没有活动的录制会话"],
    }
    for path, needles in pages.items():
        r = client.get(path)
        assert r.status_code == 200, path
        for n in needles:
            assert n in r.text, f"{path}: missing {n!r}"


def test_empty_states(client: TestClient) -> None:
    assert "还没有站点" in client.get("/").text
    assert "暂无运行记录" in client.get("/logs").text


def test_flash_toast_after_settings_save(client: TestClient) -> None:
    r = client.post("/settings", data={"timeout": "30", "delay_between_sites": "1"})
    assert r.status_code == 200
    assert "toast" in r.text and "设置已保存" in r.text


def test_static_assets_local(client: TestClient) -> None:
    html = client.get("/").text
    assert "unpkg.com" not in html and "cdn." not in html
    assert client.get("/static/style.css").status_code == 200
