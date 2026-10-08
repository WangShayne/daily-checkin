"""Pure helpers for recorded flows: login detection, replay planning, expiry checks.

Kept free of Playwright so they are unit-testable.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable
from urllib.parse import urlencode, urlsplit

from checkin.redact import (
    REDACTED,
    body_kind,
    is_csrf_field,
    is_password_field,
    parse_fields,
    redact_body,
    redact_url,
)

MAX_POST_CHARS = 2000
_USERNAME_HINT = re.compile(r"user|login|account|email|mail|phone|mobile|name|uid|acct|zhanghao", re.I)
_CAPTCHA_HINT = re.compile(
    r"验证码|captcha|短信|sms|vcode|verify_?code|动态码|二次验证|两步验证|双重验证|2fa|two-factor|one-time|otp|verification code|滑块|人机验证",
    re.I,
)
_BAD_PASSWORD_HINT = re.compile(r"密码错误|用户名或密码|账号或密码|incorrect|invalid (user|pass|cred)|wrong password", re.I)
_PASSWORD_INPUT_RE = re.compile(r"<input\b[^>]*\btype\s*=\s*[\"']?password\b", re.I)


def norm_url(url: str | None) -> str:
    """scheme://host/path without query / fragment / trailing slash (for matching)."""
    if not url:
        return ""
    try:
        p = urlsplit(url)
    except ValueError:
        return url
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc}{path}".lower()


def url_path(url: str | None) -> str:
    try:
        return (urlsplit(url or "").path.rstrip("/") or "/").lower()
    except ValueError:
        return ""


# ------------------------------------------------------------ recording


def _match_submit(step: dict[str, Any], submits: list[dict[str, Any]]) -> dict[str, Any] | None:
    best = None
    best_dt = 10.0
    for s in submits:
        if (s.get("method") or "get").lower() != (step.get("method") or "").lower():
            continue
        if norm_url(s.get("action")) != norm_url(step.get("url")):
            continue
        dt = abs(float(s.get("t", 0)) - float(step.get("t", 0)))
        if dt <= best_dt:
            best, best_dt = s, dt
    return best


def guess_username_field(
    fields: dict[str, str],
    password_fields: Iterable[str],
    form_inputs: list[dict[str, Any]] | None = None,
) -> str:
    pw = set(password_fields)
    candidates = [n for n in fields if n not in pw and not is_csrf_field(n)]
    # 1) from the submitted form: last text-like input before the first password input
    if form_inputs:
        last = ""
        for inp in form_inputs:
            t = (inp.get("type") or "text").lower()
            name = inp.get("name") or ""
            if t == "password" or name in pw:
                break
            if t in ("text", "email", "tel", "number", "") and name in fields:
                last = name
        if last:
            return last
    # 2) by name hint
    for n in candidates:
        if _USERNAME_HINT.search(n) and fields.get(n):
            return n
    # 3) first non-empty remaining field
    for n in candidates:
        if fields.get(n) and fields.get(n) != REDACTED:
            return n
    return ""


def finalize_recording(
    raw_steps: list[dict[str, Any]],
    submits: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Turn raw in-memory steps into storable steps + the detected login.

    Returns ``(steps, login)`` where ``steps`` contain no plaintext secrets
    (internal ``_raw`` bodies are dropped) and ``login`` is ``None`` or::

        {"meta": {...login step without secrets...},
         "username": "...", "passwords": {field: value}}

    Only ``login["passwords"]`` is secret; callers must encrypt it.
    """
    submits = submits or []
    steps: list[dict[str, Any]] = []
    login: dict[str, Any] | None = None
    for raw in raw_steps:
        step = {k: v for k, v in raw.items() if k not in ("_raw", "t")}
        if raw.get("kind") in ("request", "login"):
            body = raw.get("_raw")
            if body is None:
                body = raw.get("post_data")
            ct = raw.get("content_type") or ""
            fields = parse_fields(body, ct)
            form = _match_submit(raw, submits)
            form_inputs = (form or {}).get("fields") or []
            typed_pw = {i.get("name") for i in form_inputs if (i.get("type") or "").lower() == "password"}
            pw_fields = [n for n in fields if is_password_field(n) or n in typed_pw]
            pw_fields = [n for n in pw_fields if fields.get(n) and fields.get(n) != REDACTED]
            csrf_fields = [n for n in fields if is_csrf_field(n)]
            if pw_fields:
                user_field = guess_username_field(fields, pw_fields, form_inputs)
                step.update(
                    {
                        "kind": "login",
                        "fields": list(fields.keys()),
                        "password_fields": pw_fields,
                        "username_field": user_field,
                        "csrf_fields": csrf_fields,
                        "body_kind": body_kind(body, ct),
                        "page_url": (form or {}).get("page_url") or raw.get("page_url") or "",
                    }
                )
                login = {
                    "username": fields.get(user_field, "") if user_field else "",
                    "passwords": {n: fields[n] for n in pw_fields},
                }
            elif csrf_fields:
                step["csrf_fields"] = csrf_fields
            red = redact_body(body, ct, pw_fields + csrf_fields)
            if red and len(red) > MAX_POST_CHARS:
                red = red[:MAX_POST_CHARS] + "…"
            step["post_data"] = red
        if step.get("url"):
            step["url"] = redact_url(step["url"])
        if step.get("page_url"):
            step["page_url"] = redact_url(step["page_url"])
        steps.append(step)
    if login is not None:
        meta = next(s for s in reversed(steps) if s.get("kind") == "login")
        login["meta"] = {
            k: meta.get(k)
            for k in (
                "url", "method", "content_type", "resource_type", "fields", "password_fields",
                "username_field", "csrf_fields", "body_kind", "page_url", "post_data",
            )
        }
    return steps, login


def upgrade_legacy_steps(
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Migration helper for flows saved before redaction existed.

    Treats plaintext ``post_data`` as the raw body and fills ``page_url`` from
    the preceding navigation, then runs :func:`finalize_recording`.
    """
    raw: list[dict[str, Any]] = []
    last_nav = ""
    for s in steps:
        s2 = dict(s)
        if s2.get("kind") == "navigate":
            last_nav = s2.get("url") or last_nav
        elif s2.get("kind") in ("request", "login"):
            s2.setdefault("page_url", last_nav)
            if isinstance(s2.get("post_data"), str) and s2["post_data"].endswith("…"):
                s2["post_data"] = s2["post_data"][:-1]
        raw.append(s2)
    return finalize_recording(raw)


# ------------------------------------------------------------ replay plan


def plan_replay(steps: list[dict[str, Any]], final_url: str = "") -> dict[str, Any]:
    """Which URLs to warm up, which request is the check-in, login info."""
    login_idx = -1
    login_step = None
    for i, s in enumerate(steps):
        if s.get("kind") == "login":
            login_idx, login_step = i, s
    login_paths = set()
    if login_step:
        for u in (login_step.get("page_url"), login_step.get("url")):
            if u:
                login_paths.add(norm_url(u))

    # Navigations that are the result of a form POST can't be re-opened with GET
    nav_targets: list[str] = []
    for i, s in enumerate(steps):
        if s.get("kind") != "navigate" or not s.get("url") or i < login_idx:
            continue
        if s.get("via_post"):
            continue
        prev = steps[i - 1] if i > 0 else {}
        if prev.get("kind") in ("request", "login") and prev.get("resource_type") in ("document", None) \
                and norm_url(prev.get("url")) == norm_url(s["url"]) and prev.get("method") in ("POST", "PUT"):
            continue
        if norm_url(s["url"]) in login_paths:
            continue
        if nav_targets and nav_targets[-1] == s["url"]:
            continue
        nav_targets.append(s["url"])
    nav_targets = nav_targets[-5:]
    if not nav_targets and final_url and norm_url(final_url) not in login_paths:
        post_urls = {norm_url(s.get("url")) for s in steps if s.get("kind") == "request"}
        if norm_url(final_url) not in post_urls:
            nav_targets = [final_url]

    checkin = None
    for s in reversed(steps):
        if s.get("kind") == "request" and s.get("method") in ("POST", "PUT"):
            checkin = s
            break
    return {
        "nav_targets": nav_targets,
        "checkin": checkin,
        "login": login_step,
        "login_urls": sorted(login_paths),
    }


# ------------------------------------------------------------ expiry


def has_password_input(html: str | None) -> bool:
    return bool(html) and bool(_PASSWORD_INPUT_RE.search(html))


def detect_logged_out(
    *,
    url: str | None,
    status: int | None,
    html: str | None,
    login_urls: Iterable[str] = (),
    target_url: str | None = None,
    login_check: str = "",
    login_check_ok: bool | None = None,
    password_visible: bool | None = None,
) -> str | None:
    """Return a reason string if the page looks logged-out, else ``None``.

    ``login_check`` is an optional per-site *logged-in* indicator: plain text
    that must appear on the page, or ``css:<selector>`` (the caller evaluates
    the selector and passes ``login_check_ok``).
    """
    if status in (401, 403):
        return f"HTTP {status}"
    login_set = {norm_url(u) for u in login_urls if u}
    here = norm_url(url)
    target = norm_url(target_url)
    if here and here in login_set and here != target:
        return "跳转到了登录页"
    if login_check:
        # Explicit per-site indicator is authoritative when configured
        if login_check.startswith("css:"):
            if login_check_ok is False:
                return f"未找到登录标识 {login_check}"
            if login_check_ok is True:
                return None
        elif html is not None:
            return None if login_check in html else f"页面未包含登录标识「{login_check}」"
    pw = password_visible if password_visible is not None else has_password_input(html)
    if pw:
        return "页面出现密码输入框"
    return None


def relogin_failure_hint(html: str | None, login_meta: dict[str, Any] | None = None) -> str:
    text = html or ""
    fields = " ".join((login_meta or {}).get("fields") or [])
    if _CAPTCHA_HINT.search(text) or _CAPTCHA_HINT.search(fields):
        return "登录页需要验证码 / 短信 / 二次验证，无法自动登录"
    if _BAD_PASSWORD_HINT.search(text):
        return "用户名或密码错误（密码可能已修改）"
    return "自动登录后仍未进入登录状态"


# ------------------------------------------------------------ CSRF / fallback POST

_HIDDEN_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""(\w[\w\-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
_META_RE = re.compile(r"<meta\b[^>]*>", re.I)


def _attrs(tag: str) -> dict[str, str]:
    return {m.group(1).lower(): (m.group(2) or m.group(3) or m.group(4) or "") for m in _ATTR_RE.finditer(tag)}


def scrape_csrf_tokens(html: str | None) -> dict[str, str]:
    """Fresh CSRF-like values from hidden inputs and ``<meta name=csrf-token>``."""
    out: dict[str, str] = {}
    if not html:
        return out
    for tag in _HIDDEN_INPUT_RE.findall(html):
        a = _attrs(tag)
        name = a.get("name") or ""
        if name and is_csrf_field(name) and "value" in a:
            out.setdefault(name, a["value"])
    for tag in _META_RE.findall(html):
        a = _attrs(tag)
        name = (a.get("name") or "").lower()
        if name in ("csrf-token", "_csrf", "csrf_token", "x-csrf-token", "_token") and a.get("content"):
            out.setdefault("__meta__", a["content"])
    return out


def build_replay_body(
    post_data: str | None,
    content_type: str | None,
    *,
    overrides: dict[str, str] | None = None,
    fresh_tokens: dict[str, str] | None = None,
) -> str | None:
    """Rebuild a redacted body: fill overrides / fresh CSRF, drop other ``***``."""
    if post_data is None:
        return None
    overrides = overrides or {}
    fresh = dict(fresh_tokens or {})
    meta_token = fresh.pop("__meta__", None)
    kind = body_kind(post_data, content_type)

    def value_for(name: str, value: Any) -> Any:
        if name in overrides:
            return overrides[name]
        if value == REDACTED:
            if is_csrf_field(name):
                return fresh.get(name) or meta_token
            return None  # unknown secret: drop rather than send "***"
        return value

    if kind == "form":
        from urllib.parse import parse_qsl

        pairs = []
        for k, v in parse_qsl(post_data.rstrip("…"), keep_blank_values=True):
            nv = value_for(k, v)
            if nv is not None:
                pairs.append((k, nv))
        for k, v in overrides.items():
            if k not in {p[0] for p in pairs}:
                pairs.append((k, v))
        return urlencode(pairs)
    if kind == "json":
        data = json.loads(post_data)

        def walk(obj: Any, prefix: str = "") -> Any:
            if isinstance(obj, dict):
                res = {}
                for k, v in obj.items():
                    full = f"{prefix}{k}"
                    if isinstance(v, (dict, list)):
                        res[k] = walk(v, f"{full}.")
                        continue
                    nv = value_for(full, v) if full in overrides else value_for(str(k), v)
                    if nv is not None:
                        res[k] = nv
                return res
            if isinstance(obj, list):
                return [walk(v, prefix) for v in obj]
            return obj

        return json.dumps(walk(data), ensure_ascii=False)
    return post_data
