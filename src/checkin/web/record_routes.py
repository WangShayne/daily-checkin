"""Recording UI routes: noVNC session + save flow."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from checkin.browser.recorder import build_start_url, get_recording_manager
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
    async def record_start(
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
    async def record_quick_site(
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

    @app.post("/record/finish")
    async def record_finish() -> RedirectResponse:
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
    async def record_cancel() -> RedirectResponse:
        get_recording_manager().cancel()
        return RedirectResponse("/record", status_code=303)

    @app.websocket("/ws/vnc")
    async def vnc_ws(websocket: WebSocket) -> None:
        # Session cookie auth (http middleware does not cover WS)
        user = websocket.session.get("user") if hasattr(websocket, "session") else None
        # Starlette SessionMiddleware stores session in scope
        session_data = websocket.scope.get("session") or {}
        if not session_data.get("user") and not user:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        mgr = get_recording_manager().status()
        if not mgr.get("active"):
            await websocket.close(code=1013)
            return
        port = int(mgr.get("websockify_port") or 6080)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except Exception:  # noqa: BLE001
            logger.exception("无法连接 websockify")
            await websocket.close(code=1011)
            return

        async def client_to_vnc() -> None:
            try:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    data = message.get("bytes") or message.get("text")
                    if data is None:
                        continue
                    if isinstance(data, str):
                        data = data.encode("utf-8")
                    writer.write(data)
                    await writer.drain()
            except WebSocketDisconnect:
                pass
            except Exception:  # noqa: BLE001
                pass

        async def vnc_to_client() -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    await websocket.send_bytes(data)
            except Exception:  # noqa: BLE001
                pass

        try:
            await asyncio.gather(client_to_vnc(), vnc_to_client())
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
