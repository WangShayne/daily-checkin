"""Recording UI routes: noVNC session + save flow."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from checkin.browser.recorder import (
    VNC_PORT,
    build_start_url,
    get_recording_manager,
)
from checkin.db import get_db
from checkin.models import CheckInMode, SiteConfig

logger = logging.getLogger(__name__)

NOVNC_CANDIDATES = [
    Path("/usr/share/novnc"),
    Path("/usr/share/novnc/"),
]


def _find_novnc() -> Path | None:
    for p in NOVNC_CANDIDATES:
        if (p / "vnc.html").exists() or (p / "vnc_lite.html").exists():
            return p
    return None


def register_record_routes(
    app: FastAPI,
    *,
    tpl: Callable[..., HTMLResponse],
) -> None:
    novnc_dir = _find_novnc()
    if novnc_dir is not None:
        app.mount("/novnc", StaticFiles(directory=str(novnc_dir), html=True), name="novnc")
        logger.info("noVNC static mounted from %s", novnc_dir)
    else:
        logger.warning("系统未安装 noVNC，嵌入式桌面将显示说明页")

    @app.get("/record", response_class=HTMLResponse)
    async def record_index(request: Request) -> HTMLResponse:
        db = get_db()
        sites = db.list_sites()
        flows = db.list_flow_summaries()
        mgr = get_recording_manager().status()
        return tpl(
            request,
            "record.html",
            {"sites": sites, "flows": flows, "session": mgr},
        )

    @app.post("/record/start")
    def record_start(
        request: Request,
        site_id: int = Form(...),
    ) -> RedirectResponse:
        db = get_db()
        site = db.get_site(site_id)
        if site is None:
            return RedirectResponse("/record?err=站点不存在", status_code=303)
        try:
            url = build_start_url(site.base_url, site.checkin_path or "")
            get_recording_manager().start(site_id, url)
        except Exception as exc:  # noqa: BLE001
            return RedirectResponse(
                f"/record/session?err={exc}",
                status_code=303,
            )
        return RedirectResponse("/record/session", status_code=303)

    @app.post("/record/quick-site")
    def record_quick_site(
        name: str = Form(...),
        base_url: str = Form(...),
        checkin_path: str = Form(""),
    ) -> RedirectResponse:
        db = get_db()
        site = SiteConfig(
            name=name.strip(),
            type="browser",
            mode=CheckInMode.RECORDED.value,
            enabled=True,
            base_url=base_url.strip(),
            checkin_path=checkin_path.strip(),
            method="GET",
        )
        sid = db.add_site(site)
        try:
            url = build_start_url(site.base_url, site.checkin_path or "")
            get_recording_manager().start(sid, url)
        except Exception as exc:  # noqa: BLE001
            return RedirectResponse(f"/record?err={exc}", status_code=303)
        return RedirectResponse("/record/session", status_code=303)

    @app.get("/record/session", response_class=HTMLResponse)
    async def record_session(request: Request, err: str = "") -> HTMLResponse:
        mgr = get_recording_manager()
        st = mgr.status()
        site = None
        if st.get("active"):
            site = get_db().get_site(int(st["site_id"]))
        novnc_ok = _find_novnc() is not None
        return tpl(
            request,
            "record_session.html",
            {
                "session": st,
                "site": site,
                "novnc_ok": novnc_ok,
                "error": err,
            },
        )

    @app.get("/api/record/status")
    async def record_status() -> JSONResponse:
        return JSONResponse(get_recording_manager().status())

    @app.get("/api/record/diag")
    def record_diag() -> JSONResponse:
        """Troubleshooting: Xvfb / x11vnc processes and RFB handshake."""
        data = get_recording_manager().diagnostics()
        data["novnc_static"] = _find_novnc() is not None
        data["ws_path"] = "/ws/vnc"
        return JSONResponse(data)

    @app.post("/record/finish")
    def record_finish() -> RedirectResponse:
        mgr = get_recording_manager()
        try:
            payload = mgr.finish()
        except Exception as exc:  # noqa: BLE001
            return RedirectResponse(f"/record?err={exc}", status_code=303)
        db = get_db()
        site_id = int(payload["site_id"])
        db.save_recorded_flow(
            site_id,
            final_url=payload.get("final_url") or "",
            cookies=payload.get("cookies") or [],
            storage_state=payload.get("storage_state") or {},
            steps=payload.get("steps") or [],
        )
        site = db.get_site(site_id)
        if site:
            site.mode = CheckInMode.RECORDED.value
            site.type = "browser"
            # Keep a cookie header snapshot for visibility (secrets stay in volume)
            site.cookies = payload.get("cookie_header") or site.cookies
            if payload.get("final_url") and not site.checkin_path:
                # leave checkin_path as-is; final_url stored in flow
                pass
            db.update_site(site_id, site)
        from checkin.scheduler import reload_jobs

        reload_jobs()
        return RedirectResponse(f"/record?ok=1&site_id={site_id}", status_code=303)

    @app.post("/record/cancel")
    def record_cancel() -> RedirectResponse:
        get_recording_manager().cancel()
        return RedirectResponse("/record", status_code=303)

    @app.websocket("/ws/vnc")
    async def vnc_ws(websocket: WebSocket) -> None:
        """noVNC <-> x11vnc bridge.

        The browser speaks WebSocket (binary frames carrying RFB); x11vnc speaks
        raw RFB over TCP. This endpoint *is* the websockify: it unwraps frames
        and pipes bytes to 127.0.0.1:VNC_PORT. Served on the same host:port as
        the UI (4567), so LAN access like http://192.168.x.x:4567 just works.
        """
        client = websocket.client.host if websocket.client else "?"
        requested = websocket.scope.get("subprotocols") or []
        subprotocol = "binary" if "binary" in requested else None

        session_data = websocket.scope.get("session") or {}
        if not session_data.get("user"):
            logger.warning("VNC WebSocket 拒绝：未登录 (client=%s)", client)
            await websocket.accept(subprotocol=subprotocol)
            await websocket.close(code=4401, reason="not logged in")
            return

        mgr = get_recording_manager()
        st = mgr.status()
        if not st.get("active"):
            logger.warning("VNC WebSocket 拒绝：没有活动录制会话 (client=%s)", client)
            await websocket.accept(subprotocol=subprotocol)
            await websocket.close(code=4404, reason="no active recording session")
            return

        # Readiness: x11vnc may still be starting — retry for a few seconds
        reader = writer = None
        deadline = asyncio.get_running_loop().time() + 10
        last_err: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", VNC_PORT)
                break
            except OSError as exc:
                last_err = exc
                await asyncio.sleep(0.3)
        if writer is None or reader is None:
            logger.error(
                "VNC WebSocket：无法连接 x11vnc 127.0.0.1:%s (%s)", VNC_PORT, last_err
            )
            await websocket.accept(subprotocol=subprotocol)
            await websocket.close(code=1011, reason="vnc server not reachable")
            return

        await websocket.accept(subprotocol=subprotocol)
        logger.info(
            "VNC WebSocket 已连接 client=%s -> 127.0.0.1:%s subprotocol=%s",
            client,
            VNC_PORT,
            subprotocol,
        )

        async def client_to_vnc() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    data = message.get("bytes")
                    if data is None and message.get("text") is not None:
                        data = message["text"].encode("latin-1", errors="ignore")
                    if not data:
                        continue
                    writer.write(data)
                    await writer.drain()
            except (WebSocketDisconnect, ConnectionError):
                pass
            except Exception:  # noqa: BLE001
                logger.debug("client->vnc 中断", exc_info=True)

        async def vnc_to_client() -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:  # noqa: BLE001
                logger.debug("vnc->client 中断", exc_info=True)

        tasks = [
            asyncio.create_task(client_to_vnc()),
            asyncio.create_task(vnc_to_client()),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            try:
                await websocket.close()
            except Exception:  # noqa: BLE001
                pass
            logger.info("VNC WebSocket 已断开 client=%s", client)
