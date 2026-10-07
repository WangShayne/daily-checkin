"""Web UI session auth tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHECKIN_USER", "admin")
    monkeypatch.setenv("CHECKIN_PASSWORD", "secret123")
    monkeypatch.setenv("CHECKIN_SESSION_SECRET", "test-secret-key")
    # Recreate app after env set
    from checkin.web import app as app_module

    application = app_module.create_app()
    with TestClient(application) as c:
        yield c


def test_unauthenticated_redirects_to_login(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_health_public(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_and_access(client: TestClient) -> None:
    bad = client.post(
        "/login",
        data={"username": "admin", "password": "wrong", "next": "/"},
    )
    assert bad.status_code == 200
    assert "用户名或密码错误" in bad.text

    r = client.post(
        "/login",
        data={"username": "admin", "password": "secret123", "next": "/"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    home = client.get("/")
    assert home.status_code == 200
    assert "站点列表" in home.text
    assert "退出" in home.text


def test_logout(client: TestClient) -> None:
    client.post(
        "/login",
        data={"username": "admin", "password": "secret123", "next": "/"},
    )
    out = client.get("/logout", follow_redirects=False)
    assert out.status_code == 303
    assert "/login" in out.headers["location"]
    again = client.get("/", follow_redirects=False)
    assert again.status_code == 303
