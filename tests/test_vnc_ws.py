"""/ws/vnc bridge: auth, session checks, subprotocol, raw RFB piping."""

from __future__ import annotations

import socket
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def _fake_rfb_server() -> tuple[int, list[bytes], threading.Event]:
    """TCP server that greets like x11vnc and records what it receives."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    received: list[bytes] = []
    done = threading.Event()

    def run() -> None:
        conn, _ = srv.accept()
        conn.sendall(b"RFB 003.008\n")
        conn.settimeout(5)
        try:
            data = conn.recv(64)
            received.append(data)
        finally:
            done.set()
            conn.close()
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return port, received, done


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHECKIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHECKIN_USER", "admin")
    monkeypatch.setenv("CHECKIN_PASSWORD", "pw")
    monkeypatch.setenv("CHECKIN_SESSION_SECRET", "x" * 32)
    from checkin.web import app as app_module

    with TestClient(app_module.create_app()) as c:
        yield c


def _login(c: TestClient) -> None:
    c.post("/login", data={"username": "admin", "password": "pw", "next": "/"})


def test_ws_requires_login(client: TestClient) -> None:
    with client.websocket_connect("/ws/vnc") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
    assert exc.value.code == 4401


def test_ws_without_session(client: TestClient) -> None:
    _login(client)
    with client.websocket_connect("/ws/vnc") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
    assert exc.value.code == 4404


def test_ws_pipes_raw_rfb(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from checkin.browser import recorder
    from checkin.web import record_routes

    port, received, done = _fake_rfb_server()
    monkeypatch.setattr(record_routes, "VNC_PORT", port)
    mgr = recorder.get_recording_manager()
    monkeypatch.setattr(mgr, "status", lambda: {"active": True, "site_id": 1})

    _login(client)
    with client.websocket_connect("/ws/vnc", subprotocols=["binary"]) as ws:
        assert ws.accepted_subprotocol == "binary"
        greeting = ws.receive_bytes()
        assert greeting.startswith(b"RFB 003.008")
        ws.send_bytes(b"RFB 003.008\n")
        assert done.wait(5)
    assert received and received[0].startswith(b"RFB 003.008")


def test_diag_endpoint(client: TestClient) -> None:
    _login(client)
    r = client.get("/api/record/diag")
    assert r.status_code == 200
    body = r.json()
    assert body["ws_path"] == "/ws/vnc"
    assert "vnc_rfb_handshake" in body
