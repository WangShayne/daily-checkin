"""SQLite persistence smoke tests."""

from __future__ import annotations

from pathlib import Path

from checkin.db import Database
from checkin.models import CheckInResult, CheckInStatus, SiteConfig


def test_add_list_update_delete_site(tmp_path: Path) -> None:
    db = Database(tmp_path)
    site = SiteConfig(
        name="T",
        type="portal",
        mode="visit",
        base_url="https://example.com",
        checkin_path="/",
        daily_time="08:30",
    )
    sid = db.add_site(site)
    sites = db.list_sites()
    assert len(sites) == 1
    assert sites[0].id == sid
    assert sites[0].mode == "visit"
    assert sites[0].daily_time == "08:30"

    site.name = "T2"
    site.mode = "click"
    db.update_site(sid, site)
    updated = db.get_site(sid)
    assert updated is not None
    assert updated.name == "T2"
    assert updated.mode == "click"

    result = CheckInResult(
        site_name="T2",
        status=CheckInStatus.SUCCESS,
        message="ok",
        site_id=sid,
        mode="click",
    )
    db.add_run_log(result, triggered_by="manual")
    logs = db.list_run_logs()
    assert len(logs) == 1
    assert logs[0]["status"] == "success"

    db.delete_site(sid)
    assert db.get_site(sid) is None
