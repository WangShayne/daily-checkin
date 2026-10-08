<div align="center">

# Daily Check-in

[简体中文](README.md) | **English**

Self-hosted daily check-in for forums, portals and other sites that need a login. One-command Docker deploy, managed from a web UI.

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Jinja-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

<img src="docs/screenshots/dashboard.png" alt="Dashboard" width="860">

</div>

> The UI is in Chinese. Labels used below are given as **中文 (English)**.

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Check-in modes](#check-in-modes)
- [Recording guide](#recording-guide)
- [Automatic re-login & credential encryption](#automatic-re-login--credential-encryption)
- [Configuration & environment variables](#configuration--environment-variables)
- [Troubleshooting](#troubleshooting)
- [Project structure](#project-structure)
- [Development & tests](#development--tests)
- [Security notes](#security-notes)
- [License](#license)

## Features

- **Web UI**: dashboard, add/edit sites, run logs and settings. Includes a dark mode and works in mobile browsers.
- **Three check-in modes**: visit, click (submit) and recorded (replay). See [Check-in modes](#check-in-modes).
- **Embedded browser recording**: Playwright Chromium is streamed into the page over noVNC. Log in and check in once, and the tool replays it automatically.
- **Automatic re-login**: if replay finds the cookies have expired, it logs in again with the **encrypted** saved password (fetching a fresh CSRF token each time), then checks in.
- **Redaction**: passwords, tokens, CSRF values, cookies, phone and ID numbers in recorded requests are stored as `***`, and logs are scrubbed too.
- **Scheduling**: APScheduler runs a daily time or a cron expression, in the Asia/Shanghai timezone.
- **Clear results**: status badges (success / already done today / failed / skipped), today's stats, next run time, and logs you can filter by status or site.
- **Login-protected**: session login via `CHECKIN_USER` / `CHECKIN_PASSWORD`. No management page and no noVNC stream is reachable without signing in.
- **Your data stays local**: sites, cookies and recordings live in the Docker volume `/data` (SQLite).
- **Works offline**: there is no frontend build step and no CDN. CSS, icons and noVNC are all bundled in the image.
- **No GitHub Actions**: it runs on your own machine, so you avoid the risk of abusing GitHub CI.
- **Extensible**: a pluggable adapter pattern (`forum` / `portal` / `http_form` / `browser`) lets you add your own.

## Screenshots

| Dashboard | Dark mode |
|---|---|
| ![Dashboard](docs/screenshots/dashboard.png) | ![Dark mode](docs/screenshots/dashboard-dark.png) |
| **Add site (modes explained)** | **Run logs** |
| ![Add site](docs/screenshots/site-form.png) | ![Run logs](docs/screenshots/logs.png) |
| **Browser recording** | **Recording session (remote browser via noVNC)** |
| ![Recording](docs/screenshots/record.png) | ![Recording session](docs/screenshots/record-session.png) |
| **Login** | **Settings** |
| ![Login](docs/screenshots/login.png) | ![Settings](docs/screenshots/settings.png) |

<details>
<summary>More: empty state, toast, mobile</summary>

| Empty state | Toast | Mobile |
|---|---|---|
| ![Empty](docs/screenshots/dashboard-empty.png) | ![Toast](docs/screenshots/run-toast.png) | <img src="docs/screenshots/mobile-dashboard.png" width="240"> |

**Site edit page: saved login (encrypted)**

<img src="docs/screenshots/site-credentials.png" alt="Saved login" width="720">

</details>

> Screenshots are generated automatically by [`docs/capture_screenshots.py`](docs/capture_screenshots.py), which runs Playwright against a running Docker instance.

## Quick start

Requires Docker with Docker Compose v2.

```bash
git clone https://github.com/WangShayne/daily-checkin.git
cd daily-checkin

# 1. Change the login credentials (strongly recommended)
cp .env.example .env
#   edit .env: CHECKIN_USER / CHECKIN_PASSWORD / CHECKIN_SESSION_SECRET

# 2. Build and start (the first build downloads Chromium and takes a while)
docker compose up -d --build
```

Open **http://localhost:4567**. From other devices on your LAN, use **http://<server-ip>:4567** (e.g. `http://192.168.1.10:4567`).

| Item | Value |
|---|---|
| Port | `4567` (web UI and the noVNC WebSocket share one port) |
| Default login | `admin` / `changeme`. **Change it before exposing the port.** The UI shows a warning while the default is in use. |
| Data volume | `checkin-data` → `/data` in the container (SQLite: sites, logs, recordings) |
| Demo site | `http://localhost:5001` (`demo` / `demo`, for self-testing; safe to remove) |

Restart after changing credentials:

```bash
CHECKIN_USER=me CHECKIN_PASSWORD='a-strong-password' \
CHECKIN_SESSION_SECRET="$(openssl rand -hex 32)" docker compose up -d
```

Common commands:

```bash
docker compose logs -f checkin        # follow logs
docker compose up -d --build          # rebuild after pulling updates (data is kept)
docker compose down                   # stop (the volume is kept)
```

**First steps**:

1. Log in.
2. Open **添加站点 (Add site)**.
3. Choose a type and a mode.
4. Set the daily time and save.
5. Click **签到 (Check in)** for a test run.
6. Check the result under **运行日志 (Run logs)**.

> If you don't need the demo site, remove the `test-site` service and the matching `depends_on` from `docker-compose.yml`.

## Check-in modes

| Mode | When to use | How it works | What you provide |
|---|---|---|---|
| **仅访问 (Visit)** `visit` | Opening the site/page each day counts as a check-in | Sends a GET to `base URL + path`. 2xx/3xx or a matching success keyword counts as success. | URL, usually a cookie |
| **点击 / 提交 (Click / submit)** `click` | The page has a check-in button, or there is a check-in API | Sends a POST (method configurable) with form data or JSON, depending on the site type | URL, cookie, optional form/JSON/success keywords |
| **录制回放 (Recorded)** `recorded` | Complex login (redirects, client-side crypto, cookies that expire often) | Record once in the embedded browser. Replay loads the saved cookies/localStorage, opens the recorded pages and re-submits the check-in form. If the login has expired, it [logs in again automatically](#automatic-re-login--credential-encryption). | Only a start URL; the rest happens on the [recording page](#recording-guide) |

How results are classified:

- **Already done today** (an idempotent success): the response contains 已签到 / 已经签到 / already / 重复签到.
- **Failed**: an exception occurred, or the status code is not in the success list.
- **Skipped**: the site is disabled.

The site type decides how a `click` request is submitted:

| Type | Use case |
|---|---|
| `forum` | Forum check-in plugins. The default form field is `operation=qiandao` (editable under advanced settings). |
| `portal` | JSON API check-in (`body`) or a form |
| `http_form` | Any form fields plus a cookie |
| `browser` | Browser recording; use it with the `recorded` mode |

## Recording guide

The container runs **Xvfb + x11vnc + Playwright Chromium**, embedded into the page via **noVNC**, so you control the remote browser right inside the UI.

1. Go to **浏览器录制 (Browser recording)** in the sidebar. Pick an existing site, or fill in a name and start URL (e.g. the login page) under 新建并录制 (Create & record).
2. The session page shows **远程桌面：已连接 ✅ (Remote desktop: connected)**.
3. **Log in manually** in the remote browser and **check in once** (click the button or open the check-in page).
4. Click **完成录制 (Finish recording)** at the top right. Cookies, localStorage (`storage_state`) and the navigation/POST steps are saved to `/data`.
5. The site switches to **recorded** mode. Scheduled runs and manual check-ins replay the flow headlessly from then on.

Tips:

- Only port 4567 needs to be open. x11vnc listens on `127.0.0.1:5900` inside the container only, and the app bridges `/ws/vnc` to it.
- The noVNC WebSocket URL is built from the browser's current address (same host, same port), so LAN-IP access works with no extra setup.
- Only one recording session runs at a time. Recording again overwrites the old recording.
- **Start the recording on the login page and do one full login + check-in.** That lets the tool detect the login step and re-login automatically after the cookies expire.

## Automatic re-login & credential encryption

**How it works**

1. **Login detection while recording**: a POST with a field such as `password` / `passwd` / `pwd` / `pass` / `secret`, or a submitted form containing a `type=password` input, is marked as the login step. The login page URL, submit URL and field names are recorded.
2. **Only the password is encrypted**: password-like fields are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) and stored in the SQLite `login_credentials` table. The username is kept in clear and masked in the UI. A plaintext password never appears in the database, logs, UI or the diagnostics endpoint.
3. **Every other request is redacted**: fields such as `token`, `access_token`, `csrf`, `_token`, `authenticity_token`, `formhash`, `session`, `cookie`, `authorization`, `api_key`, `otp` / `code`, phone, ID-card number and email are stored as `***` (form, JSON and multipart bodies). CSRF tokens change per session, so replay fetches them fresh from the page instead of reusing recorded values.
4. **Expiry detection during replay**: after restoring cookies and opening the recorded pages, the login counts as expired if any of these happen: a redirect to the recorded login page, a password input on the page, HTTP 401 / 403, or a failed per-site "login check".
5. **Automatic re-login** (at most once per run): open the recorded login page, fill in the username and the decrypted password in the real page, and submit. The fresh CSRF token is carried along automatically. If no form is found, it falls back to a direct POST with a freshly scraped CSRF token.
6. After a successful login, the **new cookies / localStorage are written back to the database** and the check-in runs. The log shows 「登录已过期，已自动重新登录」 (login expired, re-logged in automatically).
7. If re-login fails (captcha, SMS code, 2FA, changed password), the run is marked failed with a hint to re-record.

**Site settings** (edit a site → choose 录制回放 / Recorded):

- Shows 已保存登录凭据（已加密） (saved login, encrypted), the login page, and the time and result of the last automatic re-login. **The password is never shown.**
- 清除登录凭据 (Clear saved login) deletes the encrypted password.
- 登录状态检查 (Login check, optional): text that always appears on logged-in pages (e.g. `退出登录` / "Log out"), or a selector such as `css:.user-avatar`. Only needed when the automatic detection gets it wrong.

**Encryption key**

| Option | Notes |
|---|---|
| Env var `CHECKIN_SECRET_KEY` (recommended) | Generate one with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Any long passphrase also works (a key is derived from it). |
| Auto-generated `/data/secret.key` | If the env var is unset, the key is generated on first start (mode 0600) and a warning reminds you to back it up. |

- **Back up the key.** If the key is lost or changed, saved passwords can't be decrypted and you'll be asked to re-record. Recorded cookies and steps are unaffected.
- When backing up or moving the volume, **store `secret.key` separately from `checkin.db`**, or supply the key via the env var and keep it out of the volume.
- When upgrading from an older version, a **one-time migration** runs at startup. It replaces plaintext passwords and tokens in old recordings with `***`, encrypts the password of any login step it can identify, and VACUUMs the database so no plaintext lingers in the file.

**Limitations**

- Sites that require an **image captcha, SMS code, QR scan or 2FA** cannot be re-logged automatically. Re-record them when the cookies expire.
- If the login page changes significantly (renamed fields, multi-step login), automatic re-login may fail. Record again to fix it.
- SSO / third-party OAuth logins (redirecting to another domain) are best-effort only.

## Configuration & environment variables

| Variable | Default | Description |
|---|---|---|
| `CHECKIN_USER` | `admin` | Web UI username |
| `CHECKIN_PASSWORD` | `changeme` | Web UI password. **Change it.** |
| `CHECKIN_SESSION_SECRET` | compose: `please-change-this-session-secret` | Session cookie signing key; use a long random string |
| `CHECKIN_SECRET_KEY` | auto-generated `/data/secret.key` if unset | Key that encrypts saved login passwords (Fernet key or any long passphrase). **Back it up.** |
| `CHECKIN_DATA_DIR` | `/data` (Docker), `./data` locally | SQLite data directory |
| `TZ` | `Asia/Shanghai` | Container timezone (schedules always use Asia/Shanghai) |
| `CHECKIN_DISPLAY` | `:99` | Xvfb display used for recording |
| `CHECKIN_VNC_PORT` | `5900` | x11vnc port inside the container (127.0.0.1 only) |
| `CHECKIN_VNC_READY_TIMEOUT` | `15` | Seconds to wait for x11vnc to become ready |
| `CHECKIN_XVFB_PID_FILE` | `/tmp/checkin-xvfb.pid` | Xvfb PID file, used to clean up stale processes |
| `PLAYWRIGHT_BROWSERS_PATH` | `/ms-playwright` | Playwright browser location (bundled in the image) |
| `TEST_SITE_URL` | `http://host.docker.internal:5001` | Used by the E2E scripts only |
| `CHECKIN_CONFIG` | `config.yaml` | YAML config, CLI mode only |

The **设置 (Settings)** page controls the request timeout, the delay between sites, whether to continue after a failure, and webhook notifications (Slack / Discord / custom).

## Troubleshooting

**Recording page says 「无法连接到服务器」 (cannot connect) or stays on "connecting"**

The chain is: `Xvfb :99` → `x11vnc 127.0.0.1:5900` → app `/ws/vnc` (port 4567) → noVNC in your browser.

1. Update and rebuild: `git pull && docker compose up -d --build`
2. Expand **连接诊断 (Connection diagnostics)** below the session, or open `http://<server-ip>:4567/api/record/diag` (login required):
   - in `processes`, Xvfb and x11vnc should both show `running: true`
   - `vnc_rfb_handshake: true` means x11vnc is healthy
   - `page_ws_url` should be `ws://<the address you browse>:4567/ws/vnc`
3. Check the logs with `docker compose logs -f checkin | grep -E "x11vnc|VNC WebSocket|录制"`. A healthy session logs `x11vnc 已就绪` (ready) and `VNC WebSocket 已连接` (connected).
4. Common causes:
   - Stale noVNC settings cached in the browser: use a private window, or clear localStorage for the site.
   - The session ended or your login expired: the WebSocket closes with 4404 / 4401. Go back to the recording page and start again.
   - An HTTPS reverse proxy: it must forward WebSocket upgrades (the `Upgrade` / `Connection` headers). https pages switch to `wss://` automatically.

**Other issues**

| Symptom | Fix |
|---|---|
| Broken styling | Hard refresh (Ctrl+F5). The stylesheet URL is versioned, so upgrades invalidate the cache. |
| Check-in fails with "Name or service not known" | The container can't resolve the domain; check DNS/network |
| Always "already done today" | The site reports today's check-in as already done; this is a normal idempotent success |
| Recorded replay fails / login expired | If the recording includes the login step, re-login is automatic. Without a saved login, or on captcha sites, re-record. |
| 「无法解密已保存的登录凭据」 (cannot decrypt saved login) | The encryption key changed or was lost (`CHECKIN_SECRET_KEY` / `secret.key`). Restore the original key or re-record. |
| 「自动重新登录失败：…验证码…」 (re-login failed: captcha) | The site needs a captcha / SMS / 2FA, so automatic login isn't possible; re-record |
| Logged in but reported as expired | Set the 登录状态检查 (login check) on the site, e.g. `退出登录` or `css:.avatar` |
| Chromium crashes | Make sure `shm_size: 256mb` is still in the compose file |
| Scheduled run didn't happen | Make sure the site is enabled and check 下次 (next run) on the dashboard; the container must keep running |

## Project structure

```
daily-checkin/
├── Dockerfile               # python:3.12-slim + Chromium + Xvfb + x11vnc + noVNC
├── docker-compose.yml       # port 4567, data volume, login env vars, demo site
├── .env.example             # env template (copy to .env, never commit)
├── config.example.yaml      # CLI-mode example config
├── src/checkin/
│   ├── web/
│   │   ├── app.py           # FastAPI routes, login middleware, flash messages
│   │   ├── record_routes.py # recording pages, /ws/vnc WebSocket bridge, diagnostics
│   │   ├── templates/       # Jinja2 templates (base / dashboard / form / logs / settings / recording)
│   │   └── static/style.css # design system (light/dark, no external deps)
│   ├── adapters/            # forum / portal / http_form / recorded (replay + auto re-login)
│   ├── browser/recorder.py  # Xvfb + x11vnc + Playwright recording session
│   ├── browser/flow.py      # login detection, replay plan, expiry detection, CSRF scraping
│   ├── crypto.py            # Fernet encryption (CHECKIN_SECRET_KEY / secret.key)
│   ├── redact.py            # redaction for requests / URLs / logs
│   ├── db.py                # SQLite persistence
│   ├── scheduler.py         # APScheduler (Asia/Shanghai)
│   ├── core.py              # check-in orchestration
│   └── models.py            # models, mode/type labels
├── examples/
│   ├── test-site/           # Flask demo check-in site (demo / demo, login CSRF, /admin/expire)
│   ├── e2e_against_test_site.py
│   ├── e2e_record_ui.py     # end-to-end test of the noVNC recording UI
│   └── e2e_relogin.py       # record → expire session → auto re-login → check in
├── docs/
│   ├── screenshots/         # README screenshots
│   └── capture_screenshots.py
└── tests/                   # pytest unit tests
```

## Development & tests

Run locally without Docker (recording also needs Xvfb, x11vnc and noVNC):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e ".[dev]"
playwright install chromium
export CHECKIN_DATA_DIR=./data CHECKIN_USER=admin CHECKIN_PASSWORD=changeme
uvicorn checkin.web.app:app --host 0.0.0.0 --port 4567 --reload
```

Unit tests:

```bash
pytest -q
```

End-to-end tests (with the Docker stack running):

```bash
# visit / click / recorded against the demo site
docker exec -e TEST_SITE_URL=http://host.docker.internal:5001 \
  daily-checkin python /app/examples/e2e_against_test_site.py

# Recording UI: login → noVNC connects → log in & check in inside the remote browser → finish
# Set UI_URL to a LAN IP to verify non-localhost access
docker run --rm --network host --shm-size 256m -v "$PWD/examples:/ex" \
  -e UI_URL=http://127.0.0.1:4567 -e TARGET_URL=http://host.docker.internal:5001 \
  daily-checkin-checkin python /ex/e2e_record_ui.py
```

Automatic re-login test: record the alice account, expire the demo site's sessions with `POST /admin/expire`, and confirm replay logs in and checks in by itself. Then check that no plaintext password is in the volume or logs:

```bash
docker run --rm --network host --shm-size 256m -v "$PWD/examples:/ex" \
  -e UI_URL=http://127.0.0.1:4567 -e TARGET_URL=http://host.docker.internal:5001 \
  -e TEST_SITE_LOCAL=http://127.0.0.1:5001 daily-checkin-checkin python /ex/e2e_relogin.py
docker exec daily-checkin grep -rlF 'Wonderland-2026!' /data || echo "no plaintext"
```

Demo site extras: the login form carries a CSRF token, `POST /admin/expire` invalidates all sessions, `SESSION_LIFETIME=<seconds>` sets a max session age, and `/login?prefill=alice` pre-fills the second account.

To regenerate the screenshots, follow the header of [`docs/capture_screenshots.py`](docs/capture_screenshots.py). Use a demo container with a fresh volume.

Frontend notes: pages are server-rendered with FastAPI + Jinja2, with no npm or build step. Styles live in `static/style.css`; icons are inline SVGs in `_macros.html`.

CLI mode (YAML config, handy for debugging): `cp config.example.yaml config.yaml && checkin -c config.yaml -v`

## Security notes

- **Change the default password.** `admin / changeme` is only for a first try. Also set a random `CHECKIN_SESSION_SECRET`.
- **Don't expose it directly to the internet.** LAN use is recommended. For remote access, put it behind an HTTPS reverse proxy or a VPN.
- **Cookies are as good as passwords.** Site cookies and recorded `storage_state` live in the `/data` volume. Never commit or share the volume or `checkin.db`.
- **Login passwords are encrypted.** Only password-like fields are encrypted, using the key from `CHECKIN_SECRET_KEY` or `/data/secret.key` (0600). Anyone holding both the database and the key can decrypt, so keep them apart and back the key up.
- **Redaction.** Sensitive fields in other recorded requests, URL parameters, run logs and application logs are replaced with `***`.
- **Never commit** `.env`, `config.yaml`, database files or exported cookies (they are already in `.gitignore`).
- **No GitHub Actions.** The project is meant to be self-hosted; don't run check-ins in CI.
- noVNC and `/ws/vnc` require login too, and x11vnc listens on the container's loopback only.

## Disclaimer

For personal automation and learning only. Follow the target sites' terms of service and local laws, and do not use it for unauthorized access. Examples in this repo use placeholder URLs and the local demo site only.

## License

MIT
