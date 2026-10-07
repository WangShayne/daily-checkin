"""Data models for sites and check-in results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckInStatus(str, Enum):
    SUCCESS = "success"
    ALREADY = "already"
    FAILED = "failed"
    SKIPPED = "skipped"


class CheckInMode(str, Enum):
    """How to perform the daily check-in."""

    VISIT = "visit"
    CLICK = "click"
    RECORDED = "recorded"  # replay saved browser flow


SITE_TYPES = ("forum", "portal", "http_form", "browser")
MODE_LABELS = {
    CheckInMode.VISIT.value: "仅访问（打开页面即算签到）",
    CheckInMode.CLICK.value: "点击/提交（需 POST / 表单 / API）",
    CheckInMode.RECORDED.value: "录制回放（浏览器登录流程）",
}
TYPE_LABELS = {
    "forum": "论坛",
    "portal": "门户 / API",
    "http_form": "通用 HTTP 表单",
    "browser": "浏览器录制",
}


@dataclass
class SiteConfig:
    """Configuration for a single check-in site."""

    name: str
    type: str
    mode: str = CheckInMode.CLICK.value
    enabled: bool = True
    base_url: str = ""
    checkin_path: str = ""
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    cookies: str | dict[str, str] | None = None
    form_data: dict[str, Any] | None = None
    body: dict[str, Any] | list[Any] | str | None = None
    success_keywords: list[str] = field(default_factory=list)
    success_status: list[int] = field(default_factory=lambda: [200])
    schedule_type: str = "daily"
    daily_time: str = "09:00"
    cron: str = "0 9 * * *"
    id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def mode_enum(self) -> CheckInMode:
        try:
            return CheckInMode(self.mode)
        except ValueError:
            return CheckInMode.CLICK

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SiteConfig:
        known = {
            "name",
            "type",
            "mode",
            "enabled",
            "base_url",
            "checkin_path",
            "method",
            "headers",
            "cookies",
            "form_data",
            "body",
            "success_keywords",
            "success_status",
            "schedule_type",
            "daily_time",
            "cron",
            "id",
        }
        kwargs = {k: data[k] for k in known if k in data}
        extra = {k: v for k, v in data.items() if k not in known}
        if extra:
            kwargs["extra"] = extra
        mode = str(kwargs.get("mode", "click")).strip().lower()
        if mode in ("visit", "visit-only", "visit_only", "open"):
            kwargs["mode"] = CheckInMode.VISIT.value
        elif mode in ("recorded", "record", "browser", "replay"):
            kwargs["mode"] = CheckInMode.RECORDED.value
        elif mode in ("click", "button", "submit", "post"):
            kwargs["mode"] = CheckInMode.CLICK.value
        else:
            kwargs["mode"] = mode
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "mode": self.mode,
            "enabled": self.enabled,
            "base_url": self.base_url,
            "checkin_path": self.checkin_path,
            "method": self.method,
            "headers": self.headers,
            "cookies": self.cookies,
            "form_data": self.form_data,
            "body": self.body,
            "success_keywords": self.success_keywords,
            "success_status": self.success_status,
            "schedule_type": self.schedule_type,
            "daily_time": self.daily_time,
            "cron": self.cron,
            **self.extra,
        }


@dataclass
class CheckInResult:
    """Outcome of one site check-in attempt."""

    site_name: str
    status: CheckInStatus
    message: str = ""
    http_status: int | None = None
    site_id: int | None = None
    mode: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (
            CheckInStatus.SUCCESS,
            CheckInStatus.ALREADY,
            CheckInStatus.SKIPPED,
        )


@dataclass
class RecordedFlow:
    """Saved browser session + steps for replay."""

    site_id: int
    final_url: str = ""
    cookies: list[dict[str, Any]] = field(default_factory=list)
    storage_state: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    id: int | None = None
    created_at: str = ""
    updated_at: str = ""
