"""Tests for config loading and env resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from checkin.config import get_settings, load_config, parse_sites
from checkin.models import CheckInMode


@pytest.fixture()
def sample_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEST_COOKIE", "sid=abc123")
    path = tmp_path / "config.yaml"
    path.write_text(
        """
settings:
  timeout: 15
  delay_between_sites: 0
sites:
  - name: Demo
    type: forum
    mode: click
    enabled: true
    base_url: https://example.com
    checkin_path: /sign
    cookies: "${TEST_COOKIE}"
  - name: VisitDemo
    type: portal
    mode: visit
    enabled: true
    base_url: https://visit.example.com
    checkin_path: /
  - name: Off
    type: portal
    enabled: false
    base_url: https://off.example.com
""",
        encoding="utf-8",
    )
    return path


def test_load_and_resolve_env(sample_yaml: Path) -> None:
    cfg = load_config(sample_yaml)
    sites = parse_sites(cfg)
    assert len(sites) == 3
    assert sites[0].cookies == "sid=abc123"
    assert sites[0].name == "Demo"
    assert sites[0].mode == CheckInMode.CLICK.value
    assert sites[1].mode == CheckInMode.VISIT.value
    assert sites[2].enabled is False


def test_get_settings_defaults(sample_yaml: Path) -> None:
    cfg = load_config(sample_yaml)
    settings = get_settings(cfg)
    assert settings["timeout"] == 15
    assert settings["continue_on_error"] is True


def test_missing_config() -> None:
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_missing_env_becomes_empty(tmp_path: Path) -> None:
    os.environ.pop("UNSET_SECRET_XYZ", None)
    path = tmp_path / "c.yaml"
    path.write_text(
        'sites:\n  - name: X\n    type: forum\n    cookies: "${UNSET_SECRET_XYZ}"\n',
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert parse_sites(cfg)[0].cookies == ""
