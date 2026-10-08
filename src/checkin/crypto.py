"""Encryption of saved login secrets (Fernet / AES-128-CBC + HMAC-SHA256).

Key source, in order:
  1. env ``CHECKIN_SECRET_KEY`` — a Fernet key (``Fernet.generate_key()``) or
     any long passphrase (a key is derived from it with SHA-256);
  2. ``<data dir>/secret.key`` — generated on first start with mode 0600.

Losing the key only means saved login passwords can no longer be decrypted
(re-record the site); recorded cookies / steps are unaffected.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENV_KEY = "CHECKIN_SECRET_KEY"
KEY_FILENAME = "secret.key"

_lock = threading.Lock()
_cache: dict[str, Fernet] = {}


class SecretDecryptError(RuntimeError):
    """Saved secret cannot be decrypted (key changed or data corrupted)."""


def _data_dir() -> Path:
    return Path(os.environ.get("CHECKIN_DATA_DIR", "data"))


def _fernet_from_text(text: str) -> Fernet:
    raw = text.strip()
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        return Fernet(derived)


def key_source(data_dir: Path | str | None = None) -> str:
    """Human readable key origin (never the key itself)."""
    if os.environ.get(ENV_KEY, "").strip():
        return f"环境变量 {ENV_KEY}"
    return str(Path(data_dir or _data_dir()) / KEY_FILENAME)


def _load_or_create_key_file(path: Path) -> str:
    if path.exists():
        try:
            if path.stat().st_mode & 0o077:
                os.chmod(path, 0o600)
        except OSError:
            pass
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # created concurrently
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    logger.warning(
        "未设置 %s，已生成登录凭据加密密钥 %s（权限 0600）。请备份该文件，"
        "或把其内容设置为环境变量 %s；密钥丢失后已保存的登录密码将无法解密，需要重新录制。",
        ENV_KEY,
        path,
        ENV_KEY,
    )
    return key


def get_fernet(data_dir: Path | str | None = None) -> Fernet:
    env = os.environ.get(ENV_KEY, "").strip()
    cache_key = f"env:{hashlib.sha256(env.encode()).hexdigest()}" if env else f"file:{data_dir or _data_dir()}"
    with _lock:
        if cache_key in _cache:
            return _cache[cache_key]
        if env:
            f = _fernet_from_text(env)
        else:
            path = Path(data_dir or _data_dir()) / KEY_FILENAME
            f = _fernet_from_text(_load_or_create_key_file(path))
        _cache[cache_key] = f
        return f


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def encrypt_text(plain: str, data_dir: Path | str | None = None) -> str:
    return get_fernet(data_dir).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_text(token: str, data_dir: Path | str | None = None) -> str:
    try:
        return get_fernet(data_dir).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecryptError(
            "无法解密已保存的登录凭据（加密密钥已更换或丢失？），请重新录制该站点"
        ) from exc


def encrypt_json(data: Any, data_dir: Path | str | None = None) -> str:
    return encrypt_text(json.dumps(data, ensure_ascii=False), data_dir)


def decrypt_json(token: str, data_dir: Path | str | None = None) -> Any:
    return json.loads(decrypt_text(token, data_dir))
