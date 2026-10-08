"""Replay a previously recorded browser check-in flow.

Replay = restore storage_state → open the recorded pages → detect a logged-out
state → (at most once per run) log in again with the saved, encrypted
credentials → persist the refreshed session → perform the check-in by
re-submitting the real form (fresh CSRF tokens) or, failing that, the recorded
request with fresh tokens scraped from the page.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from checkin.adapters.base import Adapter
from checkin.browser.flow import (
    build_replay_body,
    detect_logged_out,
    norm_url,
    plan_replay,
    relogin_failure_hint,
    scrape_csrf_tokens,
)
from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig

logger = logging.getLogger(__name__)

RELOGIN_OK_MSG = "登录已过期，已自动重新登录"
SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"], button:not([type])'
ALREADY_MARKERS = ("已签到", "已经签到", "already", "signed today", "重复签到")

_FIND_FORM_JS = """
(target) => {
  const norm = (u) => {
    try { const x = new URL(u, location.href); return (x.origin + x.pathname).replace(/\\/$/, '').toLowerCase(); }
    catch (e) { return ''; }
  };
  const forms = Array.from(document.forms);
  for (let i = 0; i < forms.length; i++) {
    const f = forms[i];
    const m = String(f.getAttribute('method') || 'get').toLowerCase();
    if (m !== target.method) continue;
    if (norm(f.getAttribute('action') || location.href) === norm(target.url)) return i;
  }
  return -1;
}
"""

_PASSWORD_VISIBLE_JS = """
() => Array.from(document.querySelectorAll('input[type="password"]'))
  .some((e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length))
"""

_MARK_USER_INPUT_JS = """
(el) => {
  const scope = el.form || document;
  let cand = null;
  for (const i of Array.from(scope.querySelectorAll('input'))) {
    if (i === el) break;
    const t = String(i.type || 'text').toLowerCase();
    const visible = !!(i.offsetWidth || i.offsetHeight || i.getClientRects().length);
    if (['text', 'email', 'tel', 'number'].includes(t) && visible) cand = i;
  }
  if (!cand) return false;
  cand.setAttribute('data-checkin-user', '1');
  return true;
}
"""


def _run_coro_blocking(coro: Any) -> Any:
    """Run a coroutine to completion even if this thread already has a loop.

    Manual "立即签到" may be invoked from an async web handler; asyncio.run()
    would then raise, so fall back to a short-lived helper thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=runner, name="recorded-replay", daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _css_attr(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class RecordedAdapter(Adapter):
    """mode=recorded: restore Playwright storage_state and replay the flow."""

    def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        return self.do_click(site, timeout=timeout)

    def do_visit(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        return self.do_click(site, timeout=timeout)

    def _result(self, site: SiteConfig, status: CheckInStatus, message: str,
                http_status: int | None = None) -> CheckInResult:
        return CheckInResult(
            site_name=site.name,
            status=status,
            message=message,
            http_status=http_status,
            site_id=site.id,
            mode=CheckInMode.RECORDED.value,
        )

    def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
        if site.id is None:
            return self._result(site, CheckInStatus.FAILED, "录制回放需要已保存的站点 id")
        try:
            from checkin.db import get_db

            flow = get_db().get_recorded_flow(site.id)
        except Exception as exc:  # noqa: BLE001
            return self._result(site, CheckInStatus.FAILED, f"读取录制流程失败: {exc}")
        if not flow:
            return self._result(
                site, CheckInStatus.FAILED, "尚未录制流程，请先在「浏览器录制」中完成录制"
            )
        try:
            return _run_coro_blocking(self._replay(site, flow, timeout=timeout))
        except Exception as exc:  # noqa: BLE001
            logger.exception("recorded replay failed for %s", site.name)
            return self._result(site, CheckInStatus.FAILED, str(exc))

    # ------------------------------------------------------------------ replay

    async def _replay(self, site: SiteConfig, flow: dict[str, Any], *, timeout: int) -> CheckInResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("未安装 playwright，无法回放录制流程") from exc
        from checkin.crypto import SecretDecryptError
        from checkin.db import get_db

        db = get_db()
        plan = plan_replay(flow.get("steps") or [], flow.get("final_url") or "")
        cred = db.get_login_credential(site.id) if site.id is not None else None
        login_meta: dict[str, Any] = (cred or {}).get("login_step") or plan.get("login") or {}
        login_urls = set(plan["login_urls"])
        for u in (login_meta.get("page_url"), login_meta.get("url")):
            if u:
                login_urls.add(u)
        targets = plan["nav_targets"] or [self.build_url(site)]
        storage = flow.get("storage_state") or {}
        ms = max(timeout, 30) * 1000
        state: dict[str, Any] = {"relogged": False}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            try:
                context = await browser.new_context(
                    storage_state=storage if storage else None, ignore_https_errors=True
                )
                page = await context.new_page()

                async def warm() -> tuple[Any, str | None]:
                    resp = None
                    for url in targets:
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=ms)
                        reason = await self._logged_out_reason(page, resp, login_urls, url, site.login_check)
                        if reason:
                            return resp, reason
                    return resp, None

                async def relogin(reason: str) -> CheckInResult | None:
                    """None on success, a FAILED result otherwise. Max once per run."""
                    if state["relogged"]:
                        return self._result(
                            site, CheckInStatus.FAILED,
                            f"重新登录后仍处于未登录状态（{reason}），请重新录制该站点",
                        )
                    state["relogged"] = True
                    if not cred:
                        return self._result(
                            site, CheckInStatus.FAILED,
                            f"登录已过期（{reason}），且未保存登录凭据，请在「浏览器录制」中重新录制该站点",
                        )
                    logger.info("[%s] 检测到登录已过期（%s），尝试自动重新登录", site.name, reason)
                    try:
                        passwords = db.decrypt_login_passwords(site.id)
                    except SecretDecryptError as exc:
                        db.mark_relogin(site.id, False, str(exc))
                        return self._result(site, CheckInStatus.FAILED, f"登录已过期，{exc}")
                    ok, detail = await self._perform_login(
                        page, context, login_meta, cred.get("username") or "", passwords,
                        ms=ms, login_urls=login_urls, verify_url=targets[0],
                        login_check=site.login_check,
                    )
                    passwords = {}
                    if not ok:
                        db.mark_relogin(site.id, False, detail)
                        logger.warning("[%s] 自动重新登录失败：%s", site.name, detail)
                        return self._result(
                            site, CheckInStatus.FAILED,
                            f"登录已过期，自动重新登录失败：{detail}。请在「浏览器录制」中重新录制该站点",
                        )
                    db.mark_relogin(site.id, True, "自动重新登录成功")
                    fresh = await context.storage_state()
                    db.update_flow_storage(site.id, storage_state=fresh, cookies=fresh.get("cookies"))
                    logger.info("[%s] %s", site.name, RELOGIN_OK_MSG)
                    return None

                resp, reason = await warm()
                if reason:
                    failure = await relogin(reason)
                    if failure:
                        return failure
                    resp, reason = await warm()
                    if reason:
                        return self._result(
                            site, CheckInStatus.FAILED,
                            f"重新登录后仍处于未登录状态（{reason}），请重新录制该站点",
                        )

                outcome = await self._do_checkin(page, context, plan, resp, ms)
                out_reason = detect_logged_out(
                    url=outcome.get("url"), status=outcome.get("status"), html=outcome.get("html"),
                    login_urls=login_urls, target_url=(plan.get("checkin") or {}).get("url"),
                )
                if out_reason and not state["relogged"]:
                    failure = await relogin(out_reason)
                    if failure:
                        return failure
                    resp, _ = await warm()
                    outcome = await self._do_checkin(page, context, plan, resp, ms)
                elif out_reason:
                    return self._result(
                        site, CheckInStatus.FAILED,
                        f"签到请求显示未登录（{out_reason}），请重新录制该站点",
                        outcome.get("status"),
                    )

                result = self._evaluate(site, outcome)
                if state["relogged"] and cred:
                    result.message = f"{RELOGIN_OK_MSG}；{result.message}"
                    if result.ok:
                        fresh = await context.storage_state()
                        db.update_flow_storage(site.id, storage_state=fresh, cookies=fresh.get("cookies"))
                return result
            finally:
                await browser.close()

    # ------------------------------------------------------------------ helpers

    async def _logged_out_reason(self, page: Any, resp: Any, login_urls: set[str],
                                 target: str, login_check: str) -> str | None:
        try:
            html = await page.content()
        except Exception:  # noqa: BLE001
            html = ""
        try:
            pw_visible = bool(await page.evaluate(_PASSWORD_VISIBLE_JS))
        except Exception:  # noqa: BLE001
            pw_visible = None
        check_ok = None
        if login_check.startswith("css:"):
            try:
                check_ok = await page.locator(login_check[4:].strip()).count() > 0
            except Exception:  # noqa: BLE001
                check_ok = False
        return detect_logged_out(
            url=page.url, status=resp.status if resp else None, html=html,
            login_urls=login_urls, target_url=target, login_check=login_check,
            login_check_ok=check_ok, password_visible=pw_visible,
        )

    async def _fill_login_form(self, page: Any, meta: dict[str, Any], username: str,
                               passwords: dict[str, str]) -> Any:
        """Fill the login form in the real page. Returns the password locator or None."""
        pw_loc = None
        for name, value in passwords.items():
            loc = page.locator(f'input[name="{_css_attr(name)}"]').first
            if await loc.count() and await loc.is_visible():
                await loc.fill(value)
                pw_loc = pw_loc or loc
        if pw_loc is None and passwords:
            loc = page.locator('input[type="password"]').first
            if await loc.count() and await loc.is_visible():
                await loc.fill(next(iter(passwords.values())))
                pw_loc = loc
        if pw_loc is None:
            return None
        if username:
            filled = False
            uf = meta.get("username_field") or ""
            if uf:
                loc = page.locator(f'[name="{_css_attr(uf)}"]').first
                if await loc.count() and await loc.is_visible():
                    await loc.fill(username)
                    filled = True
            if not filled and await pw_loc.evaluate(_MARK_USER_INPUT_JS):
                await page.locator('[data-checkin-user="1"]').first.fill(username)
        return pw_loc

    async def _perform_login(self, page: Any, context: Any, meta: dict[str, Any], username: str,
                             passwords: dict[str, str], *, ms: int, login_urls: set[str],
                             verify_url: str, login_check: str) -> tuple[bool, str]:
        from playwright.async_api import TimeoutError as PWTimeout

        if not passwords:
            return False, "未找到已保存的密码"
        page_url = meta.get("page_url") or meta.get("url")
        if not page_url:
            return False, "录制中没有登录页地址"
        try:
            await page.goto(page_url, wait_until="domcontentloaded", timeout=ms)
        except Exception as exc:  # noqa: BLE001
            return False, f"打开登录页失败：{type(exc).__name__}"

        pw_loc = await self._fill_login_form(page, meta, username, passwords)
        if pw_loc is not None:
            # Preferred: submit through the browser (fresh CSRF, JS handlers)
            form = pw_loc.locator("xpath=ancestor::form[1]")
            submit = form.locator(SUBMIT_SELECTOR).first if await form.count() else None
            try:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=min(ms, 20000)):
                    if submit is not None and await submit.count():
                        await submit.click()
                    else:
                        await pw_loc.press("Enter")
            except PWTimeout:
                try:  # XHR login without a page navigation
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeout:
                    pass
        else:
            # Fallback: raw POST with a CSRF token scraped from the fresh login page
            html = await page.content()
            fresh = scrape_csrf_tokens(html)
            overrides = dict(passwords)
            if meta.get("username_field") and username:
                overrides[meta["username_field"]] = username
            body = build_replay_body(meta.get("post_data") or "", meta.get("content_type"),
                                     overrides=overrides, fresh_tokens=fresh)
            headers = {}
            if meta.get("content_type"):
                headers["content-type"] = meta["content_type"].split(";")[0]
            elif meta.get("body_kind") == "form":
                headers["content-type"] = "application/x-www-form-urlencoded"
            if fresh.get("__meta__"):
                headers["X-CSRF-Token"] = fresh["__meta__"]
            resp = await context.request.fetch(
                meta.get("url") or page_url, method=meta.get("method") or "POST",
                data=body, headers=headers, timeout=ms,
            )
            body = None
            if resp.status >= 400:
                return False, f"登录请求返回 HTTP {resp.status}"
        try:
            after_html = await page.content()
        except Exception:  # noqa: BLE001
            after_html = ""
        resp = await page.goto(verify_url, wait_until="domcontentloaded", timeout=ms)
        reason = await self._logged_out_reason(page, resp, login_urls, verify_url, login_check)
        if reason:
            return False, f"{relogin_failure_hint(after_html, meta)}（{reason}）"
        return True, ""

    async def _do_checkin(self, page: Any, context: Any, plan: dict[str, Any], warm_resp: Any,
                          ms: int) -> dict[str, Any]:
        step = plan.get("checkin")
        if not step:  # visit-style recording: the last page is the check-in
            return {"html": await page.content(),
                    "status": warm_resp.status if warm_resp else None, "url": page.url}
        page_url = step.get("page_url")
        if step.get("resource_type") in ("document", None) and page_url:
            if norm_url(page.url) != norm_url(page_url):
                await page.goto(page_url, wait_until="domcontentloaded", timeout=ms)
            out = await self._submit_matching_form(page, step, ms)
            if out is not None:
                return out
        # Fallback: re-send the recorded request with fresh CSRF values
        fresh = scrape_csrf_tokens(await page.content())
        ct = step.get("content_type") or ""
        body = build_replay_body(step.get("post_data"), ct, fresh_tokens=fresh)
        headers = {}
        if ct:
            headers["content-type"] = ct.split(";")[0] if "multipart" not in ct else ct
        if fresh.get("__meta__"):
            headers["X-CSRF-Token"] = fresh["__meta__"]
        resp = await context.request.fetch(
            step["url"], method=step.get("method") or "POST", data=body or None,
            headers=headers or None, timeout=ms,
        )
        try:
            text = await resp.text()
        except Exception:  # noqa: BLE001
            text = ""
        return {"html": text, "status": resp.status, "url": resp.url}

    async def _submit_matching_form(self, page: Any, step: dict[str, Any], ms: int) -> dict[str, Any] | None:
        idx = await page.evaluate(
            _FIND_FORM_JS, {"url": step["url"], "method": (step.get("method") or "POST").lower()}
        )
        if idx is None or idx < 0:
            return None
        form = page.locator("form").nth(int(idx))
        submit = form.locator(SUBMIT_SELECTOR).first
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=ms) as nav:
            if await submit.count():
                await submit.click()
            else:
                await form.evaluate("(f) => f.requestSubmit ? f.requestSubmit() : f.submit()")
        resp = await nav.value
        return {"html": await page.content(), "status": resp.status if resp else None, "url": page.url}

    def _evaluate(self, site: SiteConfig, outcome: dict[str, Any]) -> CheckInResult:
        content = outcome.get("html") or ""
        http_status = outcome.get("status")
        text_lower = content.lower()
        if any(m.lower() in text_lower or m in content for m in ALREADY_MARKERS):
            return self._result(site, CheckInStatus.ALREADY, "今日已签到（录制回放）", http_status)
        keywords = site.success_keywords or []
        if keywords:
            if any(kw in content or kw.lower() in text_lower for kw in keywords):
                return self._result(site, CheckInStatus.SUCCESS, "录制回放签到成功", http_status)
            snippet = content[:200].replace("\n", " ")
            return self._result(site, CheckInStatus.FAILED, f"未匹配成功关键字；片段: {snippet}", http_status)
        if http_status and http_status >= 400:
            return self._result(site, CheckInStatus.FAILED, f"HTTP {http_status}", http_status)
        return self._result(
            site, CheckInStatus.SUCCESS, f"录制回放完成 → {outcome.get('url') or ''}", http_status or 200
        )
