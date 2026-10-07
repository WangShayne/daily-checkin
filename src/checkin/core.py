"""Orchestrator: run adapters for sites from DB or config, collect results."""

from __future__ import annotations

import logging
import time
from typing import Any

from checkin.adapters import get_adapter_for_site
from checkin.db import get_db
from checkin.models import CheckInResult, CheckInStatus, SiteConfig
from checkin.notifier import notify

logger = logging.getLogger(__name__)


def run_sites(
    sites: list[SiteConfig],
    *,
    timeout: int = 30,
    delay: float = 2.0,
    continue_on_error: bool = True,
    triggered_by: str = "manual",
    persist: bool = True,
    notification: dict[str, Any] | None = None,
) -> list[CheckInResult]:
    """Run check-in for the given site list."""
    results: list[CheckInResult] = []
    enabled = [s for s in sites if s.enabled]
    disabled = [s for s in sites if not s.enabled]
    db = get_db() if persist else None

    for site in disabled:
        result = CheckInResult(
            site_name=site.name,
            status=CheckInStatus.SKIPPED,
            message="站点已禁用",
            site_id=site.id,
            mode=site.mode,
        )
        results.append(result)
        logger.info("[%s] 跳过（disabled）", site.name)
        if db:
            db.add_run_log(result, triggered_by=triggered_by)

    for i, site in enumerate(enabled):
        logger.info(
            "[%s] 开始签到 (type=%s mode=%s)", site.name, site.type, site.mode
        )
        try:
            adapter = get_adapter_for_site(site)
            result = adapter.check_in(site, timeout=timeout)
            result.site_id = site.id
            result.mode = result.mode or site.mode
        except KeyError as exc:
            result = CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=site.mode,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] 未捕获异常", site.name)
            result = CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=site.mode,
            )

        results.append(result)
        _log_result(result)
        if db:
            db.add_run_log(result, triggered_by=triggered_by)

        if not result.ok and not continue_on_error:
            logger.warning("continue_on_error=false，停止后续站点")
            break

        if i < len(enabled) - 1 and delay > 0:
            time.sleep(delay)

    if notification:
        notify(notification, results)
    return results


def run_all_from_db(*, triggered_by: str = "scheduler") -> list[CheckInResult]:
    """Load all sites from SQLite and run enabled ones."""
    db = get_db()
    settings = db.get_settings()
    sites = db.list_sites()
    notification = {
        "enabled": settings["notify_enabled"],
        "webhook_url": settings["notify_webhook_url"],
    }
    return run_sites(
        sites,
        timeout=settings["timeout"],
        delay=settings["delay_between_sites"],
        continue_on_error=settings["continue_on_error"],
        triggered_by=triggered_by,
        persist=True,
        notification=notification,
    )


def run_one_site(site_id: int, *, triggered_by: str = "manual") -> CheckInResult:
    """Run a single site by id."""
    db = get_db()
    site = db.get_site(site_id)
    if site is None:
        return CheckInResult(
            site_name=f"#{site_id}",
            status=CheckInStatus.FAILED,
            message="站点不存在",
            site_id=site_id,
        )
    settings = db.get_settings()
    # Force enabled for manual single run
    site.enabled = True
    results = run_sites(
        [site],
        timeout=settings["timeout"],
        delay=0,
        continue_on_error=True,
        triggered_by=triggered_by,
        persist=True,
        notification=None,
    )
    return results[0]


def run_checkin(config_path: str) -> list[CheckInResult]:
    """CLI path: load YAML config and run (no DB persistence required)."""
    from checkin.config import get_settings, load_config, parse_sites

    config = load_config(config_path)
    settings = get_settings(config)
    sites = parse_sites(config)
    return run_sites(
        sites,
        timeout=int(settings.get("timeout", 30)),
        delay=float(settings.get("delay_between_sites", 2)),
        continue_on_error=bool(settings.get("continue_on_error", True)),
        triggered_by="cli",
        persist=False,
        notification=config.get("notification") or {},
    )


def _log_result(result: CheckInResult) -> None:
    level = logging.INFO if result.ok else logging.ERROR
    logger.log(
        level,
        "[%s] %s — %s",
        result.site_name,
        result.status.value,
        result.message,
    )


def summarize(results: list[CheckInResult]) -> dict[str, Any]:
    ok = sum(1 for r in results if r.ok)
    failed = sum(1 for r in results if r.status == CheckInStatus.FAILED)
    return {
        "total": len(results),
        "ok": ok,
        "failed": failed,
        "results": results,
    }
