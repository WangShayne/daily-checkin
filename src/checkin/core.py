"""Orchestrator: load config, run adapters, collect results."""

from __future__ import annotations

import logging
import time
from typing import Any

from checkin.adapters import get_adapter
from checkin.config import get_settings, load_config, parse_sites
from checkin.models import CheckInResult, CheckInStatus
from checkin.notifier import notify

logger = logging.getLogger(__name__)


def run_checkin(config_path: str) -> list[CheckInResult]:
    """Run all enabled sites from config. Idempotent for daily schedules."""
    config = load_config(config_path)
    settings = get_settings(config)
    sites = parse_sites(config)
    timeout = int(settings.get("timeout", 30))
    delay = float(settings.get("delay_between_sites", 2))
    continue_on_error = bool(settings.get("continue_on_error", True))

    results: list[CheckInResult] = []
    enabled = [s for s in sites if s.enabled]
    disabled = [s for s in sites if not s.enabled]

    for site in disabled:
        results.append(
            CheckInResult(
                site_name=site.name,
                status=CheckInStatus.SKIPPED,
                message="站点已禁用",
            )
        )
        logger.info("[%s] 跳过（disabled）", site.name)

    for i, site in enumerate(enabled):
        logger.info("[%s] 开始签到 (type=%s)", site.name, site.type)
        try:
            adapter = get_adapter(site.type)
            result = adapter.check_in(site, timeout=timeout)
        except KeyError as exc:
            result = CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] 未捕获异常", site.name)
            result = CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
            )

        results.append(result)
        _log_result(result)

        if not result.ok and not continue_on_error:
            logger.warning("continue_on_error=false，停止后续站点")
            break

        if i < len(enabled) - 1 and delay > 0:
            time.sleep(delay)

    notify(config.get("notification") or {}, results)
    return results


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
