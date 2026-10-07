"""Stale display cleanup must not crash when pkill is absent."""

from __future__ import annotations

from pathlib import Path

from checkin.browser import recorder as rec


def test_kill_stale_display_without_pkill(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "xvfb.pid"
    pid_file.write_text("999999", encoding="utf-8")  # unlikely live PID
    monkeypatch.setattr(rec, "XVFB_PID_FILE", pid_file)

    # Pretend pkill/killall are missing
    real_which = rec.shutil.which

    def fake_which(cmd: str):
        if cmd in ("pkill", "killall"):
            return None
        return real_which(cmd)

    monkeypatch.setattr(rec.shutil, "which", fake_which)
    # Must not raise FileNotFoundError
    rec._kill_stale_display()
    assert not pid_file.exists()


def test_kill_stale_display_tolerates_pkill_missing_binary(monkeypatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "xvfb.pid"
    monkeypatch.setattr(rec, "XVFB_PID_FILE", pid_file)

    def boom_which(cmd: str):
        if cmd == "pkill":
            return "/usr/bin/pkill"
        if cmd == "killall":
            return None
        return None

    monkeypatch.setattr(rec.shutil, "which", boom_which)

    def boom_run(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "pkill")

    monkeypatch.setattr(rec.subprocess, "run", boom_run)
    rec._kill_stale_display()  # must not raise
