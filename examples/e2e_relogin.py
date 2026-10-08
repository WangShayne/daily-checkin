"""E2E: record login + check-in through the noVNC UI, expire the session, replay.

The replay must detect the expired login, re-login with the encrypted saved
password (the test-site login form has a per-session CSRF token), save the new
session and check in. Also asserts the password never shows up in the UI.

  UI_URL=http://192.168.x.x:4567 TARGET_URL=http://host.docker.internal:5001 \\
  TEST_SITE_LOCAL=http://127.0.0.1:5001 python examples/e2e_relogin.py

Grep the data volume / container logs for the password afterwards, e.g.
  docker exec daily-checkin grep -rc 'Wonderland-2026!' /data
"""

from __future__ import annotations

import html as htmllib
import os
import re
import sys

import requests
from playwright.sync_api import sync_playwright

from e2e_record_ui import UI, record_via_ui

TEST_SITE_LOCAL = os.environ.get("TEST_SITE_LOCAL", "http://127.0.0.1:5001").rstrip("/")
PASSWORD = "Wonderland-2026!"


def latest_log(page, site_id: int) -> tuple[str, str]:
    body = page.request.get(f"{UI}/logs?site_id={site_id}").text()
    row = re.search(r"<tbody>.*?<tr>(.*?)</tr>", body, re.S)
    assert row, "no log row"
    cells = row.group(1)
    status = re.search(r"badge-(success|already|failed|skipped)", cells)
    msg = re.search(r'<td class="msg">(.*?)</td>', cells, re.S)
    return (status.group(1) if status else "?", htmllib.unescape(msg.group(1)).strip() if msg else "")


def run_site(page, site_id: int) -> tuple[str, str]:
    r = page.request.post(f"{UI}/sites/{site_id}/run", max_redirects=0)
    assert r.status in (302, 303), r.status
    return latest_log(page, site_id)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("dialog", lambda d: d.accept())
        ws_urls: list[str] = []
        page.on("websocket", lambda ws: ws_urls.append(ws.url))
        try:
            site_id = record_via_ui(page, ws_urls, start_path="/login?prefill=alice",
                                    name_prefix="E2E-Relogin")
            print(f"[7] recorded site_id={site_id}")
            assert "已识别登录步骤" in page.url or "%E5%B7%B2%E8%AF%86%E5%88%AB" in page.url, page.url

            edit = page.request.get(f"{UI}/sites/{site_id}/edit").text()
            assert "已保存登录凭据（已加密）" in edit, "credential badge missing"
            assert PASSWORD not in edit, "password rendered in site form!"
            for path in ("/", "/logs", "/record", "/api/record/diag", "/api/record/status"):
                assert PASSWORD not in page.request.get(f"{UI}{path}").text(), f"password leaked on {path}"
            print("[8] UI shows 已保存登录凭据（已加密）; password not rendered anywhere")

            status, msg = run_site(page, site_id)
            print(f"[9] replay with valid session: {status} {msg}")
            assert status in ("success", "already") and "重新登录" not in msg

            requests.post(f"{TEST_SITE_LOCAL}/admin/expire", timeout=10).raise_for_status()
            print("[10] test-site sessions expired (POST /admin/expire)")
            status, msg = run_site(page, site_id)
            print(f"[11] replay after expiry: {status} {msg}")
            assert status in ("success", "already"), msg
            assert "登录已过期，已自动重新登录" in msg, msg

            edit = page.request.get(f"{UI}/sites/{site_id}/edit").text()
            assert "最近自动登录" in edit and "badge-success\">成功" in edit, "last re-login not shown"
            status, msg = run_site(page, site_id)
            print(f"[12] next replay (refreshed session saved): {status} {msg}")
            assert status in ("success", "already") and "重新登录" not in msg
            print(f"SITE_ID={site_id}")
        except AssertionError as exc:
            print(f"FAILED: {exc}")
            page.screenshot(path=os.environ.get("SHOT", "/tmp/e2e-relogin.png"))
            return 1
        finally:
            browser.close()
    print("RELOGIN E2E PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
