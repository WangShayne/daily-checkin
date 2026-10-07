"""Tests for adapter registry, helpers, and visit/click modes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from checkin.adapters import ADAPTER_REGISTRY, get_adapter, register_adapter
from checkin.adapters.base import Adapter
from checkin.adapters.forum import ForumAdapter
from checkin.adapters.http_form import HttpFormAdapter
from checkin.adapters.portal import PortalAdapter
from checkin.models import CheckInMode, CheckInResult, CheckInStatus, SiteConfig


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
        def do_click(self, site: SiteConfig, *, timeout: int = 30) -> CheckInResult:
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


def test_forum_missing_cookies_on_click() -> None:
    adapter = ForumAdapter()
    site = SiteConfig(
        name="F",
        type="forum",
        mode="click",
        base_url="https://x.com",
        checkin_path="/s",
    )
    result = adapter.check_in(site)
    assert result.status == CheckInStatus.FAILED
    assert "cookies" in result.message.lower() or "缺少" in result.message
    assert result.mode == CheckInMode.CLICK.value


def test_visit_mode_uses_get() -> None:
    adapter = PortalAdapter()
    site = SiteConfig(
        name="VisitSite",
        type="portal",
        mode="visit",
        base_url="https://example.com",
        checkin_path="/daily",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "ok welcome"

    with patch.object(adapter, "session_for") as mock_session_for:
        session = MagicMock()
        session.get.return_value = mock_resp
        mock_session_for.return_value = session
        result = adapter.check_in(site)

    session.get.assert_called_once()
    assert result.status == CheckInStatus.SUCCESS
    assert result.mode == CheckInMode.VISIT.value
    assert "访问" in result.message or "200" in result.message


def test_click_mode_dispatches_to_do_click() -> None:
    adapter = HttpFormAdapter()
    site = SiteConfig(
        name="ClickSite",
        type="http_form",
        mode="click",
        base_url="https://example.com",
        checkin_path="/sign",
        method="POST",
        form_data={"do": "checkin"},
        success_keywords=["签到成功"],
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "签到成功"

    with patch.object(adapter, "request", return_value=mock_resp) as mock_req:
        result = adapter.check_in(site)

    mock_req.assert_called_once()
    assert result.status == CheckInStatus.SUCCESS
    assert result.mode == CheckInMode.CLICK.value


def test_mode_alias_in_from_dict() -> None:
    site = SiteConfig.from_dict(
        {"name": "A", "type": "portal", "mode": "visit-only", "base_url": "https://x"}
    )
    assert site.mode == CheckInMode.VISIT.value
    site2 = SiteConfig.from_dict(
        {"name": "B", "type": "forum", "mode": "button", "base_url": "https://y"}
    )
    assert site2.mode == CheckInMode.CLICK.value
