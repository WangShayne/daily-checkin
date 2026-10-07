"""Generic portal / homepage API sign-in pattern."""

from __future__ import annotations

import logging

from checkin.adapters.base import Adapter
from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class PortalAdapter(Adapter):
    """
    Portal sites often expose a JSON API under /api/.../checkin.

    mode=visit: GET the portal URL.
    mode=click: POST JSON / form check-in API.
    """

    def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        try:
            json_body = site.body
            if json_body is None and site.form_data:
                response = self.request(site, timeout=timeout, data=site.form_data)
            else:
                response = self.request(site, timeout=timeout, json_body=json_body)
            result = self.evaluate_response(site, response)
            result.mode = CheckInMode.CLICK.value
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("portal click failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=CheckInMode.CLICK.value,
            )
