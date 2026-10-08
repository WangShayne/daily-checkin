"""Detect and redact sensitive values in recorded requests, URLs and logs."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "***"

# Strict, token-based: decides whether a POST is a *login* (field is a password).
_PASSWORD_TOKENS = {"password", "passwd", "passwort", "pwd", "pass", "pw", "secret", "mima"}

# Broad substring match: anything that must never be stored / logged in clear.
_PASSWORD_SUB = r"pass(?:word|wd|wort)?|pwd|secret|mima"
_CSRF_SUB = (
    r"csrf|xsrf|authenticity_?token|requestverificationtoken|formhash|nonce|"
    r"^_?token$|_token$"
)
_SENSITIVE_SUB = (
    r"token|session|sess_?id|^sid$|cookie|authori[sz]ation|^auth$|api_?key|access_?key|"
    r"private_?key|otp|captcha|verify_?code|vcode|sms|^code$|_code$|code$|"
    r"phone|mobile|^tel$|telephone|id_?card|id_?no|idnumber|identity|sfz|shenfenzheng|"
    r"card_?no|bank|cvv|ssn|email"
)
_PASSWORD_RE = re.compile(_PASSWORD_SUB, re.I)
_CSRF_RE = re.compile(_CSRF_SUB, re.I)
_SENSITIVE_RE = re.compile(_SENSITIVE_SUB, re.I)
# Field names whose *value* is never secret even though they match loosely
_ALLOW = {"zipcode", "postcode", "country_code", "countrycode", "lang_code", "status_code",
          "error_code", "response_code", "promo_code", "invite_code_hint", "qrcode_type",
          "remember", "rememberme", "remember_me", "keep_session", "passport_type"}


def _tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name or "")
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def is_password_field(name: str) -> bool:
    """Strict: the field holds a login password (``password``, ``user[pwd]``…)."""
    toks = _tokens(name)
    return any(t in _PASSWORD_TOKENS or t.startswith("password") for t in toks)


def is_csrf_field(name: str) -> bool:
    n = _norm(name)
    return bool(n) and bool(_CSRF_RE.search(n))


def classify_field(name: str) -> str | None:
    """``password`` | ``csrf`` | ``sensitive`` | None."""
    n = _norm(name)
    if not n or n in _ALLOW:
        return None
    if is_password_field(name) or _PASSWORD_RE.search(n):
        return "password"
    if is_csrf_field(n):
        return "csrf"
    if _SENSITIVE_RE.search(n):
        return "sensitive"
    return None


def is_sensitive_field(name: str) -> bool:
    return classify_field(name) is not None


# ---------------------------------------------------------------- parsing


def body_kind(post_data: str | None, content_type: str | None = "") -> str:
    ct = (content_type or "").lower()
    body = (post_data or "").lstrip()
    if "multipart/form-data" in ct or body.startswith("--") and "content-disposition" in body.lower():
        return "multipart"
    if "json" in ct or body[:1] in ("{", "["):
        try:
            json.loads(post_data or "")
            return "json"
        except ValueError:
            pass
    if "=" in body and "\n" not in body.strip():
        return "form"
    return "text"


_MP_PART = re.compile(
    r'(Content-Disposition:\s*form-data;\s*name="(?P<name>[^"]*)"[^\r\n]*\r?\n'
    r"(?:[^\r\n]+\r?\n)*\r?\n)(?P<value>.*?)(?=\r?\n--)",
    re.I | re.S,
)


def parse_fields(post_data: str | None, content_type: str | None = "") -> dict[str, str]:
    """Flat name -> value view of a request body (top-level JSON keys)."""
    if not post_data:
        return {}
    kind = body_kind(post_data, content_type)
    out: dict[str, str] = {}
    if kind == "form":
        for k, v in parse_qsl(post_data, keep_blank_values=True):
            out[k] = v
    elif kind == "json":
        try:
            data = json.loads(post_data)
        except ValueError:
            return {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        if not isinstance(v2, (dict, list)):
                            out[f"{k}.{k2}"] = "" if v2 is None else str(v2)
                elif not isinstance(v, list):
                    out[str(k)] = "" if v is None else str(v)
    elif kind == "multipart":
        for m in _MP_PART.finditer(post_data):
            out[m.group("name")] = m.group("value")
    return out


# ---------------------------------------------------------------- redaction


def _should_redact(name: str, extra: set[str]) -> bool:
    return name in extra or is_sensitive_field(name)


def _redact_json(obj: Any, extra: set[str]) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _should_redact(str(k), extra) and not isinstance(v, (dict, list)):
                out[k] = REDACTED if v not in (None, "") else v
            else:
                out[k] = _redact_json(v, extra)
        return out
    if isinstance(obj, list):
        return [_redact_json(v, extra) for v in obj]
    return obj


def redact_body(
    post_data: str | None,
    content_type: str | None = "",
    extra_fields: Iterable[str] = (),
) -> str | None:
    """Replace values of sensitive fields with ``***`` (form / JSON / multipart)."""
    if not post_data:
        return post_data
    extra = {str(x) for x in extra_fields}
    kind = body_kind(post_data, content_type)
    if kind == "form":
        pairs = parse_qsl(post_data, keep_blank_values=True)
        red = [(k, REDACTED if (_should_redact(k, extra) and v) else v) for k, v in pairs]
        return urlencode(red, safe="*")
    if kind == "json":
        try:
            return json.dumps(_redact_json(json.loads(post_data), extra), ensure_ascii=False)
        except ValueError:
            return redact_text(post_data)
    if kind == "multipart":
        def repl(m: re.Match[str]) -> str:
            if _should_redact(m.group("name"), extra) and m.group("value"):
                return m.group(1) + REDACTED
            return m.group(0)

        return _MP_PART.sub(repl, post_data)
    return redact_text(post_data)


def redact_url(url: str | None) -> str | None:
    """Redact sensitive query-string parameters."""
    if not url or "?" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return redact_text(url)
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(is_sensitive_field(k) for k, _ in pairs):
        return url
    red = [(k, REDACTED if is_sensitive_field(k) and v else v) for k, v in pairs]
    return urlunsplit(parts._replace(query=urlencode(red, safe="*/:")))


_TEXT_KEY = (
    r"[\w.\-\[\]]*?(?:pass(?:word|wd)?|pwd|secret|token|csrf|xsrf|session|cookie|"
    r"authori[sz]ation|api[_-]?key|otp|captcha|formhash)[\w.\-\[\]]*"
)
_TEXT_RE = re.compile(
    r"(?P<key>[\"']?" + _TEXT_KEY + r"[\"']?)(?P<sep>\s*[:=]\s*)"
    r"(?P<q>[\"']?)(?P<val>(?:Bearer\s+|Basic\s+)?[^\"'&\s,;}<>]+)",
    re.I,
)


def redact_text(text: str | None) -> str | None:
    """Best-effort redaction for free text (log lines, error messages)."""
    if not text:
        return text

    def repl(m: re.Match[str]) -> str:
        if m.group("val") == REDACTED:
            return m.group(0)
        return f"{m.group('key')}{m.group('sep')}{m.group('q')}{REDACTED}"

    return _TEXT_RE.sub(repl, text)


def redact_step(step: dict[str, Any]) -> dict[str, Any]:
    """Redact one recorded step in place-safe copy (idempotent)."""
    out = dict(step)
    out.pop("_raw", None)
    extra = set(out.get("password_fields") or []) | set(out.get("csrf_fields") or [])
    if out.get("url"):
        out["url"] = redact_url(out["url"])
    if out.get("page_url"):
        out["page_url"] = redact_url(out["page_url"])
    if out.get("post_data"):
        out["post_data"] = redact_body(out["post_data"], out.get("content_type"), extra)
    return out


def redact_steps(steps: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [redact_step(s) for s in steps or []]


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs secrets from every formatted record."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        red = redact_text(msg)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


_installed = False


def install_log_redaction() -> None:
    """Attach :class:`RedactingFilter` to root + uvicorn handlers (idempotent)."""
    global _installed
    flt = RedactingFilter()
    names = ["", "uvicorn", "uvicorn.error", "uvicorn.access"]
    for name in names:
        lg = logging.getLogger(name)
        for h in lg.handlers:
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(flt)
        if not any(isinstance(f, RedactingFilter) for f in lg.filters):
            lg.addFilter(flt)
    _installed = True
