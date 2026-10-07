"""Replay a previously recorded browser check-in flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from checkin.adapters.base import Adapter
from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)


class RecordedAdapter(Adapter):
    """
    mode=recorded: restore Playwright storage_state and replay navigations / POSTs.
    """

    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        return self.do_click(site, timeout=timeout)

    def do_visit(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        return self.do_click(site, timeout=timeout)

    def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        if site.id is None:
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message="录制回放需要已保存的站点 id",
                mode=CheckInMode.RECORDED.value,
            )
        try:
            from checkin.db import get_db

            flow = get_db().get_recorded_flow(site.id)
        except Exception as exc:  # noqa: BLE001
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=f"读取录制流程失败: {exc}",
                site_id=site.id,
                mode=CheckInMode.RECORDED.value,
            )
        if not flow:
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message="尚未录制流程，请先在「录制会话」中完成录制",
                site_id=site.id,
                mode=CheckInMode.RECORDED.value,
            )
        try:
            return asyncio.run(
                self._replay(site, flow, timeout=timeout)
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("recorded replay failed for %s", site.name)
            return CheckInResult(
                site_name=site.name,
                status=CheckInStatus.FAILED,
                message=str(exc),
                site_id=site.id,
                mode=CheckInMode.RECORDED.value,
            )

    async def _replay(
        self,
        site: SiteConfig,
        flow: dict[str, Any],
        *,
        timeout: int,
    ) -> CheckInResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("未安装 playwright，无法回放录制流程") from exc

        storage = flow.get("storage_state") or {}
        steps = flow.get("steps") or []
        final_url = flow.get("final_url") or ""
        if not final_url:
            final_url = self.build_url(site)

        ms = max(timeout, 30) * 1000
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = await browser.new_context(
                    storage_state=storage if storage else None,
                    ignore_https_errors=True,
                )
                page = await context.new_page()
                navigations = [s for s in steps if s.get("kind") == "navigate" and s.get("url")]
                posts = [
                    s
                    for s in steps
                    if s.get("kind") == "request" and s.get("method") in ("POST", "PUT")
                ]

                # Replay last few navigations to warm session, then land on final
                targets = navigations[-5:] if navigations else []
                if not targets and final_url:
                    targets = [{"url": final_url}]
                last_response = None
                for step in targets:
                    last_response = await page.goto(
                        step["url"],
                        wait_until="domcontentloaded",
                        timeout=ms,
                    )

                # Replay last POST that looks like a check-in (same host preferred)
                if posts:
                    post = posts[-1]
                    try:
                        last_response = await context.request.fetch(
                            post["url"],
                            method=post.get("method") or "POST",
                            data=post.get("post_data") or None,
                            timeout=ms,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("POST 回放失败，已保留页面导航结果", exc_info=True)

                # Ensure we end on final_url if different
                if final_url and page.url.rstrip("/") != final_url.rstrip("/"):
                    try:
                        last_response = await page.goto(
                            final_url, wait_until="domcontentloaded", timeout=ms
                        )
                    except Exception:  # noqa: BLE001
                        pass

                content = await page.content()
                http_status = last_response.status if last_response else None
                text_lower = content.lower()
                already = ("已签到", "已经签到", "already", "signed today", "重复签到")
                if any(m.lower() in text_lower or m in content for m in already):
                    return CheckInResult(
                        site_name=site.name,
                        status=CheckInStatus.ALREADY,
                        message="今日已签到（录制回放）",
                        http_status=http_status,
                        site_id=site.id,
                        mode=CheckInMode.RECORDED.value,
                    )
                keywords = site.success_keywords or []
                if keywords:
                    matched = any(kw in content or kw.lower() in text_lower for kw in keywords)
                    if matched:
                        return CheckInResult(
                            site_name=site.name,
                            status=CheckInStatus.SUCCESS,
                            message="录制回放签到成功",
                            http_status=http_status,
                            site_id=site.id,
                            mode=CheckInMode.RECORDED.value,
                        )
                    snippet = content[:200].replace("\n", " ")
                    return CheckInResult(
                        site_name=site.name,
                        status=CheckInStatus.FAILED,
                        message=f"未匹配成功关键字；片段: {snippet}",
                        http_status=http_status,
                        site_id=site.id,
                        mode=CheckInMode.RECORDED.value,
                    )
                # Default: navigation succeeded
                if http_status and http_status >= 400:
                    return CheckInResult(
                        site_name=site.name,
                        status=CheckInStatus.FAILED,
                        message=f"HTTP {http_status}",
                        http_status=http_status,
                        site_id=site.id,
                        mode=CheckInMode.RECORDED.value,
                    )
                return CheckInResult(
                    site_name=site.name,
                    status=CheckInStatus.SUCCESS,
                    message=f"录制回放完成 → {page.url}",
                    http_status=http_status or 200,
                    site_id=site.id,
                    mode=CheckInMode.RECORDED.value,
                )
            finally:
                await browser.close()
