"""Constants for the Intiface Control integration."""

DOMAIN = "intiface_control"

CONF_URL = "url"
CONF_FALLBACK_URL = "fallback_url"

DEFAULT_URL = "ws://127.0.0.1:12345"

# How often the coordinator refreshes the device list, capabilities and
# battery levels. Mirrors the standalone bridge's device-watch interval.
UPDATE_INTERVAL_SECONDS = 5

# After this many consecutive failed connection attempts, all devices are
# marked offline (coordinator.data cleared) rather than keeping the last
# known snapshot forever. Keeps a single transient blip from flashing
# everything offline, while still eventually reflecting a real outage.
MAX_CONSECUTIVE_FAILURES = 3

CLIENT_NAME = "home-assistant-intiface-control"
