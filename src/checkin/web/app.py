"""FastAPI application: visual UI + scheduler + session login."""

from __future__ import annotations

import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from checkin import __version__
from checkin.core import run_all_from_db, run_one_site
from checkin.db import get_db, init_db
from checkin.models import (
    MODE_LABELS,
    SITE_TYPES,
    TYPE_LABELS,
    CheckInMode,
    SiteConfig,
)
from checkin.scheduler import (
    next_run_times,
    reload_jobs,
    shutdown_scheduler,
    start_scheduler,
)
from checkin.web.record_routes import register_record_routes

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Public paths that do not require login
PUBLIC_PATHS = frozenset({"/login", "/logout", "/health"})

STATUS_TEXT = {
    "success": "签到成功",
    "already": "今日已签",
    "failed": "失败",
    "skipped": "已跳过",
}

DEFAULT_USER = "admin"
DEFAULT_PASSWORD = "changeme"


def _auth_username() -> str:
    return os.environ.get("CHECKIN_USER", DEFAULT_USER).strip() or DEFAULT_USER


def _auth_password() -> str:
    return os.environ.get("CHECKIN_PASSWORD", DEFAULT_PASSWORD)


def _session_secret() -> str:
    secret = os.environ.get("CHECKIN_SESSION_SECRET", "").strip()
    if secret:
        return secret
    # Stable-enough fallback for single-instance; override in production
    return os.environ.get(
        "CHECKIN_SESSION_SECRET_FALLBACK",
        "daily-checkin-dev-secret-change-me",
    )


def _is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user"))


def _cred_ok(given: str, expected: str) -> bool:
    """Constant-time compare; unequal lengths are never equal."""
    g = given.encode("utf-8")
    e = expected.encode("utf-8")
    if len(g) != len(e):
        secrets.compare_digest(e, e)
        return False
    return secrets.compare_digest(g, e)


def _parse_json_field(raw: str | None, default: Any = None) -> Any:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if default == {} or isinstance(default, dict):
            result: dict[str, str] = {}
            for part in text.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    result[k.strip()] = v.strip()
            return result or default
        if isinstance(default, list):
            return [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]
        return default


def _keywords_from_form(raw: str | None) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = _parse_json_field(text, [])
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    return [x.strip() for x in text.replace("，", ",").split(",") if x.strip()]


def _site_from_form(
    *,
    name: str,
    type_: str,
    mode: str,
    enabled: str | None,
    base_url: str,
    checkin_path: str,
    method: str,
    cookies: str,
    headers_json: str,
    form_data_json: str,
    body_json: str,
    success_keywords: str,
    success_status: str,
    schedule_type: str,
    daily_time: str,
    cron: str,
) -> SiteConfig:
    mode_norm = mode.strip().lower()
    if mode_norm in ("visit", "visit-only", "visit_only"):
        mode_norm = CheckInMode.VISIT.value
    elif mode_norm in ("recorded", "record", "browser", "replay"):
        mode_norm = CheckInMode.RECORDED.value
    else:
        mode_norm = CheckInMode.CLICK.value

    statuses = _parse_json_field(success_status, [200])
    if not isinstance(statuses, list):
        statuses = [200]
    statuses = [int(x) for x in statuses]

    headers = _parse_json_field(headers_json, {})
    if not isinstance(headers, dict):
        headers = {}

    form_data = _parse_json_field(form_data_json, None)
    body = _parse_json_field(body_json, None)

    return SiteConfig(
        name=name.strip(),
        type=type_.strip().lower(),
        mode=mode_norm,
        enabled=enabled in ("1", "true", "on", "yes"),
        base_url=base_url.strip(),
        checkin_path=checkin_path.strip(),
        method=(method or "POST").strip().upper(),
        cookies=cookies.strip() or None,
        headers={str(k): str(v) for k, v in headers.items()},
        form_data=form_data if isinstance(form_data, dict) else None,
        body=body,
        success_keywords=_keywords_from_form(success_keywords),
        success_status=statuses,
        schedule_type=schedule_type if schedule_type in ("daily", "cron") else "daily",
        daily_time=(daily_time or "09:00").strip(),
        cron=(cron or "0 9 * * *").strip(),
    )


def flash(request: Request, message: str, kind: str = "success") -> None:
    """Queue a toast message for the next rendered page (session based)."""
    items = list(request.session.get("_flash") or [])
    items.append({"kind": kind, "message": message})
    request.session["_flash"] = items[-5:]


def _tpl(request: Request, name: str, context: dict[str, Any] | None = None) -> HTMLResponse:
    flashes = list(request.session.pop("_flash", None) or [])
    # Query-string messages (used by redirects from the recorder routes)
    q = request.query_params
    if q.get("err"):
        flashes.append({"kind": "error", "message": q["err"]})
    if q.get("msg"):
        flashes.append({"kind": "success", "message": q["msg"]})
    ctx = {
        "version": __version__,
        "current_user": request.session.get("user"),
        "flashes": flashes,
        "path": request.url.path,
        "default_password": _auth_password() == DEFAULT_PASSWORD,
    }
    if context:
        ctx.update(context)
    return TEMPLATES.TemplateResponse(request, name, ctx)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    data_dir = os.environ.get("CHECKIN_DATA_DIR", "data")
    init_db(data_dir)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    start_scheduler()
    user = _auth_username()
    if _auth_password() == DEFAULT_PASSWORD:
        logger.warning(
            "正在使用默认密码（用户 %s / changeme）。请通过环境变量 "
            "CHECKIN_USER / CHECKIN_PASSWORD 修改！",
            user,
        )
    logger.info("Daily Check-in Web UI v%s ready (port default 4567)", __version__)
    yield
    shutdown_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="每日自动签到", version=__version__, lifespan=lifespan)
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if path.startswith("/static") or path in PUBLIC_PATHS:
            return await call_next(request)
        if _is_logged_in(request):
            return await call_next(request)
        next_url = path
        if request.url.query:
            next_url = f"{path}?{request.url.query}"
        return RedirectResponse(
            url=f"/login?next={quote(next_url, safe='')}",
            status_code=303,
        )

    # Outermost: session must wrap auth so request.session is available
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(),
        session_cookie="checkin_session",
        max_age=60 * 60 * 24 * 7,  # 7 days
        same_site="lax",
        https_only=False,
    )

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/") -> HTMLResponse:
        if _is_logged_in(request):
            return RedirectResponse(next or "/", status_code=303)
        return _tpl(
            request,
            "login.html",
            {"error": None, "next": next or "/"},
        )

    @app.post("/login")
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form("/"),
    ) -> Response:
        ok = _cred_ok(username.strip(), _auth_username()) and _cred_ok(
            password, _auth_password()
        )
        if not ok:
            return _tpl(
                request,
                "login.html",
                {
                    "error": "用户名或密码错误",
                    "next": next or "/",
                },
            )
        request.session["user"] = username.strip()
        dest = next if next.startswith("/") else "/"
        return RedirectResponse(dest, status_code=303)

    @app.get("/logout")
    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        db = get_db()
        sites = db.list_sites()
        latest = db.latest_result_by_site()
        today = db.stats_today()
        stats = {
            "total": len(sites),
            "enabled": sum(1 for s in sites if s.enabled),
            "ok_today": today["success"] + today["already"],
            "failed_today": today["failed"],
        }
        return _tpl(
            request,
            "index.html",
            {
                "sites": sites,
                "latest": latest,
                "flows": db.list_flow_summaries(),
                "next_runs": next_run_times(),
                "stats": stats,
                "mode_labels": MODE_LABELS,
                "type_labels": TYPE_LABELS,
            },
        )

    @app.get("/sites/new", response_class=HTMLResponse)
    async def site_new(request: Request) -> HTMLResponse:
        return _tpl(
            request,
            "site_form.html",
            {
                "site": None,
                "mode_labels": MODE_LABELS,
                "type_labels": TYPE_LABELS,
                "site_types": SITE_TYPES,
                "title": "添加站点",
            },
        )

    @app.get("/sites/{site_id}/edit", response_class=HTMLResponse)
    async def site_edit(request: Request, site_id: int) -> HTMLResponse:
        site = get_db().get_site(site_id)
        if site is None:
            return RedirectResponse("/", status_code=303)
        return _tpl(
            request,
            "site_form.html",
            {
                "site": site,
                "mode_labels": MODE_LABELS,
                "type_labels": TYPE_LABELS,
                "site_types": SITE_TYPES,
                "title": f"编辑：{site.name}",
            },
        )

    @app.post("/sites")
    async def site_create(
        request: Request,
        name: str = Form(...),
        type: str = Form(...),
        mode: str = Form(...),
        enabled: str | None = Form(None),
        base_url: str = Form(""),
        checkin_path: str = Form(""),
        method: str = Form("POST"),
        cookies: str = Form(""),
        headers_json: str = Form(""),
        form_data_json: str = Form(""),
        body_json: str = Form(""),
        success_keywords: str = Form(""),
        success_status: str = Form("[200]"),
        schedule_type: str = Form("daily"),
        daily_time: str = Form("09:00"),
        cron: str = Form("0 9 * * *"),
    ) -> RedirectResponse:
        site = _site_from_form(
            name=name,
            type_=type,
            mode=mode,
            enabled=enabled,
            base_url=base_url,
            checkin_path=checkin_path,
            method=method,
            cookies=cookies,
            headers_json=headers_json,
            form_data_json=form_data_json,
            body_json=body_json,
            success_keywords=success_keywords,
            success_status=success_status,
            schedule_type=schedule_type,
            daily_time=daily_time,
            cron=cron,
        )
        get_db().add_site(site)
        reload_jobs()
        flash(request, f"已添加站点「{site.name}」")
        return RedirectResponse("/", status_code=303)

    @app.post("/sites/{site_id}")
    async def site_update(
        request: Request,
        site_id: int,
        name: str = Form(...),
        type: str = Form(...),
        mode: str = Form(...),
        enabled: str | None = Form(None),
        base_url: str = Form(""),
        checkin_path: str = Form(""),
        method: str = Form("POST"),
        cookies: str = Form(""),
        headers_json: str = Form(""),
        form_data_json: str = Form(""),
        body_json: str = Form(""),
        success_keywords: str = Form(""),
        success_status: str = Form("[200]"),
        schedule_type: str = Form("daily"),
        daily_time: str = Form("09:00"),
        cron: str = Form("0 9 * * *"),
    ) -> RedirectResponse:
        site = _site_from_form(
            name=name,
            type_=type,
            mode=mode,
            enabled=enabled,
            base_url=base_url,
            checkin_path=checkin_path,
            method=method,
            cookies=cookies,
            headers_json=headers_json,
            form_data_json=form_data_json,
            body_json=body_json,
            success_keywords=success_keywords,
            success_status=success_status,
            schedule_type=schedule_type,
            daily_time=daily_time,
            cron=cron,
        )
        get_db().update_site(site_id, site)
        reload_jobs()
        flash(request, f"已保存「{site.name}」")
        return RedirectResponse("/", status_code=303)

    @app.post("/sites/{site_id}/delete")
    async def site_delete(request: Request, site_id: int) -> RedirectResponse:
        site = get_db().get_site(site_id)
        get_db().delete_site(site_id)
        reload_jobs()
        if site:
            flash(request, f"已删除「{site.name}」", "info")
        return RedirectResponse("/", status_code=303)

    @app.post("/run")
    def run_all(request: Request) -> RedirectResponse:
        results = run_all_from_db(triggered_by="manual")
        ok = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if r.status.value == "failed")
        flash(
            request,
            f"全部签到完成：成功 {ok}，失败 {failed}",
            "error" if failed else "success",
        )
        return RedirectResponse("/logs", status_code=303)

    @app.post("/sites/{site_id}/run")
    def run_site(request: Request, site_id: int) -> RedirectResponse:
        r = run_one_site(site_id, triggered_by="manual")
        flash(
            request,
            f"「{r.site_name}」{STATUS_TEXT.get(r.status.value, r.status.value)}：{r.message}",
            "success" if r.ok else "error",
        )
        return RedirectResponse("/logs", status_code=303)

    @app.get("/logs", response_class=HTMLResponse)
    async def logs(
        request: Request,
        status: str = "",
        site_id: str = "",
    ) -> HTMLResponse:
        db = get_db()
        sid = int(site_id) if site_id.isdigit() else None
        entries = db.list_run_logs(limit=200, site_id=sid, status=status or None)
        return _tpl(
            request,
            "logs.html",
            {
                "logs": entries,
                "sites": db.list_sites(),
                "f_status": status,
                "f_site": sid,
                "today": db.stats_today(),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        settings = get_db().get_settings()
        return _tpl(
            request,
            "settings.html",
            {
                "settings": settings,
                "auth_user": _auth_username(),
                "data_dir": os.environ.get("CHECKIN_DATA_DIR", "data"),
            },
        )

    @app.post("/settings")
    async def settings_save(
        request: Request,
        timeout: int = Form(30),
        delay_between_sites: float = Form(2),
        continue_on_error: str | None = Form(None),
        notify_enabled: str | None = Form(None),
        notify_webhook_url: str = Form(""),
    ) -> RedirectResponse:
        get_db().update_settings(
            {
                "timeout": timeout,
                "delay_between_sites": delay_between_sites,
                "continue_on_error": continue_on_error in ("1", "on", "true"),
                "notify_enabled": notify_enabled in ("1", "on", "true"),
                "notify_webhook_url": notify_webhook_url,
            }
        )
        flash(request, "设置已保存")
        return RedirectResponse("/settings", status_code=303)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    register_record_routes(app, tpl=_tpl)

    return app


app = create_app()
