"""Data models for sites and check-in results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckInStatus(str, Enum):
    SUCCESS = "success"
    ALREADY = "already"  # already checked in today (idempotent OK)
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SiteConfig:
    """Configuration for a single check-in site."""

    name: str
    type: str
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
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SiteConfig:
        known = {
            "name",
            "type",
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
        }
        kwargs = {k: data[k] for k in known if k in data}
        extra = {k: v for k, v in data.items() if k not in known}
        if extra:
            kwargs["extra"] = extra
        return cls(**kwargs)


@dataclass
class CheckInResult:
    """Outcome of one site check-in attempt."""

    site_name: str
    status: CheckInStatus
    message: str = ""
    http_status: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in (CheckInStatus.SUCCESS, CheckInStatus.ALREADY, CheckInStatus.SKIPPED)
