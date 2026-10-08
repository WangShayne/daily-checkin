"""Encrypted login credentials in SQLite, legacy migration, UI display."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from checkin import crypto
from checkin.db import Database
from checkin.models import SiteConfig

PW = "Wonderland-2026!"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKIN_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CHECKIN_SECRET_KEY", raising=False)
    crypto.reset_cache()
    yield
    crypto.reset_cache()


def _file_has(db: Database, needle: str) -> bool:
    return any(needle.encode() in p.read_bytes() for p in db.data_dir.iterdir() if p.is_file())


def test_credential_encrypted_at_rest(tmp_path: Path) -> None:
    db = Database(tmp_path)
    sid = db.add_site(SiteConfig(name="s", type="browser", mode="recorded", base_url="http://t"))
    db.save_login_credential(sid, username="alice", passwords={"password": PW},
                             login_step={"url": "http://t/login", "post_data": f"username=alice&password={PW}",
                                         "password_fields": ["password"], "_raw": f"password={PW}"})
    cred = db.get_login_credential(sid)
    assert cred["username"] == "alice"
    assert PW not in json.dumps(cred)
    assert db.decrypt_login_passwords(sid) == {"password": PW}
    assert not _file_has(db, PW)
    db.mark_relogin(sid, True, "ok")
    assert db.get_login_credential(sid)["last_relogin_ok"] is True
    assert db.delete_login_credential(sid)
    assert db.get_login_credential(sid) is None


def test_save_recorded_flow_redacts_defensively(tmp_path: Path) -> None:
    db = Database(tmp_path)
    sid = db.add_site(SiteConfig(name="s", type="browser", mode="recorded", base_url="http://t"))
    db.save_recorded_flow(sid, final_url="", cookies=[], storage_state={},
                          steps=[{"kind": "request", "method": "POST", "url": "http://t/login",
                                  "post_data": f"username=a&password={PW}&_token=xyz"}])
    assert PW not in json.dumps(db.get_recorded_flow(sid))
    assert not _file_has(db, PW)


def test_migration_redacts_legacy_and_encrypts_login(tmp_path: Path) -> None:
    db = Database(tmp_path)
    sid = db.add_site(SiteConfig(name="s", type="browser", mode="recorded", base_url="http://t"))
    legacy = [
        {"kind": "navigate", "url": "http://t/login"},
        {"kind": "request", "method": "POST", "url": "http://t/login", "resource_type": "document",
         "post_data": f"csrf_token=old&username=alice&password={PW}"},
        {"kind": "navigate", "url": "http://t/dashboard"},
        {"kind": "request", "method": "POST", "url": "http://t/ping", "resource_type": "xhr", "post_data": None},
    ]
    with db.connect() as conn:  # bypass save_recorded_flow redaction = pre-v0.5 data
        conn.execute(
            "INSERT INTO recorded_flows (site_id, final_url, cookies_json, storage_state_json, steps_json, "
            "created_at, updated_at) VALUES (?, '', '[]', '{}', ?, 'x', 'x')",
            (sid, json.dumps(legacy)),
        )
    assert _file_has(db, PW)
    stats = db.migrate_recorded_secrets()
    assert stats == {"flows": 1, "changed": 1, "logins": 1}
    assert not _file_has(db, PW), "plaintext still in sqlite file after migration"
    steps = db.get_recorded_flow(sid)["steps"]
    assert steps[1]["kind"] == "login" and PW not in json.dumps(steps)
    assert db.decrypt_login_passwords(sid) == {"password": PW}
    assert db.get_login_credential(sid)["username"] == "alice"
    # one-time
    assert db.migrate_recorded_secrets() == {"flows": 0, "changed": 0, "logins": 0}


def test_ui_shows_credential_state_and_clear(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECKIN_USER", "admin")
    monkeypatch.setenv("CHECKIN_PASSWORD", "changeme")
    monkeypatch.setenv("CHECKIN_SESSION_SECRET", "k")
    from checkin.db import get_db
    from checkin.web import app as app_module

    with TestClient(app_module.create_app()) as c:
        c.post("/login", data={"username": "admin", "password": "changeme", "next": "/"})
        db = get_db()
        assert (Path(tmp_path) / "secret.key").exists()
        sid = db.add_site(SiteConfig(name="R", type="browser", mode="recorded", base_url="http://t"))
        db.save_recorded_flow(sid, final_url="", cookies=[], storage_state={}, steps=[])
        page = c.get(f"/sites/{sid}/edit").text
        assert "未保存" in page and 'name="login_check"' in page
        db.save_login_credential(sid, username="alice@example.com", passwords={"password": PW},
                                 login_step={"url": "http://t/login", "page_url": "http://t/login",
                                             "fields": ["username", "password"]})
        page = c.get(f"/sites/{sid}/edit").text
        assert "已保存登录凭据（已加密）" in page and PW not in page
        assert "alice@example.com" not in page  # username is masked
        assert "自动登录" in c.get("/").text
        r = c.post(f"/sites/{sid}/credentials/clear")
        assert r.status_code == 200 and "已清除" in r.text
        assert db.get_login_credential(sid) is None
        # login_check round-trips through the form
        form = {"name": "R", "type": "browser", "mode": "recorded", "enabled": "1", "base_url": "http://t",
                "login_check": "css:.avatar"}
        c.post(f"/sites/{sid}", data=form)
        assert db.get_site(sid).login_check == "css:.avatar"
