"""E2E: visit / click / recorded modes against examples/test-site.

Run inside daily-checkin container:
  python examples/e2e_against_test_site.py
Or from host with TEST_SITE_URL / CHECKIN_DATA_DIR set.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import requests

# Ensure src on path when run from /app
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from checkin.adapters import get_adapter_for_site
from checkin.db import Database, init_db
from checkin.models import CheckInMode, CheckInStatus, SiteConfig

TEST_SITE = os.environ.get(
    "TEST_SITE_URL", "http://host.docker.internal:5001"
).rstrip("/")
DATA_DIR = os.environ.get("CHECKIN_DATA_DIR", "/data")


ALICE_PASSWORD = "Wonderland-2026!"  # must never appear in /data or logs


def login_cookie() -> str:
    s = requests.Session()
    page = s.get(f"{TEST_SITE}/login", timeout=15)
    m = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    r = s.post(
        f"{TEST_SITE}/login",
        data={"username": "demo", "password": "demo", "csrf_token": m.group(1) if m else ""},
        allow_redirects=True,
        timeout=15,
    )
    r.raise_for_status()
    if "session" not in s.cookies.get_dict() and not s.cookies:
        raise RuntimeError(f"login failed, status={r.status_code} body={r.text[:200]}")
    return "; ".join(f"{c.name}={c.value}" for c in s.cookies)


def assert_ok(result, label: str) -> None:
    print(f"[{label}] {result.status.value}: {result.message}")
    if result.status not in (
        CheckInStatus.SUCCESS,
        CheckInStatus.ALREADY,
    ):
        raise AssertionError(f"{label} failed: {result.status} {result.message}")


def test_visit(db: Database, cookie: str) -> None:
    site = SiteConfig(
        name="E2E-Visit",
        type="portal",
        mode=CheckInMode.VISIT.value,
        base_url=TEST_SITE,
        checkin_path="/visit",
        method="GET",
        cookies=cookie,
        success_keywords=["访问成功", "签到", "已记为"],
    )
    sid = db.add_site(site)
    site.id = sid
    result = get_adapter_for_site(site).check_in(site, timeout=20)
    assert_ok(result, "visit")


def test_click(db: Database, cookie: str) -> None:
    # Fresh login so we are not already checked in
    cookie2 = login_cookie()
    # Reset by logging in again — new session unchecked. Click checkin.
    site = SiteConfig(
        name="E2E-Click",
        type="http_form",
        mode=CheckInMode.CLICK.value,
        base_url=TEST_SITE,
        checkin_path="/checkin",
        method="POST",
        cookies=cookie2,
        success_keywords=["签到成功", "已签到"],
    )
    sid = db.add_site(site)
    site.id = sid
    result = get_adapter_for_site(site).check_in(site, timeout=20)
    assert_ok(result, "click")


def test_recorded(db: Database) -> None:
    """Record login + check-in like the real recorder, then replay, expire, re-login."""
    from playwright.sync_api import sync_playwright

    from checkin.browser.flow import finalize_recording
    from checkin.browser.recorder import FORM_HOOK_JS

    site = SiteConfig(
        name="E2E-Recorded",
        type="browser",
        mode=CheckInMode.RECORDED.value,
        base_url=TEST_SITE,
        checkin_path="/login?prefill=alice",
        method="GET",
    )
    sid = db.add_site(site)
    site.id = sid

    steps: list[dict] = []
    submits: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(ignore_https_errors=True)
        context.expose_binding(
            "__checkinFormSubmit", lambda _src, info: submits.append({**info, "t": time.monotonic()})
        )
        context.add_init_script(FORM_HOOK_JS)
        page = context.new_page()

        def on_nav(frame):
            if frame == page.main_frame and frame.url and not frame.url.startswith("about:"):
                steps.append({"kind": "navigate", "url": frame.url, "ts": time.time()})

        def on_req(request):
            if request.method in ("POST", "PUT") and request.resource_type in (
                "document", "xhr", "fetch", "other",
            ):
                steps.append(
                    {
                        "kind": "request",
                        "method": request.method,
                        "url": request.url,
                        "resource_type": request.resource_type,
                        "content_type": request.headers.get("content-type", ""),
                        "page_url": request.frame.url,
                        "_raw": request.post_data,
                        "t": time.monotonic(),
                    }
                )

        page.on("framenavigated", on_nav)
        page.on("request", on_req)

        page.goto(f"{TEST_SITE}/login?prefill=alice", wait_until="domcontentloaded")
        page.click('button[type="submit"]')  # login (alice, prefilled) + CSRF token
        page.wait_for_url("**/dashboard**", timeout=15000)
        page.click('button[type="submit"]')  # 签到
        page.wait_for_selector("text=签到成功", timeout=15000)
        storage = context.storage_state()
        cookies = context.cookies()
        final_url = page.url
        browser.close()

    clean_steps, login = finalize_recording(steps, submits)
    assert login, "login step not detected"
    assert login["meta"]["password_fields"] == ["password"], login["meta"]
    assert login["meta"]["username_field"] == "username"
    assert "csrf_token" in login["meta"]["csrf_fields"]
    print(f"[recorded] login detected: url={login['meta']['url']} fields={login['meta']['fields']}")
    db.save_recorded_flow(sid, final_url=final_url, cookies=cookies, storage_state=storage,
                          steps=clean_steps)
    db.save_login_credential(sid, username=login["username"], passwords=login["passwords"],
                             login_step=login["meta"])

    adapter = get_adapter_for_site(site)
    # 1) session still valid → no re-login
    result = adapter.check_in(site, timeout=30)
    assert_ok(result, "recorded-replay")
    assert "重新登录" not in result.message, result.message

    # 2) expire the session server-side → must auto re-login (fresh CSRF) and check in
    requests.post(f"{TEST_SITE}/admin/expire", timeout=10).raise_for_status()
    result = adapter.check_in(site, timeout=30)
    assert_ok(result, "recorded-expired-relogin")
    assert "已自动重新登录" in result.message, result.message
    cred = db.get_login_credential(sid)
    assert cred and cred["last_relogin_ok"] is True, cred

    # 3) refreshed storage_state was saved → next run needs no re-login
    result = adapter.check_in(site, timeout=30)
    assert_ok(result, "recorded-after-relogin")
    assert "重新登录" not in result.message, result.message

    # 4) wrong saved password → clear failure suggesting re-record
    db.save_login_credential(sid, username="alice", passwords={"password": "wrong-pass"},
                             login_step=login["meta"])
    requests.post(f"{TEST_SITE}/admin/expire", timeout=10).raise_for_status()
    result = adapter.check_in(site, timeout=30)
    print(f"[recorded-bad-password] {result.status.value}: {result.message}")
    assert result.status == CheckInStatus.FAILED and "重新录制" in result.message, result.message
    db.save_login_credential(sid, username=login["username"], passwords=login["passwords"],
                             login_step=login["meta"])

    # 5) no plaintext password anywhere in the data dir
    check_no_plaintext()


def check_no_plaintext() -> None:
    leaks = []
    for f in Path(DATA_DIR).rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            if ALICE_PASSWORD.encode() in data:
                leaks.append(str(f))
            if re.search(rb"password[\"']?\s*[=:]\s*[\"']?demo\b", data):
                leaks.append(f"{f} (password=demo)")
    assert not leaks, f"plaintext password found in: {leaks}"
    print(f"[plaintext-check] {ALICE_PASSWORD!r} / password=demo not found under {DATA_DIR}")


def test_ui_login() -> None:
    base = os.environ.get("CHECKIN_UI_URL", "http://127.0.0.1:4567")
    # When run inside container, hit self
    if Path("/.dockerenv").exists():
        base = "http://127.0.0.1:4567"
    s = requests.Session()
    r = s.post(
        f"{base}/login",
        data={"username": "admin", "password": "changeme", "next": "/"},
        allow_redirects=False,
        timeout=15,
    )
    assert r.status_code in (303, 302), r.status_code
    home = s.get(f"{base}/", timeout=15)
    assert home.status_code == 200
    assert "站点" in home.text
    print("[ui-login] ok")


def main() -> int:
    print(f"TEST_SITE={TEST_SITE} DATA_DIR={DATA_DIR}")
    # health
    h = requests.get(f"{TEST_SITE}/health", timeout=10)
    h.raise_for_status()
    print("[health] test-site ok")

    db = init_db(DATA_DIR)
    cookie = login_cookie()
    print("[login] got session cookie")

    test_visit(db, cookie)
    test_click(db, cookie)
    test_recorded(db)
    try:
        test_ui_login()
    except Exception as exc:  # noqa: BLE001
        print(f"[ui-login] skipped/fail: {exc}")

    print("ALL E2E CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
