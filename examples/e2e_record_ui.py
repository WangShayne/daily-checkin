"""Browser E2E for the recording UI (noVNC over /ws/vnc).

Logs into the UI, starts a recording, waits for noVNC to connect (clicks the
noVNC 连接/Connect button if needed), drives the remote Chromium via the VNC
canvas (keyboard), clicks 完成录制 and checks the flow was saved.

  UI_URL=http://192.168.x.x:4567 TARGET_URL=http://host.docker.internal:5001 \
    python examples/e2e_record_ui.py
"""

from __future__ import annotations

import os
import re
import sys
import time

from playwright.sync_api import Page, sync_playwright

UI = os.environ.get("UI_URL", "http://127.0.0.1:4567").rstrip("/")
TARGET = os.environ.get("TARGET_URL", "http://host.docker.internal:5001").rstrip("/")
USER = os.environ.get("CHECKIN_USER", "admin")
PASSWORD = os.environ.get("CHECKIN_PASSWORD", "changeme")
SHOT = os.environ.get("SHOT", "/tmp/record-ui.png")


def novnc_state(page: Page) -> str:
    frame = page.frame(url=re.compile(r"/novnc/vnc\.html"))
    if not frame:
        return "no-frame"
    return frame.evaluate("document.documentElement.className")


def record_via_ui(page: Page, ws_urls: list[str], start_path: str = "/login",
                  name_prefix: str = "E2E-noVNC") -> int:
    """Login → quick-site → noVNC → log in + check in remotely → 完成录制. Returns site id."""
    page.goto(f"{UI}/login")
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(r"/$"))
    print("[1] UI login ok")

    page.goto(f"{UI}/record")
    name = f"{name_prefix}-{int(time.time())}"
    quick = page.locator('form[action="/record/quick-site"]')
    quick.locator('input[name="name"]').fill(name)
    quick.locator('input[name="base_url"]').fill(TARGET)
    quick.locator('input[name="checkin_path"]').fill(start_path)
    quick.locator('button[type="submit"]').click()
    page.wait_for_url(re.compile(r"/record/session"), timeout=90000)
    print("[2] recording session page opened")

    # Wait for noVNC; click its connect button like the user does
    deadline = time.time() + 45
    clicked = False
    state = ""
    while time.time() < deadline:
        state = novnc_state(page)
        if "noVNC_connected" in state:
            break
        frame = page.frame(url=re.compile(r"/novnc/vnc\.html"))
        if frame and not clicked and time.time() > deadline - 40:
            btn = frame.locator("#noVNC_connect_button")
            if btn.is_visible():
                btn.click()
                clicked = True
                print("    clicked noVNC 连接 button")
        time.sleep(0.5)
    try:
        page.wait_for_function(
            "document.getElementById('vnc-status').textContent.includes('已连接')",
            timeout=10000,
        )
    except Exception:  # noqa: BLE001
        pass
    status_text = page.locator("#vnc-status").inner_text()
    print(f"[3] noVNC state={state!r} status={status_text!r} ws={ws_urls}")
    if "noVNC_connected" not in state:
        print(page.locator("#vnc-diag-body").inner_text())
        page.screenshot(path=SHOT)
        raise AssertionError("noVNC did not connect")
    expected_ws = UI.replace("http://", "ws://").replace("https://", "wss://") + "/ws/vnc"
    assert expected_ws in ws_urls, f"websocket not built from page origin: {ws_urls}"

    frame = page.frame(url=re.compile(r"/novnc/vnc\.html"))
    canvas = frame.locator("#noVNC_container canvas")
    canvas.wait_for(state="visible", timeout=10000)
    box = canvas.bounding_box()
    assert box and box["width"] > 100 and box["height"] > 100, box
    print(f"[4] canvas visible {int(box['width'])}x{int(box['height'])}")

    # Drive remote Chromium via VNC: focus page, Tab to username, Enter submits
    time.sleep(1.5)
    canvas.click(position={"x": box["width"] * 0.5, "y": box["height"] * 0.8})
    frame.page.keyboard.press("Tab")
    frame.page.keyboard.press("Enter")
    time.sleep(3)
    # Dashboard: first focusable is the 签到 button
    canvas.click(position={"x": box["width"] * 0.5, "y": box["height"] * 0.8})
    frame.page.keyboard.press("Tab")
    frame.page.keyboard.press("Enter")
    time.sleep(3)
    st = page.evaluate("fetch('/api/record/status').then(r => r.json())")
    print(f"[5] remote browser url={st.get('current_url')} steps={st.get('step_count')}")
    if "/checkin" not in (st.get("current_url") or ""):
        print("    WARN: remote check-in via VNC keyboard not detected")
    page.screenshot(path=SHOT)

    page.locator('form[action="/record/finish"] button').click()
    page.wait_for_url(re.compile(r"/record\?"), timeout=60000)
    print(f"[6] finish -> {page.url}")
    assert "ok=1" in page.url, page.url
    m = re.search(r"site_id=(\d+)", page.url)
    return int(m.group(1)) if m else -1


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("dialog", lambda d: d.accept())
        ws_urls: list[str] = []
        page.on("websocket", lambda ws: ws_urls.append(ws.url))
        try:
            record_via_ui(page, ws_urls)
        except AssertionError as exc:
            print(f"FAILED: {exc}")
            return 1
        finally:
            browser.close()
        print("RECORD UI E2E PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
