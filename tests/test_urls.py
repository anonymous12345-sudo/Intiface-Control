"""Unit tests for Intiface WebSocket URL validation."""

from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.intiface_control.const import optional_intiface_url, validate_intiface_url
from custom_components.intiface_control.coordinator import _safe_connect_url


def test_accepts_ws_and_wss() -> None:
    assert validate_intiface_url("ws://127.0.0.1:12345") == "ws://127.0.0.1:12345"
    assert validate_intiface_url("  wss://intiface.local:12345/path  ") == "wss://intiface.local:12345/path"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://127.0.0.1:12345",
        "https://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ws://",
        "ftp://127.0.0.1:12345",
    ],
)
def test_rejects_non_websocket_urls(url: str) -> None:
    with pytest.raises(vol.Invalid):
        validate_intiface_url(url)


def test_optional_url_treats_blank_as_none() -> None:
    assert optional_intiface_url("") is None
    assert optional_intiface_url("   ") is None
    assert optional_intiface_url(None) is None
    assert optional_intiface_url("ws://192.168.1.2:12345") == "ws://192.168.1.2:12345"


def test_runtime_backstop_refuses_bad_schemes() -> None:
    assert _safe_connect_url("ws://127.0.0.1:12345") == "ws://127.0.0.1:12345"
    assert _safe_connect_url("http://127.0.0.1:12345") is None
    assert _safe_connect_url("file:///tmp") is None
    assert _safe_connect_url("") is None
    assert _safe_connect_url(None) is None
