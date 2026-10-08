"""Capture README screenshots from a running daily-checkin instance.

Seeds demo sites (against examples/test-site), records one site through the
real noVNC UI, runs check-ins and saves PNGs to docs/screenshots/.

Intended for a throwaway demo container (fresh /data volume), e.g.:

  docker run -d --name checkin-demo -p 4568:4567 --shm-size 256m \
    --add-host forum.demo.lan:host-gateway --add-host portal.demo.lan:host-gateway \
    --add-host club.demo.lan:host-gateway --add-host mall.demo.lan:host-gateway \
    -e CHECKIN_PASSWORD=demo-2026 -v checkin-demo-data:/data daily-checkin-checkin
  docker run --rm --network host -v $PWD:/repo -e UI_URL=http://127.0.0.1:4568 \
    -e CHECKIN_PASSWORD=demo-2026 daily-checkin-checkin python /repo/docs/capture_screenshots.py
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import Page, sync_playwright

UI = os.environ.get("UI_URL", "http://127.0.0.1:4568").rstrip("/")
TEST_SITE_LOCAL = os.environ.get("TEST_SITE_LOCAL", "http://127.0.0.1:5001")
USER = os.environ.get("CHECKIN_USER", "admin")
PASSWORD = os.environ.get("CHECKIN_PASSWORD", "changeme")
OUT = Path(os.environ.get("OUT_DIR", Path(__file__).parent / "screenshots"))
W, H = 1440, 900


def test_site_cookie(checked_in: bool = False) -> str:
    s = requests.Session()
    s.post(f"{TEST_SITE_LOCAL}/login", data={"username": "demo", "password": "demo"}, timeout=10)
    if checked_in:
        s.post(f"{TEST_SITE_LOCAL}/checkin", timeout=10)
    return "; ".join(f"{c.name}={c.value}" for c in s.cookies)


def shot(page: Page, name: str, full: bool = False) -> None:
    page.wait_for_timeout(400)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=full)
    print("  saved", path)


def add_site(page: Page, **fields: str) -> None:
    data = {
        "type": "forum", "mode": "click", "enabled": "1", "checkin_path": "", "method": "POST",
        "cookies": "", "headers_json": "", "form_data_json": "", "body_json": "",
        "success_keywords": "", "success_status": "[200]", "schedule_type": "daily",
        "daily_time": "09:00", "cron": "0 9 * * *",
    }
    data.update(fields)
    if data.get("enabled") == "":
        data.pop("enabled")
    r = page.request.post(f"{UI}/sites", form=data, max_redirects=0)
    assert r.status in (302, 303), (r.status, r.text()[:300])


def novnc_connected(page: Page) -> bool:
    frame = page.frame(url=re.compile(r"/novnc/vnc\.html"))
    return bool(frame) and "noVNC_connected" in frame.evaluate("document.documentElement.className")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                  locale="zh-CN", color_scheme="light")
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())

        print("[login]")
        page.goto(f"{UI}/login")
        shot(page, "login")
        page.fill('input[name="username"]', USER)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(re.compile(r"/$"))

        print("[empty dashboard]")
        shot(page, "dashboard-empty")

        print("[seed sites]")
        add_site(page, name="示例论坛", type="forum", mode="click",
                 base_url="http://forum.demo.lan:5001", checkin_path="/checkin",
                 cookies=test_site_cookie(), success_keywords="签到成功, 已签到", daily_time="08:30")
        add_site(page, name="资讯门户", type="portal", mode="visit", method="GET",
                 base_url="http://portal.demo.lan:5001", checkin_path="/visit",
                 cookies=test_site_cookie(), success_keywords="访问成功, 已记为", daily_time="09:00")
        add_site(page, name="积分商城", type="http_form", mode="click",
                 base_url="http://mall.demo.lan:5001", checkin_path="/checkin",
                 cookies=test_site_cookie(checked_in=True), daily_time="09:15")
        add_site(page, name="公司 OA 考勤", type="portal", mode="click",
                 base_url="https://oa.example.invalid", checkin_path="/api/attendance/sign",
                 body_json='{"action": "sign"}', daily_time="08:55")
        add_site(page, name="每周福利", type="portal", mode="visit", method="GET", enabled="",
                 base_url="https://weekly.example.invalid", checkin_path="/bonus",
                 schedule_type="cron", cron="0 10 * * 1")

        print("[record page]")
        page.goto(f"{UI}/settings")  # consume queued flash toasts from seeding
        page.goto(f"{UI}/record")
        shot(page, "record")

        print("[live recording session]")
        quick = page.locator('form[action="/record/quick-site"]')
        quick.locator('input[name="name"]').fill("社区（录制回放）")
        quick.locator('input[name="base_url"]').fill("http://club.demo.lan:5001")
        quick.locator('input[name="checkin_path"]').fill("/login")
        quick.locator('button[type="submit"]').click()
        page.wait_for_url(re.compile(r"/record/session"), timeout=90000)
        deadline = time.time() + 45
        while time.time() < deadline and not novnc_connected(page):
            time.sleep(0.5)
        if not novnc_connected(page):
            print("noVNC did not connect")
            shot(page, "record-session-failed")
            return 1
        frame = page.frame(url=re.compile(r"/novnc/vnc\.html"))
        canvas = frame.locator("#noVNC_container canvas")
        canvas.wait_for(state="visible")
        box = canvas.bounding_box()
        time.sleep(1.5)
        canvas.click(position={"x": box["width"] * 0.5, "y": box["height"] * 0.8})
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        time.sleep(3)
        page.wait_for_function("document.getElementById('vnc-status').textContent.includes('已连接')")
        time.sleep(3.5)  # let the status poll refresh step count / url
        page.evaluate("window.scrollTo(0, 0)")
        shot(page, "record-session")
        canvas.click(position={"x": box["width"] * 0.5, "y": box["height"] * 0.8})
        page.keyboard.press("Tab")
        page.keyboard.press("Enter")
        time.sleep(3)
        page.locator('form[action="/record/finish"] button').click()
        page.wait_for_url(re.compile(r"/record\?"), timeout=60000)
        assert "ok=1" in page.url, page.url

        print("[run all]")
        page.goto(f"{UI}/")
        page.locator('form[action="/run"] button').click()
        page.wait_for_url(re.compile(r"/logs"), timeout=180000)
        page.goto(f"{UI}/")
        first = page.locator('form[action$="/run"]').nth(1)  # first site row
        first.locator("button").click()
        page.wait_for_load_state()

        print("[dashboard]")
        page.goto(f"{UI}/")
        shot(page, "dashboard")

        print("[logs]")
        page.goto(f"{UI}/logs")
        shot(page, "logs")

        print("[site form]")
        page.goto(f"{UI}/sites/new")
        page.fill('input[name="name"]', "我的论坛")
        page.fill('input[name="base_url"]', "https://bbs.example.com")
        page.fill('input[name="checkin_path"]', "/plugin.php?id=dsu_paulsign:sign")
        page.set_viewport_size({"width": W, "height": 1480})
        shot(page, "site-form")
        page.set_viewport_size({"width": W, "height": H})

        print("[settings]")
        page.goto(f"{UI}/settings")
        shot(page, "settings")

        print("[toast]")
        page.goto(f"{UI}/")
        page.locator('form[action$="/run"]').nth(2).locator("button").click()
        page.wait_for_selector(".toast", timeout=30000)
        shot(page, "run-toast")

        print("[dark]")
        page.evaluate("localStorage.setItem('theme', 'dark')")
        page.goto(f"{UI}/")
        shot(page, "dashboard-dark")
        page.goto(f"{UI}/sites/new")
        shot(page, "site-form-dark")
        page.evaluate("localStorage.setItem('theme', 'light')")

        print("[mobile]")
        m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                locale="zh-CN", storage_state=ctx.storage_state())
        mp = m.new_page()
        mp.goto(f"{UI}/")
        shot(mp, "mobile-dashboard")
        browser.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
