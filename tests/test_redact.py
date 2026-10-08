"""Redaction of sensitive fields in recorded requests, URLs and logs."""

from __future__ import annotations

import json
import logging

from checkin.redact import (
    REDACTED,
    RedactingFilter,
    classify_field,
    is_password_field,
    parse_fields,
    redact_body,
    redact_step,
    redact_text,
    redact_url,
)


def test_password_field_detection_strict() -> None:
    for name in ("password", "passwd", "pwd", "pass", "user[password]", "userPassword", "login_pwd", "secret"):
        assert is_password_field(name), name
    for name in ("passage_id", "username", "compass", "bypass_cache", "email"):
        assert not is_password_field(name), name


def test_classify_field() -> None:
    assert classify_field("password") == "password"
    assert classify_field("csrf_token") == "csrf"
    assert classify_field("_token") == "csrf"
    assert classify_field("authenticity_token") == "csrf"
    assert classify_field("formhash") == "csrf"
    for name in ("access_token", "session", "sessionid", "cookie", "Authorization", "api_key",
                 "otp", "code", "sms_code", "phone", "mobile", "id_card", "idcard", "email"):
        assert classify_field(name) in ("sensitive", "csrf"), name
    for name in ("username", "remember", "status_code", "zipcode", "page", "q"):
        assert classify_field(name) is None, name


def test_redact_form_body() -> None:
    out = redact_body("username=alice&password=hunter2&csrf_token=abc&remember=1&phone=13800000000")
    assert "hunter2" not in out and "abc" not in out and "13800000000" not in out
    assert "username=alice" in out and "remember=1" in out
    assert f"password={REDACTED}" in out


def test_redact_json_body_nested() -> None:
    body = json.dumps({"user": {"email": "a@b.c", "password": "pw"}, "otp": "123456", "keep": 1,
                       "list": [{"token": "t"}]})
    data = json.loads(redact_body(body, "application/json"))
    assert data["user"]["password"] == REDACTED and data["user"]["email"] == REDACTED
    assert data["otp"] == REDACTED and data["keep"] == 1 and data["list"][0]["token"] == REDACTED


def test_redact_multipart() -> None:
    body = ("--XyZ\r\nContent-Disposition: form-data; name=\"password\"\r\n\r\nhunter2\r\n"
            "--XyZ\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nhello\r\n--XyZ--\r\n")
    out = redact_body(body, "multipart/form-data; boundary=XyZ")
    assert "hunter2" not in out and "hello" in out
    assert parse_fields(body, "multipart/form-data")["password"] == "hunter2"


def test_extra_fields_and_idempotent() -> None:
    out = redact_body("p=topsecret&u=bob", "", extra_fields=["p"])
    assert "topsecret" not in out and "u=bob" in out
    assert redact_body(out, "", extra_fields=["p"]) == out


def test_redact_url_query() -> None:
    assert redact_url("https://x/cb?token=abc&page=2") == f"https://x/cb?token={REDACTED}&page=2"
    assert redact_url("https://x/a?page=2") == "https://x/a?page=2"


def test_redact_text_and_log_filter() -> None:
    line = 'body password=hunter2&x=1 Authorization: Bearer abc.def {"api_key": "k1"} csrf_token=zz'
    out = redact_text(line)
    for secret in ("hunter2", "abc.def", "k1", "zz"):
        assert secret not in out
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "login %s", ("password=hunter2",), None)
    RedactingFilter().filter(rec)
    assert "hunter2" not in rec.getMessage()


def test_redact_step_uses_declared_password_fields() -> None:
    step = {"kind": "login", "url": "https://x/login?sid=1", "post_data": "u=a&p=secret1",
            "password_fields": ["p"], "_raw": "u=a&p=secret1"}
    out = redact_step(step)
    assert "_raw" not in out and "secret1" not in json.dumps(out)
