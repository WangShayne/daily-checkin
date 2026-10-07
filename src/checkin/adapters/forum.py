"""Typical forum daily check-in (Discuz-style and similar)."""

from __future__ import annotations

import logging

from checkin.adapters.base import Adapter
from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class ForumAdapter(Adapter):
    """
    Forum pattern: authenticated session cookie + POST to a check-in endpoint.

    mode=visit: GET the page (daily visit counts).
    mode=click: POST form / plugin sign URL.
    """

    def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        if not site.cookies:
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message="缺少 cookies（论坛签到通常需要登录态）",
                site_id=site.id,
                mode=CheckInMode.CLICK.value,
            )
        try:
            form = site.form_data or {"operation": "qiandao"}
            response = self.request(site, timeout=timeout, data=form)
            result = self.evaluate_response(site, response)
            result.mode = CheckInMode.CLICK.value
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("forum click failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=CheckInMode.CLICK.value,
            )
