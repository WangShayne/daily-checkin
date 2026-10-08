"""Fernet key management and encryption round-trip."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from checkin import crypto


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHECKIN_SECRET_KEY", raising=False)
    crypto.reset_cache()
    yield
    crypto.reset_cache()


def test_generates_key_file_0600_and_round_trip(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    token = crypto.encrypt_text("hunter2", tmp_path)
    key_file = tmp_path / "secret.key"
    assert key_file.exists()
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    assert "hunter2" not in token
    assert crypto.decrypt_text(token, tmp_path) == "hunter2"
    assert "secret.key" in caplog.text  # warning recommends backing up
    # key persists across restarts
    crypto.reset_cache()
    assert crypto.decrypt_text(token, tmp_path) == "hunter2"


def test_env_key_fernet_and_passphrase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECKIN_SECRET_KEY", Fernet.generate_key().decode())
    tok = crypto.encrypt_json({"passwords": {"password": "pw"}}, tmp_path)
    assert crypto.decrypt_json(tok, tmp_path) == {"passwords": {"password": "pw"}}
    assert not (tmp_path / "secret.key").exists()
    crypto.reset_cache()
    monkeypatch.setenv("CHECKIN_SECRET_KEY", "any long passphrase works too")
    tok2 = crypto.encrypt_text("x", tmp_path)
    assert crypto.decrypt_text(tok2, tmp_path) == "x"
    assert "环境变量" in crypto.key_source(tmp_path)


def test_wrong_key_raises_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tok = crypto.encrypt_text("pw", tmp_path)
    crypto.reset_cache()
    monkeypatch.setenv("CHECKIN_SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(crypto.SecretDecryptError, match="重新录制"):
        crypto.decrypt_text(tok, tmp_path)
