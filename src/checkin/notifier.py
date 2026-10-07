"""Optional webhook / email notification stub."""

from __future__ import annotations

import logging
from typing import Any

import requests

from checkin.models import CheckInResult, CheckInStatus

logger = logging.getLogger(__name__)


def format_summary(results: list[CheckInResult]) -> str:
    lines = ["每日签到结果汇总："]
    for r in results:
        icon = {
            CheckInStatus.SUCCESS: "✅",
            CheckInStatus.ALREADY: "🔁",
            CheckInStatus.SKIPPED: "⏭️",
            CheckInStatus.FAILED: "❌",
        }.get(r.status, "•")
        lines.append(f"{icon} {r.site_name}: {r.status.value} — {r.message}")
    failed = sum(1 for r in results if r.status == CheckInStatus.FAILED)
    lines.append(f"\n合计: {len(results)} 站点，失败 {failed}")
    return "\n".join(lines)


def notify(notification: dict[str, Any], results: list[CheckInResult]) -> None:
    """
    Send summary if notification.enabled and webhook_url is set.

    Supports generic POST JSON webhooks (Slack incoming, Discord, custom).
    Email is left as a stub for future extension.
    """
    if not notification.get("enabled"):
        logger.debug("通知未启用")
        return

    webhook_url = (notification.get("webhook_url") or "").strip()
    text = format_summary(results)

    if webhook_url:
        try:
            payload = {"text": text, "content": text}
            resp = requests.post(webhook_url, json=payload, timeout=15)
            resp.raise_for_status()
            logger.info("已发送 webhook 通知")
        except Exception:  # noqa: BLE001
            logger.exception("webhook 通知失败")
    else:
        logger.warning("通知已启用但未配置 webhook_url，跳过推送")

    # Email stub — wire SMTP later via env if needed
    if notification.get("email"):
        logger.info("邮件通知尚未实现，跳过: %s", notification.get("email"))
