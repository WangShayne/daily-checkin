"""Base adapter ABC with first-class visit / click modes."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urljoin

import requests

from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class Adapter(ABC):
    """Pluggable check-in backend for one site type."""

    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        """Dispatch by mode: visit (GET page) or click (submit action)."""
        mode = site.mode_enum
        if mode == CheckInMode.VISIT:
            return self.do_visit(site, timeout=timeout)
        return self.do_click(site, timeout=timeout)

    def do_visit(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        """Visit-only: open / GET the site URL each day counts as check-in."""
        try:
            url = self.build_url(site)
            session = self.session_for(site)
            logger.debug("VISIT GET %s", url)
            response = session.get(url, timeout=timeout, allow_redirects=True)
            # For visit mode, default success is any 2xx/3xx unless keywords set
            statuses = site.success_status or [200, 301, 302, 303, 307, 308]
            # Temporarily evaluate with visit-friendly defaults
            original = site.success_status
            site.success_status = statuses
            try:
                result = self.evaluate_response(site, response)
            finally:
                site.success_status = original
            if result.status == CheckInStatus.SUCCESS and not site.success_keywords:
                result.message = f"访问成功 HTTP {response.status_code}"
            result.mode = CheckInMode.VISIT.value
            result.site_id = site.id
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("visit failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=CheckInMode.VISIT.value,
            )

    @abstractmethod
    def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        """Click/button mode: POST form, API call, or other submit action."""

    # --- shared helpers ---

    def build_url(self, site: SiteConfig) -> str:
        base = site.base_url.rstrip("/") + "/"
        path = (site.checkin_path or "").lstrip("/")
        if not path:
            return site.base_url.rstrip("/") or base.rstrip("/")
        return urljoin(base, path)

    def parse_cookies(self, cookies: str | dict[str, str] | None) -> dict[str, str]:
        if cookies is None or cookies == "":
            return {}
        if isinstance(cookies, dict):
            return {str(k): str(v) for k, v in cookies.items()}
        result: dict[str, str] = {}
        for part in str(cookies).split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
        return result

    def session_for(self, site: SiteConfig) -> requests.Session:
        session = requests.Session()
        if site.headers:
            session.headers.update(site.headers)
        cookie_dict = self.parse_cookies(site.cookies)
        if cookie_dict:
            session.cookies.update(cookie_dict)
        return session

    def evaluate_response(
        self,
        site: SiteConfig,
        response: requests.Response,
    ) -> CheckInResult:
        status_ok = response.status_code in (site.success_status or [200])
        text = response.text or ""
        text_lower = text.lower()

        already_markers = ("已签到", "已经签到", "already", "signed today", "重复签到")
        if any(m.lower() in text_lower or m in text for m in already_markers):
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.ALREADY,
                message="今日已签到（幂等成功）",
                http_status=response.status_code,
                site_id=site.id,
                mode=site.mode,
            )

        keywords = site.success_keywords or []
        if keywords:
            matched = any(kw in text or kw.lower() in text_lower for kw in keywords)
            if status_ok and matched:
                return CheckInResult(
                    site_name=site.name,
                    status=CheckInStatus.SUCCESS,
                    message="签到成功",
                    http_status=response.status_code,
                    site_id=site.id,
                    mode=site.mode,
                )
            if not matched:
                snippet = text[:200].replace("\n", " ")
                return CheckInResult(
                    site_name=site.name,
                    status=CheckInStatus.FAILED,
                    message=f"未匹配成功关键字；响应片段: {snippet}",
                    http_status=response.status_code,
                    site_id=site.id,
                    mode=site.mode,
                )

        if status_ok:
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.SUCCESS,
                message=f"HTTP {response.status_code}",
                http_status=response.status_code,
                site_id=site.id,
                mode=site.mode,
            )

        snippet = text[:200].replace("\n", " ")
        return CheckInResult(
            site_name=site.name,
            status=CheckInStatus.FAILED,
            message=f"HTTP {response.status_code}; {snippet}",
            http_status=response.status_code,
            site_id=site.id,
            mode=site.mode,
        )

    def request(
        self,
        site: SiteConfig,
        *,
        timeout: int = 30,
        data: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> requests.Response:
        url = self.build_url(site)
        method = (site.method or "POST").upper()
        session = self.session_for(site)
        logger.debug("%s %s", method, url)
        return session.request(
            method,
            url,
            data=data,
            json=json_body,
            timeout=timeout,
            allow_redirects=True,
        )
