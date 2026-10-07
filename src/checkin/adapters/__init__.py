"""Adapter registry: type name -> adapter class."""

from __future__ import annotations

from checkin.adapters.base import Adapter
from checkin.adapters.forum import ForumAdapter
from checkin.adapters.http_form import HttpFormAdapter
from checkin.adapters.portal import PortalAdapter
from checkin.adapters.recorded import RecordedAdapter
from checkin.models import CheckInMode, SiteConfig

ADAPTER_REGISTRY: dict[str, type[Adapter]] = {
    "forum": ForumAdapter,
    "http_form": HttpFormAdapter,
    "portal": PortalAdapter,
    "browser": RecordedAdapter,
}


def get_adapter(type_name: str) -> Adapter:
    """Instantiate adapter by config type name."""
    key = type_name.strip().lower()
    cls = ADAPTER_REGISTRY.get(key)
    if cls is None:
        known = ", ".join(sorted(ADAPTER_REGISTRY))
        raise KeyError(f"Unknown adapter type '{type_name}'. Known: {known}")
    return cls()


def get_adapter_for_site(site: SiteConfig) -> Adapter:
    """Pick adapter by mode first (recorded), then by type."""
    if site.mode_enum == CheckInMode.RECORDED or site.type == "browser":
        return RecordedAdapter()
    return get_adapter(site.type)


def register_adapter(type_name: str, cls: type[Adapter]) -> None:
    """Register a custom adapter (for plugins / local extensions)."""
    ADAPTER_REGISTRY[type_name.strip().lower()] = cls


__all__ = [
    "ADAPTER_REGISTRY",
    "Adapter",
    "ForumAdapter",
    "HttpFormAdapter",
    "PortalAdapter",
    "RecordedAdapter",
    "get_adapter",
    "get_adapter_for_site",
    "register_adapter",
]
