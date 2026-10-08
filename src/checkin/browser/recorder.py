"""Launch headed Chromium on Xvfb, expose via VNC/noVNC, capture cookies + steps."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import signal
import subprocess
import threading
import queue
import socket
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from checkin.browser.flow import finalize_recording, norm_url

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")

DISPLAY = os.environ.get("CHECKIN_DISPLAY", ":99")
VNC_PORT = int(os.environ.get("CHECKIN_VNC_PORT", "5900"))
VNC_READY_TIMEOUT = float(os.environ.get("CHECKIN_VNC_READY_TIMEOUT", "15"))
XVFB_PID_FILE = Path(os.environ.get("CHECKIN_XVFB_PID_FILE", "/tmp/checkin-xvfb.pid"))


# Reports form submits (field names / types, never values) to Python so the
# login form can be recognised even when the password field has an odd name.
FORM_HOOK_JS = r"""
(() => {
  if (window.__checkinHooked) return;
  window.__checkinHooked = true;
  document.addEventListener('submit', (ev) => {
    try {
      const f = ev.target;
      if (!f || f.tagName !== 'FORM') return;
      const fields = Array.from(f.elements)
        .filter((e) => e.name)
        .map((e) => ({ name: e.name, type: String(e.type || '').toLowerCase() }));
      const attr = f.getAttribute('action');
      const info = {
        page_url: location.href,
        action: new URL(attr || location.href, location.href).href,
        method: String(f.getAttribute('method') || 'get').toLowerCase(),
        fields,
        has_password: fields.some((x) => x.type === 'password'),
      };
      if (window.__checkinFormSubmit) window.__checkinFormSubmit(info);
    } catch (e) { /* ignore */ }
  }, true);
})();
"""


def _now() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


@dataclass
class ActiveSession:
    site_id: int
    start_url: str
    vnc_password: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    # form submit metadata from the page (field names/types only, never values)
    submits: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=_now)
    status: str = "starting"  # starting | ready | stopping | error
    error: str = ""
    # runtime handles
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    procs: list[subprocess.Popen[Any]] = field(default_factory=list)
    current_url: str = ""
    vnc_ready: bool = False
    # Commands executed on the Playwright-owning worker thread
    commands: "queue.Queue[tuple[str, Future[Any]]]" = field(default_factory=queue.Queue)
    worker: threading.Thread | None = None
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



def wait_for_port(host: str, port: int, timeout: float) -> bool:
    """Poll until a TCP port accepts connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def vnc_handshake_ok(host: str = "127.0.0.1", port: int | None = None) -> bool:
    """True if a VNC server answers with an RFB greeting."""
    try:
        with socket.create_connection((host, port or VNC_PORT), timeout=2) as s:
            s.settimeout(2)
            return s.recv(12).startswith(b"RFB ")
    except OSError:
        return False


class RecordingManager:
    """One interactive recording session at a time (MVP).

    All Playwright sync objects live on ONE worker thread (they are greenlet /
    thread bound). HTTP handlers talk to it through a command queue.
    """

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
            "current_url": s.current_url,
            "vnc_password": s.vnc_password,
            "vnc_ready": s.vnc_ready,
            "vnc_port": VNC_PORT,
        }

    def diagnostics(self) -> dict[str, Any]:
        """Process / port checks for the troubleshooting panel."""
        s = self._session
        procs = []
        if s:
            for proc in s.procs:
                args = proc.args if isinstance(proc.args, list) else [str(proc.args)]
                procs.append(
                    {
                        "cmd": str(args[0]),
                        "pid": proc.pid,
                        "running": proc.poll() is None,
                    }
                )
        return {
            "session": self.status(),
            "display": DISPLAY,
            "binaries": {
                name: bool(shutil.which(name)) for name in ("Xvfb", "x11vnc")
            },
            "processes": procs,
            "vnc_port": VNC_PORT,
            "vnc_rfb_handshake": vnc_handshake_ok(),
        }

    # ---- lifecycle -------------------------------------------------------

    def start(self, site_id: int, start_url: str) -> dict[str, Any]:
        with self._lock:
            if self._session and self._session.status in ("starting", "ready"):
                raise RuntimeError("已有录制会话进行中，请先完成或取消")
            session = ActiveSession(
                site_id=site_id,
                start_url=start_url,
                vnc_password=secrets.token_urlsafe(8),
            )
            self._session = session

        worker = threading.Thread(
            target=self._worker_main,
            args=(session,),
            daemon=True,
            name="checkin-recorder",
        )
        session.worker = worker
        worker.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and session.status not in ("ready", "error"):
            time.sleep(0.2)
        if session.status == "error":
            err = session.error or "录制会话启动失败"
            with self._lock:
                if self._session is session:
                    self._session = None
            raise RuntimeError(err)
        if session.status != "ready":
            self.cancel()
            raise RuntimeError("录制会话启动超时（60 秒）")
        return self.status()

    def _worker_main(self, session: ActiveSession) -> None:
        """Owns Xvfb/x11vnc/Playwright for the whole session."""
        try:
            self._start_display_stack(session)
            self._start_playwright(session)
            session.status = "ready"
            logger.info(
                "录制会话就绪 site_id=%s url=%s vnc=127.0.0.1:%s",
                session.site_id,
                session.start_url,
                VNC_PORT,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("录制启动失败")
            session.error = str(exc)
            session.status = "error"
            self._teardown(session)
            return

        # Command loop — every Playwright call happens on this thread.
        # The sync API only dispatches events (navigations, requests) while
        # this thread is inside a Playwright call, so idle time is spent in
        # page.wait_for_timeout() instead of a blocking queue.get().
        while True:
            try:
                cmd, fut = session.commands.get_nowait()
            except queue.Empty:
                self._pump_events(session)
                continue
            try:
                if cmd == "finish":
                    fut.set_result(self._collect(session))
                    self._teardown(session)
                    return
                if cmd == "cancel":
                    self._teardown(session)
                    fut.set_result(None)
                    return
                fut.set_exception(RuntimeError(f"未知命令 {cmd}"))
            except Exception as exc:  # noqa: BLE001
                logger.exception("录制命令失败: %s", cmd)
                if not fut.done():
                    fut.set_exception(exc)
                self._teardown(session)
                return

    def _send(self, session: ActiveSession, cmd: str, timeout: float = 60) -> Any:
        fut: Future[Any] = Future()
        session.commands.put((cmd, fut))
        return fut.result(timeout=timeout)

    def _pump_events(self, session: ActiveSession) -> None:
        try:
            if session.page and not session.page.is_closed():
                session.page.wait_for_timeout(300)
                session.current_url = session.page.url or ""
            else:
                time.sleep(0.3)
        except Exception:  # noqa: BLE001
            # Browser closed by the user etc. — keep loop alive for finish/cancel
            time.sleep(0.3)

    def _start_display_stack(self, session: ActiveSession) -> None:
        """Xvfb + x11vnc. The web app itself proxies /ws/vnc -> x11vnc TCP."""
        if not shutil.which("Xvfb"):
            raise RuntimeError("未找到 Xvfb。请使用项目 Dockerfile 构建镜像后录制。")
        if not shutil.which("x11vnc"):
            raise RuntimeError("未找到 x11vnc。请使用项目 Dockerfile 重新构建镜像。")

        _kill_stale_display()
        xvfb = subprocess.Popen(
            ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24", "-ac", "+extension", "RANDR"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        session.procs.append(xvfb)
        try:
            XVFB_PID_FILE.write_text(str(xvfb.pid), encoding="utf-8")
        except OSError as exc:
            logger.debug("无法写入 Xvfb PID 文件: %s", exc)

        # Wait for the X socket
        display_num = DISPLAY.lstrip(":").split(".")[0]
        x_socket = Path(f"/tmp/.X11-unix/X{display_num}")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not x_socket.exists():
            if xvfb.poll() is not None:
                err = (xvfb.stderr.read() or b"").decode(errors="replace")[-300:]
                raise RuntimeError(f"Xvfb 启动失败: {err}")
            time.sleep(0.1)
        if not x_socket.exists():
            raise RuntimeError(f"Xvfb 未就绪（{x_socket} 不存在）")
        os.environ["DISPLAY"] = DISPLAY

        passfile = f"/tmp/checkin-vnc-{session.site_id}.pass"
        subprocess.run(
            ["x11vnc", "-storepasswd", session.vnc_password, passfile],
            check=True,
            capture_output=True,
        )
        vnc = subprocess.Popen(
            [
                "x11vnc",
                "-display", DISPLAY,
                "-rfbport", str(VNC_PORT),
                "-rfbauth", passfile,
                "-forever",
                "-shared",
                "-localhost",
                "-noxdamage",
                "-quiet",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        session.procs.append(vnc)
        if not wait_for_port("127.0.0.1", VNC_PORT, VNC_READY_TIMEOUT):
            err = ""
            if vnc.poll() is not None:
                err = (vnc.stderr.read() or b"").decode(errors="replace")[-300:]
            raise RuntimeError(
                f"x11vnc 未在 {VNC_READY_TIMEOUT:.0f}s 内监听 127.0.0.1:{VNC_PORT}。{err}"
            )
        session.vnc_ready = True
        logger.info("x11vnc 已就绪 127.0.0.1:%s (DISPLAY=%s)", VNC_PORT, DISPLAY)

    def _start_playwright(self, session: ActiveSession) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("未安装 playwright。请在 Docker 镜像中安装依赖。") from exc

        pw = sync_playwright().start()
        session.playwright = pw
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1280,800",
                "--window-position=0,0",
                "--start-maximized",
                "--disable-gpu",
            ],
            env={**os.environ, "DISPLAY": DISPLAY},
        )
        session.browser = browser
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
        )
        session.context = context
        def on_submit(_source: Any, info: Any) -> None:
            if isinstance(info, dict):
                info = {k: info.get(k) for k in ("page_url", "action", "method", "fields", "has_password")}
                info["t"] = time.monotonic()
                with session._lock:
                    session.submits.append(info)

        try:
            context.expose_binding("__checkinFormSubmit", on_submit)
            context.add_init_script(FORM_HOOK_JS)
        except Exception:  # noqa: BLE001
            logger.warning("无法注入表单提交钩子，登录识别将仅依据请求字段", exc_info=True)
        page = context.new_page()
        session.page = page

        def on_nav(frame: Any) -> None:
            if frame != page.main_frame:
                return
            url = frame.url
            if not url or url.startswith("about:"):
                return
            session.current_url = url
            step: dict[str, Any] = {"kind": "navigate", "url": url, "ts": _now()}
            with session._lock:
                # A navigation that is the response of a form POST can't be re-opened via GET
                for prev in reversed(session.steps[-4:]):
                    if prev.get("kind") == "request":
                        if (
                            prev.get("resource_type") == "document"
                            and norm_url(prev.get("url")) == norm_url(url)
                            and time.monotonic() - float(prev.get("t", 0)) < 30
                        ):
                            step["via_post"] = True
                        break
                session.steps.append(step)

        def on_request(request: Any) -> None:
            try:
                if request.method not in ("POST", "PUT"):
                    return
                if request.resource_type not in ("document", "xhr", "fetch", "other"):
                    return
                try:
                    page_url = request.frame.url
                except Exception:  # noqa: BLE001
                    page_url = request.headers.get("referer", "")
                with session._lock:
                    session.steps.append(
                        {
                            "kind": "request",
                            "method": request.method,
                            "url": request.url,
                            "resource_type": request.resource_type,
                            "content_type": request.headers.get("content-type", ""),
                            "page_url": page_url,
                            # Raw body lives in memory only for this session; it is
                            # classified + redacted in _collect() before anything is saved.
                            "_raw": request.post_data,
                            "t": time.monotonic(),
                            "ts": _now(),
                        }
                    )
            except Exception:  # noqa: BLE001
                pass

        page.on("framenavigated", on_nav)
        page.on("request", on_request)
        try:
            page.goto(session.start_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            # Keep the browser open so the user can fix the URL manually
            logger.warning("起始页加载失败（会话仍可用）: %s", exc)
        session.current_url = page.url

    def _collect(self, session: ActiveSession) -> dict[str, Any]:
        cookies: list[dict[str, Any]] = []
        storage_state: dict[str, Any] = {}
        final_url = ""
        if session.page:
            final_url = session.page.url or ""
        if session.context:
            cookies = session.context.cookies()
            storage_state = session.context.storage_state()
        with session._lock:
            raw_steps = list(session.steps)
            submits = list(session.submits)
            session.steps.clear()
        steps, login = finalize_recording(raw_steps, submits)
        if login:
            logger.info(
                "已识别登录步骤 %s（字段 %s），密码将加密保存",
                login["meta"].get("url"),
                ", ".join(login["meta"].get("fields") or []),
            )
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
        return {
            "site_id": session.site_id,
            "final_url": final_url,
            "cookies": cookies,
            "cookie_header": cookie_header,
            "storage_state": storage_state,
            "steps": cleaned,
            # {"meta", "username", "passwords"} — passwords are encrypted by
            # Database.save_login_credential() and never written in clear.
            "login": login,
        }

    def finish(self) -> dict[str, Any]:
        session = self._session
        if not session:
            raise RuntimeError("没有进行中的录制会话")
        if session.status != "ready":
            raise RuntimeError(f"会话状态不可完成: {session.status}")
        session.status = "stopping"
        try:
            return self._send(session, "finish")
        finally:
            with self._lock:
                if self._session is session:
                    self._session = None

    def cancel(self) -> None:
        with self._lock:
            session = self._session
            self._session = None
        if not session:
            return
        worker = session.worker
        if worker and worker.is_alive() and session.status in ("ready", "stopping"):
            try:
                self._send(session, "cancel", timeout=30)
                return
            except Exception:  # noqa: BLE001
                logger.warning("取消录制时工作线程无响应，强制清理进程")
        self._cleanup_procs(session)

    def _teardown(self, session: ActiveSession) -> None:
        """Must run on the worker thread (closes Playwright objects)."""
        for obj in (session.context, session.browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if session.playwright:
                session.playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        session.context = session.browser = session.page = session.playwright = None
        self._cleanup_procs(session)
        session.vnc_ready = False

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
