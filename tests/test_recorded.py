"""Tests for recorded mode and flow persistence."""

from __future__ import annotations

from pathlib import Path

from checkin.adapters import get_adapter_for_site
from checkin.adapters.recorded import RecordedAdapter
from checkin.db import Database
from checkin.models import CheckInMode, CheckInStatus, SiteConfig


def test_mode_recorded_alias() -> None:
    site = SiteConfig.from_dict(
        {"name": "R", "type": "browser", "mode": "record", "base_url": "https://x"}
    )
    assert site.mode == CheckInMode.RECORDED.value


def test_get_adapter_for_recorded_site() -> None:
    site = SiteConfig(
        name="R", type="portal", mode="recorded", base_url="https://x.com"
    )
    assert isinstance(get_adapter_for_site(site), RecordedAdapter)
    site2 = SiteConfig(
        name="B", type="browser", mode="click", base_url="https://x.com"
    )
    assert isinstance(get_adapter_for_site(site2), RecordedAdapter)


def test_save_and_load_flow(tmp_path: Path) -> None:
    db = Database(tmp_path)
    sid = db.add_site(
        SiteConfig(
            name="FlowSite",
            type="browser",
            mode="recorded",
            base_url="https://example.com",
        )
    )
    db.save_recorded_flow(
        sid,
        final_url="https://example.com/checkin",
        cookies=[{"name": "sid", "value": "abc", "domain": "example.com"}],
        storage_state={"cookies": [], "origins": []},
        steps=[
            {"kind": "navigate", "url": "https://example.com/login"},
            {"kind": "navigate", "url": "https://example.com/checkin"},
            {
                "kind": "request",
                "method": "POST",
                "url": "https://example.com/api/sign",
                "post_data": "a=1",
            },
        ],
    )
    flow = db.get_recorded_flow(sid)
    assert flow is not None
    assert flow["final_url"].endswith("/checkin")
    assert len(flow["steps"]) == 3
    assert db.has_recorded_flow(sid)
    summary = db.list_flow_summaries()
    assert summary[sid]["step_count"] == 3


def test_replay_without_flow_fails(tmp_path: Path, monkeypatch) -> None:
    db = Database(tmp_path)
    monkeypatch.setenv("CHECKIN_DATA_DIR", str(tmp_path))
    from checkin import db as dbmod

    dbmod._db = db
    site = SiteConfig(
        id=1,
        name="NoFlow",
        type="browser",
        mode="recorded",
        base_url="https://example.com",
    )
    # site id 1 may not exist in db — adapter only needs get_recorded_flow
    result = RecordedAdapter().check_in(site)
    assert result.status == CheckInStatus.FAILED
    assert "录制" in result.message or "流程" in result.message


def test_run_coro_blocking_inside_running_loop() -> None:
    import asyncio

    from checkin.adapters.recorded import _run_coro_blocking

    async def inner() -> int:
        return 42

    async def outer() -> int:
        # simulates an async web handler calling the sync adapter
        return _run_coro_blocking(inner())

    assert asyncio.run(outer()) == 42
    assert _run_coro_blocking(inner()) == 42
