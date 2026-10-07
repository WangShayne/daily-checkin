"""Typical forum daily check-in (Discuz-style and similar)."""

from __future__ import annotations

import logging

from checkin.adapters.base import Adapter
from checkin.models import CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class ForumAdapter(Adapter):
    """
    Forum pattern: authenticated session cookie + POST to a check-in endpoint.

    Many Chinese forums (Discuz plugins, custom boards) expose a dedicated
    sign URL; customize checkin_path / form_data / success_keywords per site.
    """

    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        if not site.cookies:
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message="缺少 cookies（论坛签到通常需要登录态）",
            )
        try:
            form = site.form_data or {"operation": "qiandao"}
            # Prefer form; some forums accept empty body
            response = self.request(site, timeout=timeout, data=form)
            return self.evaluate_response(site, response)
        except Exception as exc:  # noqa: BLE001
            logger.exception("forum check-in failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
            )
