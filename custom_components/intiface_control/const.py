"""Constants for the Intiface Control integration."""

from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol

DOMAIN = "intiface_control"

CONF_URL = "url"
CONF_FALLBACK_URL = "fallback_url"

DEFAULT_URL = "ws://127.0.0.1:12345"

ALLOWED_URL_SCHEMES = ("ws", "wss")

# How often the coordinator refreshes the device list, capabilities and
# battery levels. Mirrors the standalone bridge's device-watch interval.
UPDATE_INTERVAL_SECONDS = 5

# Battery level changes on a scale of hours, not seconds, so it's polled
# far less often than the general device-list refresh above — every
# device still gets its first reading immediately when it's newly seen
# (or reappears after being offline), never waiting out this interval
# for that first value. See IntifaceCoordinator._async_update_data().
BATTERY_POLL_INTERVAL_SECONDS = 60

# After this many consecutive failed connection attempts, all devices are
# marked offline (coordinator.data cleared) rather than keeping the last
# known snapshot forever. Keeps a single transient blip from flashing
# everything offline, while still eventually reflecting a real outage.
MAX_CONSECUTIVE_FAILURES = 3

CLIENT_NAME = "home-assistant-intiface-control"


def validate_intiface_url(url: str) -> str:
    """Accept only ws:// or wss:// URLs with a host.

    The coordinator connects to whatever is stored in the config entry,
    so refusing other schemes (http, file, javascript, …) at the form
    boundary stops a mistyped or malicious URL from being used as an
    outbound WebSocket target.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        raise vol.Invalid("URL is required")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise vol.Invalid("URL must start with ws:// or wss://")
    if not parsed.hostname:
        raise vol.Invalid("URL must include a host")
    return cleaned


def optional_intiface_url(url: str | None) -> str | None:
    """Like validate_intiface_url, but empty / missing means 'no fallback'."""
    if url is None:
        return None
    cleaned = str(url).strip()
    if not cleaned:
        return None
    return validate_intiface_url(cleaned)
