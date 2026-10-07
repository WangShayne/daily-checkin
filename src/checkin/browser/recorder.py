"""Launch headed Chromium on Xvfb, expose via VNC/noVNC, capture cookies + steps."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")

DISPLAY = os.environ.get("CHECKIN_DISPLAY", ":99")
VNC_PORT = int(os.environ.get("CHECKIN_VNC_PORT", "5900"))
WEBSOCKIFY_PORT = int(os.environ.get("CHECKIN_WEBSOCKIFY_PORT", "6080"))
XVFB_PID_FILE = Path(os.environ.get("CHECKIN_XVFB_PID_FILE", "/tmp/checkin-xvfb.pid"))


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


@dataclass
class ActiveSession:
    site_id: int
    start_url: str
    vnc_password: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    status: str = "starting"  # starting | ready | stopping | error
    error: str = ""
    # runtime handles
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    procs: list[subprocess.Popen[Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)



def _terminate_pid(pid: int, *, name: str = "proc") -> None:
    """Best-effort terminate a process by PID."""
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        logger.debug("无权限结束 %s pid=%s", name, pid)
        return
    time.sleep(0.2)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _kill_stale_display() -> None:
    """Clear a previous Xvfb on DISPLAY without requiring pkill.

    Prefer PID file from last launch; optionally use pkill/killall if present.
    Never raise if the helper binary is missing.
    """
    # 1) PID file from our previous start
    try:
        if XVFB_PID_FILE.exists():
            raw = XVFB_PID_FILE.read_text(encoding="utf-8").strip()
            if raw.isdigit():
                _terminate_pid(int(raw), name="Xvfb")
            XVFB_PID_FILE.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("清理 Xvfb PID 文件失败: %s", exc)

    # 2) Optional pkill / killall (procps / psmisc) — ignore if absent
    pattern = f"Xvfb {DISPLAY}"
    for cmd in (
        ["pkill", "-f", pattern],
        ["killall", "-q", "Xvfb"],
    ):
        binary = shutil.which(cmd[0])
        if not binary:
            continue
        try:
            subprocess.run(
                [binary, *cmd[1:]],
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            logger.debug("%s 不可用，跳过", cmd[0])
        except OSError as exc:
            logger.debug("调用 %s 失败: %s", cmd[0], exc)


class RecordingManager:
    """One interactive recording session at a time (MVP)."""

    def __init__(self) -> None:
        self._session: ActiveSession | None = None
        self._lock = threading.Lock()

    @property
    def active(self) -> ActiveSession | None:
        return self._session

    def status(self) -> dict[str, Any]:
        s = self._session
        if not s:
            return {"active": False}
        return {
            "active": True,
            "site_id": s.site_id,
            "start_url": s.start_url,
            "status": s.status,
            "error": s.error,
            "started_at": s.started_at,
            "step_count": len(s.steps),
            "current_url": self._current_url(s),
            "vnc_password": s.vnc_password,
            "websockify_port": WEBSOCKIFY_PORT,
        }

    def _current_url(self, s: ActiveSession) -> str:
        try:
            if s.page:
                return s.page.url or ""
        except Exception:  # noqa: BLE001
            pass
        return ""

    def start(self, site_id: int, start_url: str) -> dict[str, Any]:
        with self._lock:
            if self._session and self._session.status in ("starting", "ready"):
                raise RuntimeError("已有录制会话进行中，请先完成或取消")
            password = secrets.token_urlsafe(8)
            session = ActiveSession(
                site_id=site_id,
                start_url=start_url,
                vnc_password=password,
            )
            self._session = session

        thread = threading.Thread(
            target=self._boot, args=(session,), daemon=True, name="checkin-recorder"
        )
        thread.start()
        # Wait briefly for ready/error
        for _ in range(60):
            if session.status in ("ready", "error"):
                break
            time.sleep(0.25)
        if session.status == "error":
            raise RuntimeError(session.error or "录制会话启动失败")
        return self.status()

    def _boot(self, session: ActiveSession) -> None:
        try:
            self._start_display_stack(session)
            self._start_playwright(session)
            session.status = "ready"
            logger.info("录制会话就绪 site_id=%s url=%s", session.site_id, session.start_url)
        except Exception as exc:  # noqa: BLE001
            logger.exception("录制启动失败")
            session.error = str(exc)
            session.status = "error"
            self._cleanup_procs(session)

    def _start_display_stack(self, session: ActiveSession) -> None:
        """Xvfb + x11vnc + websockify (best-effort; required in Docker image)."""
        if not shutil.which("Xvfb"):
            raise RuntimeError(
                "未找到 Xvfb。请使用项目 Dockerfile 构建镜像后录制。"
            )
        # Kill stale display if any (PID file + optional pkill)
        _kill_stale_display()
        xvfb = subprocess.Popen(
            [
                "Xvfb",
                DISPLAY,
                "-screen",
                "0",
                "1280x800x24",
                "-ac",
                "+extension",
                "RANDR",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        session.procs.append(xvfb)
        try:
            XVFB_PID_FILE.write_text(str(xvfb.pid), encoding="utf-8")
        except OSError as exc:
            logger.debug("无法写入 Xvfb PID 文件: %s", exc)
        time.sleep(0.4)
        os.environ["DISPLAY"] = DISPLAY

        if shutil.which("x11vnc"):
            # write password file
            passfile = f"/tmp/checkin-vnc-{session.site_id}.pass"
            subprocess.run(
                ["x11vnc", "-storepasswd", session.vnc_password, passfile],
                check=True,
                capture_output=True,
            )
            vnc = subprocess.Popen(
                [
                    "x11vnc",
                    "-display",
                    DISPLAY,
                    "-rfbport",
                    str(VNC_PORT),
                    "-rfbauth",
                    passfile,
                    "-forever",
                    "-shared",
                    "-localhost",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            session.procs.append(vnc)
            time.sleep(0.3)

        ws_bin = shutil.which("websockify")
        if ws_bin:
            ws = subprocess.Popen(
                [
                    ws_bin,
                    "--heartbeat=30",
                    f"127.0.0.1:{WEBSOCKIFY_PORT}",
                    f"127.0.0.1:{VNC_PORT}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            session.procs.append(ws)
            time.sleep(0.2)
        else:
            logger.warning("websockify 未安装，noVNC 嵌入可能不可用")

    def _start_playwright(self, session: ActiveSession) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "未安装 playwright。请在 Docker 镜像中安装依赖。"
            ) from exc

        os.environ["DISPLAY"] = DISPLAY
        pw = sync_playwright().start()
        session.playwright = pw
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1280,800",
                "--disable-gpu",
            ],
        )
        session.browser = browser
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )
        session.context = context
        page = context.new_page()
        session.page = page

        def on_nav(frame: Any) -> None:
            if frame != page.main_frame:
                return
            url = frame.url
            if not url or url.startswith("about:"):
                return
            with session._lock:
                session.steps.append(
                    {"kind": "navigate", "url": url, "ts": _now()}
                )

        def on_request(request: Any) -> None:
            try:
                if request.method not in ("POST", "PUT"):
                    return
                if request.resource_type not in ("document", "xhr", "fetch", "other"):
                    return
                post = request.post_data
                if post and len(post) > 2000:
                    post = post[:2000] + "…"
                with session._lock:
                    session.steps.append(
                        {
                            "kind": "request",
                            "method": request.method,
                            "url": request.url,
                            "resource_type": request.resource_type,
                            "post_data": post,
                            "ts": _now(),
                        }
                    )
            except Exception:  # noqa: BLE001
                pass

        page.on("framenavigated", on_nav)
        page.on("request", on_request)
        page.goto(session.start_url, wait_until="domcontentloaded", timeout=60000)
        with session._lock:
            session.steps.append(
                {"kind": "navigate", "url": page.url, "ts": _now()}
            )

    def finish(self) -> dict[str, Any]:
        with self._lock:
            session = self._session
            if not session:
                raise RuntimeError("没有进行中的录制会话")
            if session.status not in ("ready", "error"):
                raise RuntimeError(f"会话状态不可完成: {session.status}")

        session.status = "stopping"
        try:
            cookies: list[dict[str, Any]] = []
            storage_state: dict[str, Any] = {}
            final_url = self._current_url(session)
            if session.context:
                cookies = session.context.cookies()
                storage_state = session.context.storage_state()
            with session._lock:
                steps = list(session.steps)
            # dedupe consecutive identical navigations
            cleaned: list[dict[str, Any]] = []
            for step in steps:
                if (
                    cleaned
                    and step.get("kind") == "navigate"
                    and cleaned[-1].get("kind") == "navigate"
                    and cleaned[-1].get("url") == step.get("url")
                ):
                    continue
                cleaned.append(step)
            cookie_header = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies if "name" in c and "value" in c
            )
            payload = {
                "site_id": session.site_id,
                "final_url": final_url,
                "cookies": cookies,
                "cookie_header": cookie_header,
                "storage_state": storage_state,
                "steps": cleaned,
            }
            return payload
        finally:
            self._shutdown_session(session)

    def cancel(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
        if session:
            self._shutdown_session(session)

    def _shutdown_session(self, session: ActiveSession) -> None:
        try:
            if session.context:
                session.context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if session.browser:
                session.browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if session.playwright:
                session.playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._cleanup_procs(session)
        with self._lock:
            if self._session is session:
                self._session = None

    def _cleanup_procs(self, session: ActiveSession) -> None:
        for proc in reversed(session.procs):
            try:
                if proc.poll() is None:
                    proc.send_signal(signal.SIGTERM)
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.2)
        for proc in reversed(session.procs):
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:  # noqa: BLE001
                pass
        session.procs.clear()
        try:
            XVFB_PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass


_manager: RecordingManager | None = None


def get_recording_manager() -> RecordingManager:
    global _manager
    if _manager is None:
        _manager = RecordingManager()
    return _manager


def build_start_url(base_url: str, path: str = "") -> str:
    base = (base_url or "").strip()
    if not base:
        raise ValueError("站点缺少 base_url")
    if not path:
        return base
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
