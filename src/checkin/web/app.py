"""FastAPI application: visual UI + scheduler."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from checkin import __version__
from checkin.core import run_all_from_db, run_one_site, summarize
from checkin.db import get_db, init_db
from checkin.models import (
    MODE_LABELS,
    SITE_TYPES,
    TYPE_LABELS,
    CheckInMode,
    SiteConfig,
)
from checkin.scheduler import reload_jobs, shutdown_scheduler, start_scheduler

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _parse_json_field(raw: str | None, default: Any = None) -> Any:
    text = (raw or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Allow simple key=value;key2=value2 for form_data
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
    logger.info("Daily Check-in Web UI v%s ready", __version__)
    yield
    shutdown_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="每日自动签到", version=__version__, lifespan=lifespan)
    static_dir = BASE_DIR / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        db = get_db()
        sites = db.list_sites()
        latest = db.latest_result_by_site()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "sites": sites,
                "latest": latest,
                "mode_labels": MODE_LABELS,
                "type_labels": TYPE_LABELS,
                "version": __version__,
            },
        )

    @app.get("/sites/new", response_class=HTMLResponse)
    async def site_new(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
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
        return TEMPLATES.TemplateResponse(
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
        return RedirectResponse("/", status_code=303)

    @app.post("/sites/{site_id}")
    async def site_update(
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
        return RedirectResponse("/", status_code=303)

    @app.post("/sites/{site_id}/delete")
    async def site_delete(site_id: int) -> RedirectResponse:
        get_db().delete_site(site_id)
        reload_jobs()
        return RedirectResponse("/", status_code=303)

    @app.post("/run")
    async def run_all() -> RedirectResponse:
        run_all_from_db(triggered_by="manual")
        return RedirectResponse("/logs", status_code=303)

    @app.post("/sites/{site_id}/run")
    async def run_site(site_id: int) -> RedirectResponse:
        run_one_site(site_id, triggered_by="manual")
        return RedirectResponse("/logs", status_code=303)

    @app.get("/logs", response_class=HTMLResponse)
    async def logs(request: Request) -> HTMLResponse:
        db = get_db()
        entries = db.list_run_logs(limit=200)
        return TEMPLATES.TemplateResponse(
            request,
            "logs.html",
            {"logs": entries, "version": __version__},
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        settings = get_db().get_settings()
        return TEMPLATES.TemplateResponse(
            request,
            "settings.html",
            {"settings": settings, "version": __version__},
        )

    @app.post("/settings")
    async def settings_save(
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
        return RedirectResponse("/settings", status_code=303)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
