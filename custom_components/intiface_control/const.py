"""Constants for the Intiface Control integration."""

DOMAIN = "intiface_control"

CONF_URL = "url"
CONF_FALLBACK_URL = "fallback_url"

DEFAULT_URL = "ws://127.0.0.1:12345"

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
