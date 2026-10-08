"""APScheduler integration — per-site schedules in Asia/Shanghai."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from checkin.db import Database

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone=SHANGHAI)
    return _scheduler


def _job_id(site_id: int) -> str:
    return f"site_{site_id}"


def _run_site_job(site_id: int) -> None:
    from checkin.core import run_one_site

    logger.info("定时任务触发 site_id=%s", site_id)
    try:
        run_one_site(site_id, triggered_by="scheduler")
    except Exception:  # noqa: BLE001
        logger.exception("定时签到失败 site_id=%s", site_id)


def _parse_daily_time(daily_time: str) -> tuple[int, int]:
    parts = (daily_time or "09:00").strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return hour, minute


def reload_jobs(db: Database | None = None) -> None:
    """Rebuild all site jobs from the database."""
    from checkin.db import get_db

    database = db or get_db()
    scheduler = get_scheduler()

    # Remove existing site jobs
    for job in list(scheduler.get_jobs()):
        if job.id.startswith("site_"):
            job.remove()

    for site in database.list_sites():
        if not site.enabled or site.id is None:
            continue
        jid = _job_id(site.id)
        try:
            if site.schedule_type == "cron":
                trigger = CronTrigger.from_crontab(
                    site.cron or "0 9 * * *", timezone=SHANGHAI
                )
            else:
                hour, minute = _parse_daily_time(site.daily_time)
                trigger = CronTrigger(
                    hour=hour, minute=minute, timezone=SHANGHAI
                )
            scheduler.add_job(
                _run_site_job,
                trigger=trigger,
                id=jid,
                args=[site.id],
                replace_existing=True,
                name=f"签到: {site.name}",
            )
            logger.info(
                "已调度 [%s] id=%s type=%s time=%s cron=%s",
                site.name,
                site.id,
                site.schedule_type,
                site.daily_time,
                site.cron,
            )
        except Exception:  # noqa: BLE001
            logger.exception("无法调度站点 %s", site.name)


def start_scheduler() -> BackgroundScheduler:
    scheduler = get_scheduler()
    if not scheduler.running:
        reload_jobs()
        scheduler.start()
        logger.info("调度器已启动（时区 Asia/Shanghai）")
    else:
        reload_jobs()
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
    _scheduler = None


def next_run_times() -> dict[int, str]:
    """site_id -> next run 'MM-DD HH:MM' (Asia/Shanghai) for scheduled sites."""
    out: dict[int, str] = {}
    sched = _scheduler
    if sched is None:
        return out
    for job in sched.get_jobs():
        if not job.id.startswith("site_") or job.next_run_time is None:
            continue
        try:
            sid = int(job.id.split("_", 1)[1])
        except ValueError:
            continue
        out[sid] = job.next_run_time.astimezone(SHANGHAI).strftime("%m-%d %H:%M")
    return out
