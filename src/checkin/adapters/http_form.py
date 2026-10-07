"""Generic HTTP form / cookie check-in adapter."""

from __future__ import annotations

import logging

from checkin.adapters.base import Adapter
from checkin.models import CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class HttpFormAdapter(Adapter):
    """POST/GET form fields with session cookies — suitable for many portal forms."""

    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        try:
            form = site.form_data or site.extra.get("form_data") or {}
            response = self.request(site, timeout=timeout, data=form or None)
            return self.evaluate_response(site, response)
        except Exception as exc:  # noqa: BLE001 — surface as failed result
            logger.exception("http_form check-in failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
            )
