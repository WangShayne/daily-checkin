"""Generic HTTP form / cookie check-in adapter."""

from __future__ import annotations

import logging

from checkin.adapters.base import Adapter
from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class HttpFormAdapter(Adapter):
    """
    POST/GET form fields with session cookies.

    mode=visit: GET the page URL.
    mode=click: submit form_data.
    """

    def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        try:
            form = site.form_data or site.extra.get("form_data") or {}
            response = self.request(site, timeout=timeout, data=form or None)
            result = self.evaluate_response(site, response)
            result.mode = CheckInMode.CLICK.value
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("http_form click failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=CheckInMode.CLICK.value,
            )
