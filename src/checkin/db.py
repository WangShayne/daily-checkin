"""SQLite persistence for sites, settings, and run logs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from checkin.models import CheckInResult, CheckInStatus, SiteConfig

SHANGHAI = ZoneInfo("Asia/Shanghai")

DEFAULT_DATA_DIR = Path(os.environ.get("CHECKIN_DATA_DIR", "data"))


def _now_iso() -> str:
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


class Database:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir or DEFAULT_DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "checkin.db"
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'click',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    base_url TEXT NOT NULL DEFAULT '',
                    checkin_path TEXT NOT NULL DEFAULT '',
                    method TEXT NOT NULL DEFAULT 'POST',
                    headers_json TEXT NOT NULL DEFAULT '{}',
                    cookies TEXT,
                    form_data_json TEXT,
                    body_json TEXT,
                    success_keywords_json TEXT NOT NULL DEFAULT '[]',
                    success_status_json TEXT NOT NULL DEFAULT '[200]',
                    schedule_type TEXT NOT NULL DEFAULT 'daily',
                    daily_time TEXT NOT NULL DEFAULT '09:00',
                    cron TEXT NOT NULL DEFAULT '0 9 * * *',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER,
                    site_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    http_status INTEGER,
                    mode TEXT NOT NULL DEFAULT '',
                    triggered_by TEXT NOT NULL DEFAULT 'scheduler',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_run_logs_created
                    ON run_logs(created_at DESC);

                CREATE TABLE IF NOT EXISTS recorded_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site_id INTEGER NOT NULL UNIQUE,
                    final_url TEXT NOT NULL DEFAULT '',
                    cookies_json TEXT NOT NULL DEFAULT '[]',
                    storage_state_json TEXT NOT NULL DEFAULT '{}',
                    steps_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (site_id) REFERENCES sites(id) ON DELETE CASCADE
                );
                """
            )
            # Default settings
            defaults = {
                "timeout": "30",
                "delay_between_sites": "2",
                "continue_on_error": "true",
                "notify_enabled": "false",
                "notify_webhook_url": "",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (k, v),
                )

    # --- settings ---

    def get_setting(self, key: str, default: str = "") -> str:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        raw = {r["key"]: r["value"] for r in rows}
        return {
            "timeout": int(raw.get("timeout", "30")),
            "delay_between_sites": float(raw.get("delay_between_sites", "2")),
            "continue_on_error": raw.get("continue_on_error", "true").lower()
            in ("1", "true", "yes"),
            "notify_enabled": raw.get("notify_enabled", "false").lower()
            in ("1", "true", "yes"),
            "notify_webhook_url": raw.get("notify_webhook_url", ""),
        }

    def update_settings(self, data: dict[str, Any]) -> None:
        mapping = {
            "timeout": str(data.get("timeout", 30)),
            "delay_between_sites": str(data.get("delay_between_sites", 2)),
            "continue_on_error": "true"
            if data.get("continue_on_error")
            else "false",
            "notify_enabled": "true" if data.get("notify_enabled") else "false",
            "notify_webhook_url": str(data.get("notify_webhook_url") or ""),
        }
        for k, v in mapping.items():
            self.set_setting(k, v)

    # --- sites ---

    def _row_to_site(self, row: sqlite3.Row) -> SiteConfig:
        headers = json.loads(row["headers_json"] or "{}")
        form_data = (
            json.loads(row["form_data_json"])
            if row["form_data_json"]
            else None
        )
        body = json.loads(row["body_json"]) if row["body_json"] else None
        keywords = json.loads(row["success_keywords_json"] or "[]")
        statuses = json.loads(row["success_status_json"] or "[200]")
        return SiteConfig(
            id=row["id"],
            name=row["name"],
            type=row["type"],
            mode=row["mode"],
            enabled=bool(row["enabled"]),
            base_url=row["base_url"],
            checkin_path=row["checkin_path"],
            method=row["method"],
            headers=headers,
            cookies=row["cookies"],
            form_data=form_data,
            body=body,
            success_keywords=keywords,
            success_status=statuses,
            schedule_type=row["schedule_type"],
            daily_time=row["daily_time"],
            cron=row["cron"],
        )

    def list_sites(self) -> list[SiteConfig]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sites ORDER BY id ASC"
            ).fetchall()
        return [self._row_to_site(r) for r in rows]

    def get_site(self, site_id: int) -> SiteConfig | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sites WHERE id = ?", (site_id,)
            ).fetchone()
        return self._row_to_site(row) if row else None

    def add_site(self, site: SiteConfig) -> int:
        now = _now_iso()
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sites (
                    name, type, mode, enabled, base_url, checkin_path, method,
                    headers_json, cookies, form_data_json, body_json,
                    success_keywords_json, success_status_json,
                    schedule_type, daily_time, cron, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    site.name,
                    site.type,
                    site.mode,
                    1 if site.enabled else 0,
                    site.base_url,
                    site.checkin_path,
                    site.method or "POST",
                    json.dumps(site.headers or {}, ensure_ascii=False),
                    site.cookies
                    if isinstance(site.cookies, str) or site.cookies is None
                    else json.dumps(site.cookies),
                    json.dumps(site.form_data, ensure_ascii=False)
                    if site.form_data is not None
                    else None,
                    json.dumps(site.body, ensure_ascii=False)
                    if site.body is not None
                    else None,
                    json.dumps(site.success_keywords or [], ensure_ascii=False),
                    json.dumps(site.success_status or [200]),
                    site.schedule_type or "daily",
                    site.daily_time or "09:00",
                    site.cron or "0 9 * * *",
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)

    def update_site(self, site_id: int, site: SiteConfig) -> None:
        now = _now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sites SET
                    name=?, type=?, mode=?, enabled=?, base_url=?, checkin_path=?,
                    method=?, headers_json=?, cookies=?, form_data_json=?,
                    body_json=?, success_keywords_json=?, success_status_json=?,
                    schedule_type=?, daily_time=?, cron=?, updated_at=?
                WHERE id=?
                """,
                (
                    site.name,
                    site.type,
                    site.mode,
                    1 if site.enabled else 0,
                    site.base_url,
                    site.checkin_path,
                    site.method or "POST",
                    json.dumps(site.headers or {}, ensure_ascii=False),
                    site.cookies
                    if isinstance(site.cookies, str) or site.cookies is None
                    else json.dumps(site.cookies),
                    json.dumps(site.form_data, ensure_ascii=False)
                    if site.form_data is not None
                    else None,
                    json.dumps(site.body, ensure_ascii=False)
                    if site.body is not None
                    else None,
                    json.dumps(site.success_keywords or [], ensure_ascii=False),
                    json.dumps(site.success_status or [200]),
                    site.schedule_type or "daily",
                    site.daily_time or "09:00",
                    site.cron or "0 9 * * *",
                    now,
                    site_id,
                ),
            )

    def delete_site(self, site_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))

    # --- run logs ---

    def add_run_log(
        self,
        result: CheckInResult,
        *,
        triggered_by: str = "scheduler",
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO run_logs (
                    site_id, site_name, status, message, http_status,
                    mode, triggered_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.site_id,
                    result.site_name,
                    result.status.value,
                    result.message,
                    result.http_status,
                    result.mode,
                    triggered_by,
                    _now_iso(),
                ),
            )
            return int(cur.lastrowid)

    def list_run_logs(
        self,
        limit: int = 100,
        site_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM run_logs"
        where: list[str] = []
        args: list[Any] = []
        if site_id is not None:
            where.append("site_id = ?")
            args.append(site_id)
        if status:
            where.append("status = ?")
            args.append(status)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def stats_today(self) -> dict[str, int]:
        """Counts of today's (Asia/Shanghai) run results by status."""
        today = datetime.now(SHANGHAI).strftime("%Y-%m-%d")
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM run_logs "
                "WHERE created_at LIKE ? GROUP BY status",
                (f"{today}%",),
            ).fetchall()
        counts = {r["status"]: int(r["n"]) for r in rows}
        return {
            "success": counts.get("success", 0),
            "already": counts.get("already", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0),
            "total": sum(counts.values()),
        }

    def latest_result_by_site(self) -> dict[int, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.* FROM run_logs r
                INNER JOIN (
                    SELECT site_id, MAX(id) AS max_id
                    FROM run_logs
                    WHERE site_id IS NOT NULL
                    GROUP BY site_id
                ) t ON r.id = t.max_id
                """
            ).fetchall()
        return {int(r["site_id"]): dict(r) for r in rows if r["site_id"] is not None}



    # --- recorded flows (cookies stay in /data volume) ---

    def save_recorded_flow(
        self,
        site_id: int,
        *,
        final_url: str,
        cookies: list | dict,
        storage_state: dict,
        steps: list,
    ) -> int:
        now = _now_iso()
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        storage_json = json.dumps(storage_state or {}, ensure_ascii=False)
        steps_json = json.dumps(steps or [], ensure_ascii=False)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT id FROM recorded_flows WHERE site_id = ?", (site_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE recorded_flows SET
                        final_url=?, cookies_json=?, storage_state_json=?,
                        steps_json=?, updated_at=?
                    WHERE site_id=?
                    """,
                    (final_url, cookies_json, storage_json, steps_json, now, site_id),
                )
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO recorded_flows (
                    site_id, final_url, cookies_json, storage_state_json,
                    steps_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (site_id, final_url, cookies_json, storage_json, steps_json, now, now),
            )
            return int(cur.lastrowid)

    def get_recorded_flow(self, site_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM recorded_flows WHERE site_id = ?", (site_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "site_id": row["site_id"],
            "final_url": row["final_url"],
            "cookies": json.loads(row["cookies_json"] or "[]"),
            "storage_state": json.loads(row["storage_state_json"] or "{}"),
            "steps": json.loads(row["steps_json"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def has_recorded_flow(self, site_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM recorded_flows WHERE site_id = ?", (site_id,)
            ).fetchone()
        return row is not None

    def delete_recorded_flow(self, site_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM recorded_flows WHERE site_id = ?", (site_id,))

    def list_flow_summaries(self) -> dict[int, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT site_id, final_url, updated_at, steps_json FROM recorded_flows"
            ).fetchall()
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            steps = json.loads(r["steps_json"] or "[]")
            out[int(r["site_id"])] = {
                "final_url": r["final_url"],
                "updated_at": r["updated_at"],
                "step_count": len(steps),
            }
        return out


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def init_db(data_dir: Path | str | None = None) -> Database:
    global _db
    _db = Database(data_dir)
    return _db
