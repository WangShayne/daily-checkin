"""Login detection during recording, replay planning, expiry detection."""

from __future__ import annotations

import json

from checkin.browser.flow import (
    build_replay_body,
    detect_logged_out,
    finalize_recording,
    plan_replay,
    relogin_failure_hint,
    scrape_csrf_tokens,
    upgrade_legacy_steps,
)

FORM = "application/x-www-form-urlencoded"


def _raw_session() -> list[dict]:
    return [
        {"kind": "navigate", "url": "http://t/login"},
        {"kind": "request", "method": "POST", "url": "http://t/login", "resource_type": "document",
         "content_type": FORM, "page_url": "http://t/login", "t": 10.0,
         "_raw": "csrf_token=abc123&username=alice&password=S3cret%21"},
        {"kind": "navigate", "url": "http://t/dashboard"},
        {"kind": "request", "method": "POST", "url": "http://t/checkin", "resource_type": "document",
         "content_type": FORM, "page_url": "http://t/dashboard", "t": 20.0, "_raw": "formhash=ff01&op=sign"},
        {"kind": "navigate", "url": "http://t/checkin", "via_post": True},
    ]


def test_login_detected_and_secrets_removed() -> None:
    steps, login = finalize_recording(_raw_session())
    assert login is not None
    assert login["username"] == "alice"
    assert login["passwords"] == {"password": "S3cret!"}
    meta = login["meta"]
    assert meta["url"] == "http://t/login" and meta["page_url"] == "http://t/login"
    assert meta["password_fields"] == ["password"] and meta["username_field"] == "username"
    assert meta["csrf_fields"] == ["csrf_token"]
    dumped = json.dumps(steps)
    assert "S3cret" not in dumped and "abc123" not in dumped and "ff01" not in dumped
    assert "_raw" not in dumped
    assert steps[1]["kind"] == "login" and steps[3]["kind"] == "request"


def test_login_detected_from_form_input_type() -> None:
    raw = [{"kind": "request", "method": "POST", "url": "http://t/auth", "resource_type": "document",
            "content_type": FORM, "page_url": "http://t/signin", "t": 5.0, "_raw": "acct=bob&k=topsecret"}]
    submits = [{"action": "http://t/auth", "method": "post", "page_url": "http://t/signin", "t": 5.2,
                "fields": [{"name": "acct", "type": "text"}, {"name": "k", "type": "password"}]}]
    steps, login = finalize_recording(raw, submits)
    assert login and login["passwords"] == {"k": "topsecret"} and login["username"] == "bob"
    assert "topsecret" not in json.dumps(steps)


def test_json_login_detected() -> None:
    raw = [{"kind": "request", "method": "POST", "url": "http://t/api/login", "resource_type": "fetch",
            "content_type": "application/json", "page_url": "http://t/", "t": 1.0,
            "_raw": json.dumps({"email": "a@b.c", "password": "pw1"})}]
    steps, login = finalize_recording(raw)
    assert login and login["passwords"] == {"password": "pw1"} and login["username"] == "a@b.c"
    assert "pw1" not in json.dumps(steps) and "a@b.c" not in json.dumps(steps)


def test_non_login_post_not_marked_login() -> None:
    raw = [{"kind": "request", "method": "POST", "url": "http://t/checkin", "resource_type": "xhr",
            "content_type": FORM, "t": 1.0, "_raw": "passage_id=3&token=zzz"}]
    steps, login = finalize_recording(raw)
    assert login is None and steps[0]["kind"] == "request"
    assert "zzz" not in steps[0]["post_data"]


def test_plan_replay_skips_login_and_post_results() -> None:
    steps, _ = finalize_recording(_raw_session())
    plan = plan_replay(steps, "http://t/checkin")
    assert plan["nav_targets"] == ["http://t/dashboard"]
    assert plan["checkin"]["url"] == "http://t/checkin"
    assert plan["login"]["kind"] == "login"
    assert "http://t/login" in plan["login_urls"]


def test_detect_logged_out_variants() -> None:
    kw = dict(login_urls=["http://t/login"], target_url="http://t/dashboard")
    assert detect_logged_out(url="http://t/login?next=/dashboard", status=200, html="", **kw)
    assert detect_logged_out(url="http://t/dashboard", status=401, html="", **kw) == "HTTP 401"
    assert detect_logged_out(url="http://t/dashboard", status=200,
                             html='<form><input type="password" name="p"></form>', **kw)
    assert detect_logged_out(url="http://t/dashboard", status=200, html="<p>欢迎 退出</p>", **kw) is None
    # per-site keyword (logged-in indicator)
    assert detect_logged_out(url="http://t/dashboard", status=200, html="<p>hi</p>",
                             login_check="退出", **kw)
    assert detect_logged_out(url="http://t/dashboard", status=200, html="<p>退出</p>",
                             login_check="退出", **kw) is None
    # css selector evaluated by the caller
    assert detect_logged_out(url="http://t/dashboard", status=200, html="", login_check="css:.avatar",
                             login_check_ok=False, **kw)
    assert detect_logged_out(url="http://t/dashboard", status=200, html='<input type="password">',
                             login_check="css:.avatar", login_check_ok=True, **kw) is None


def test_scrape_and_rebuild_with_fresh_csrf() -> None:
    html = ('<form><input type="hidden" name="csrf_token" value="NEW1">'
            '<meta name="csrf-token" content="META2"></form>')
    tokens = scrape_csrf_tokens(html)
    assert tokens == {"csrf_token": "NEW1", "__meta__": "META2"}
    body = build_replay_body("csrf_token=***&username=alice&password=***&remember=1", FORM,
                             overrides={"password": "pw", "username": "alice"}, fresh_tokens=tokens)
    assert body == "csrf_token=NEW1&username=alice&password=pw&remember=1"
    # unknown redacted secrets are dropped instead of sending "***"
    assert build_replay_body("op=sign&token=***", FORM) == "op=sign"
    js = build_replay_body('{"_token": "***", "a": 1}', "application/json", fresh_tokens={"__meta__": "M"})
    assert json.loads(js) == {"_token": "M", "a": 1}


def test_relogin_failure_hints() -> None:
    assert "验证码" in relogin_failure_hint("请输入验证码")
    assert "密码" in relogin_failure_hint("用户名或密码错误")
    assert relogin_failure_hint("", {"fields": ["user", "password", "sms_code"]}).startswith("登录页需要")


def test_upgrade_legacy_plaintext_steps() -> None:
    legacy = [
        {"kind": "navigate", "url": "http://t/login"},
        {"kind": "request", "method": "POST", "url": "http://t/login", "post_data": "username=demo&password=demo"},
        {"kind": "navigate", "url": "http://t/dashboard"},
    ]
    steps, login = upgrade_legacy_steps(legacy)
    assert login and login["passwords"] == {"password": "demo"}
    assert login["meta"]["page_url"] == "http://t/login"
    assert "password=demo" not in json.dumps(steps)
