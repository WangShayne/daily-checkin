"""Base adapter ABC."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urljoin

import requests

from checkin.models import CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class Adapter(ABC):
    """Pluggable check-in backend for one site type."""

    @abstractmethod
    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        """Perform check-in for the given site. Must be safe to re-run daily."""

    # --- shared helpers ---

    def build_url(self, site: SiteConfig) -> str:
        base = site.base_url.rstrip("/") + "/"
        path = site.checkin_path.lstrip("/")
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
                )
            if not matched:
                snippet = text[:200].replace("\n", " ")
                return CheckInResult(
                    site_name=site.name,
                    status=CheckInStatus.FAILED,
                    message=f"未匹配成功关键字；响应片段: {snippet}",
                    http_status=response.status_code,
                )

        if status_ok:
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.SUCCESS,
                message=f"HTTP {response.status_code}",
                http_status=response.status_code,
            )

        snippet = text[:200].replace("\n", " ")
        return CheckInResult(
            site_name=site.name,
            status=CheckInStatus.FAILED,
            message=f"HTTP {response.status_code}; {snippet}",
            http_status=response.status_code,
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
