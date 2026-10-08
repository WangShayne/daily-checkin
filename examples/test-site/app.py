"""Tiny session-cookie check-in site for E2E testing daily-checkin."""

from __future__ import annotations

import os
import secrets
import time

from flask import Flask, redirect, render_template_string, request, session, url_for
from markupsafe import escape

app = Flask(__name__)
app.secret_key = "checkin-test-secret"

USER = "demo"
PASS = "demo"
# Second account with a distinctive password, used by the re-login E2E to grep
# daily-checkin's data volume / logs for plaintext leaks.
ACCOUNTS = {USER: PASS, "alice": "Wonderland-2026!"}

# Server-side session generation: bumping it (POST /admin/expire) invalidates
# every existing session cookie, simulating an expired login.
EPOCH = int(time.time())
# Optional max session age in seconds (0 = unlimited)
SESSION_LIFETIME = int(os.environ.get("SESSION_LIFETIME", "0"))


def logged_in() -> bool:
    if not session.get("user") or session.get("epoch") != EPOCH:
        return False
    if SESSION_LIFETIME and time.time() - float(session.get("login_at", 0)) > SESSION_LIFETIME:
        return False
    return True

BASE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <style>
    body { font-family: sans-serif; max-width: 480px; margin: 2rem auto; }
    .ok { color: #065f46; background: #ecfdf5; padding: .75rem; border-radius: 8px; }
    .err { color: #991b1b; background: #fef2f2; padding: .75rem; border-radius: 8px; }
    button, .btn { background: #2563eb; color: #fff; border: 0; padding: .5rem 1rem;
                   border-radius: 6px; cursor: pointer; text-decoration: none; }
    input { display: block; width: 100%; margin: .35rem 0 .75rem; padding: .45rem; }
  </style>
</head>
<body>
  <h1>{{ title }}</h1>
  {% if msg %}<div class="{{ 'ok' if ok else 'err' }}">{{ msg }}</div>{% endif %}
  {{ body|safe }}
</body>
</html>
"""


def page(title: str, body: str, msg: str = "", ok: bool = True) -> str:
    return render_template_string(BASE, title=title, body=body, msg=msg, ok=ok)


@app.get("/")
def index():
    if logged_in():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


def login_form(username: str = "", password: str = "") -> str:
    token = secrets.token_hex(16)
    session["csrf"] = token
    return f"""<form method="post" action="/login">
          <input type="hidden" name="csrf_token" value="{token}">
          <label>用户名<input name="username" value="{escape(username)}"></label>
          <label>密码<input name="password" type="password" value="{escape(password)}"></label>
          <button type="submit">登录</button>
        </form>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        expected = session.pop("csrf", None)
        if not expected or not secrets.compare_digest(sent, expected):
            return page("登录", login_form(), msg="CSRF 校验失败，请刷新页面重试", ok=False), 400
        user = request.form.get("username", "")
        if user in ACCOUNTS and request.form.get("password") == ACCOUNTS[user]:
            session.clear()
            session["user"] = user
            session["epoch"] = EPOCH
            session["login_at"] = time.time()
            session["checked_in"] = False
            return redirect(url_for("dashboard"))
        return page("登录", login_form(), msg="用户名或密码错误", ok=False)
    # ?prefill=alice pre-fills the second account (E2E convenience)
    prefill = request.args.get("prefill", USER)
    if prefill not in ACCOUNTS:
        prefill = USER
    return page("登录", login_form(prefill, ACCOUNTS[prefill]) + "<p>测试账号：demo / demo</p>")


@app.get("/dashboard")
def dashboard():
    if not logged_in():
        return redirect(url_for("login"))
    status = "已签到" if session.get("checked_in") else "今日未签到"
    return page(
        "控制台",
        f"""<p>你好，{session['user']}。状态：<strong>{status}</strong></p>
        <form method="post" action="/checkin">
          <button type="submit">签到</button>
        </form>
        <p><a href="/visit">访问签到页</a> · <a href="/logout">退出</a></p>""",
    )


@app.post("/checkin")
def checkin():
    if not logged_in():
        return redirect(url_for("login")), 401
    if session.get("checked_in"):
        return page("签到", '<p><a class="btn" href="/dashboard">返回</a></p>',
                    msg="今日已签到", ok=True)
    session["checked_in"] = True
    return page(
        "签到",
        '<p><a class="btn" href="/dashboard">返回</a></p>',
        msg="签到成功",
        ok=True,
    )


@app.get("/visit")
def visit():
    """Visit-only: opening this page while logged in counts as check-in."""
    if not logged_in():
        return redirect(url_for("login"))
    session["checked_in"] = True
    return page(
        "访问签到",
        '<p><a class="btn" href="/dashboard">返回控制台</a></p>',
        msg="访问成功，已记为签到",
        ok=True,
    )


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/expire", methods=["GET", "POST"])
def admin_expire():
    """Invalidate all sessions (simulates login expiry for E2E tests)."""
    global EPOCH
    EPOCH += 1
    return {"expired": True, "epoch": EPOCH}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
