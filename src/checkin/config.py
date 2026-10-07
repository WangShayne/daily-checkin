"""Load and resolve YAML/JSON configuration."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from checkin.models import SiteConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders with environment values."""
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            return os.environ.get(key, "")

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    return value


def load_config(path: str | Path) -> dict[str, Any]:
    """Load config from YAML or JSON file and resolve env placeholders."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(text) or {}
    elif suffix == ".json":
        raw = json.loads(text)
    else:
        # Try YAML first, then JSON
        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            raw = json.loads(text)

    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping/object")

    return _resolve_env(raw)


def parse_sites(config: dict[str, Any]) -> list[SiteConfig]:
    """Extract SiteConfig list from loaded config."""
    sites_raw = config.get("sites") or []
    return [SiteConfig.from_dict(item) for item in sites_raw]


def get_settings(config: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "timeout": 30,
        "delay_between_sites": 2,
        "continue_on_error": True,
    }
    settings = dict(defaults)
    settings.update(config.get("settings") or {})
    return settings
