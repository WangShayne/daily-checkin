"""Generic portal / homepage API sign-in pattern."""

from __future__ import annotations

import logging

from checkin.adapters.base import Adapter
from checkin.models import CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class PortalAdapter(Adapter):
    """
    Portal sites often expose a JSON API under /api/.../checkin.

    Sends optional JSON body and Authorization header from config.
    """

    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        try:
            json_body = site.body
            # If form_data is set without body, fall back to form
            if json_body is None and site.form_data:
                response = self.request(site, timeout=timeout, data=site.form_data)
            else:
                response = self.request(site, timeout=timeout, json_body=json_body)
            return self.evaluate_response(site, response)
        except Exception as exc:  # noqa: BLE001
            logger.exception("portal check-in failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
            )
