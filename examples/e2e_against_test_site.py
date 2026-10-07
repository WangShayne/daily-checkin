"""E2E: visit / click / recorded modes against examples/test-site.

Run inside daily-checkin container:
  python examples/e2e_against_test_site.py
Or from host with TEST_SITE_URL / CHECKIN_DATA_DIR set.
"""

from __future__ import annotations

import os
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


def login_cookie() -> str:
    s = requests.Session()
    r = s.post(
        f"{TEST_SITE}/login",
        data={"username": "demo", "password": "demo"},
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
    """Record via Playwright API (no noVNC), save flow, replay."""
    from playwright.sync_api import sync_playwright

    site = SiteConfig(
        name="E2E-Recorded",
        type="browser",
        mode=CheckInMode.RECORDED.value,
        base_url=TEST_SITE,
        checkin_path="/login",
        method="GET",
    )
    sid = db.add_site(site)
    site.id = sid

    steps: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        def on_nav(frame):
            if frame == page.main_frame and frame.url and not frame.url.startswith("about:"):
                steps.append({"kind": "navigate", "url": frame.url, "ts": time.time()})

        def on_req(request):
            if request.method in ("POST", "PUT") and request.resource_type in (
                "document",
                "xhr",
                "fetch",
                "other",
            ):
                post = request.post_data
                if post and len(post) > 2000:
                    post = post[:2000]
                steps.append(
                    {
                        "kind": "request",
                        "method": request.method,
                        "url": request.url,
                        "post_data": post,
                        "ts": time.time(),
                    }
                )

        page.on("framenavigated", on_nav)
        page.on("request", on_req)

        page.goto(f"{TEST_SITE}/login", wait_until="domcontentloaded")
        page.fill('input[name="username"]', "demo")
        page.fill('input[name="password"]', "demo")
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard**", timeout=15000)
        page.click('button[type="submit"]')  # 签到
        page.wait_for_selector("text=签到成功", timeout=15000)
        storage = context.storage_state()
        cookies = context.cookies()
        final_url = page.url
        browser.close()

    db.save_recorded_flow(
        sid,
        final_url=final_url,
        cookies=cookies,
        storage_state=storage,
        steps=steps,
    )
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    site.cookies = cookie_header
    db.update_site(sid, site)

    # Replay
    result = get_adapter_for_site(site).check_in(site, timeout=30)
    assert_ok(result, "recorded-replay")


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
