"""Tests for adapter registry and helpers."""

from __future__ import annotations

import pytest

from checkin.adapters import ADAPTER_REGISTRY, get_adapter, register_adapter
from checkin.adapters.base import Adapter
from checkin.adapters.forum import ForumAdapter
from checkin.adapters.http_form import HttpFormAdapter
from checkin.adapters.portal import PortalAdapter
from checkin.models import CheckInResult, CheckInStatus, SiteConfig


def test_registry_contains_builtins() -> None:
    assert "forum" in ADAPTER_REGISTRY
    assert "portal" in ADAPTER_REGISTRY
    assert "http_form" in ADAPTER_REGISTRY
    assert isinstance(get_adapter("forum"), ForumAdapter)
    assert isinstance(get_adapter("PORTAL"), PortalAdapter)
    assert isinstance(get_adapter("http_form"), HttpFormAdapter)


def test_unknown_adapter() -> None:
    with pytest.raises(KeyError, match="Unknown adapter"):
        get_adapter("not_a_real_type")


def test_register_custom_adapter() -> None:
    class DummyAdapter(Adapter):
        def check_in(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
            return CheckInResult(site.name, CheckInStatus.SUCCESS, "ok")

    register_adapter("dummy_test", DummyAdapter)
    assert isinstance(get_adapter("dummy_test"), DummyAdapter)


def test_parse_cookies() -> None:
    adapter = ForumAdapter()
    assert adapter.parse_cookies("a=1; b=2") == {"a": "1", "b": "2"}
    assert adapter.parse_cookies({"x": "y"}) == {"x": "y"}
    assert adapter.parse_cookies(None) == {}


def test_build_url() -> None:
    adapter = PortalAdapter()
    site = SiteConfig(
        name="t",
        type="portal",
        base_url="https://example.com/app/",
        checkin_path="/api/checkin",
    )
    assert adapter.build_url(site) == "https://example.com/app/api/checkin"


def test_forum_missing_cookies() -> None:
    adapter = ForumAdapter()
    site = SiteConfig(name="F", type="forum", base_url="https://x.com", checkin_path="/s")
    result = adapter.check_in(site)
    assert result.status == CheckInStatus.FAILED
    assert "cookies" in result.message.lower() or "缺少" in result.message
