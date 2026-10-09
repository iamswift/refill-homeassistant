"""Constants for the RE.FILL agent."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "refill"

CONF_BASE_URL: Final = "base_url"
CONF_AGENT_TOKEN: Final = "agent_token"
CONF_CONNECTION_ID: Final = "connection_id"
CONF_PAIRING_CODE: Final = "pairing_code"

# Where a fresh install points before anyone tells it otherwise.
#
# This is the Vercel production URL rather than a real domain, and it is
# expected to change. Changing it here only helps *future* installs, which is
# the wrong half of the problem: an agent runs unattended on hardware nobody
# updates promptly, so a moved domain would strand every hub already out there.
#
# So the server tells the agent where it lives, on every call, and the agent
# follows — see CONF_BASE_URL handling in __init__.py. Moving domains becomes
# one environment variable on the server plus one report cycle, instead of a
# release note asking people to reconfigure an integration they forgot they
# installed.
DEFAULT_BASE_URL: Final = "https://refill-me-iamswifts-projects.vercel.app"

# Report levels hourly. The privacy promise on four screens is that RE.FILL
# reads consumable levels roughly once a day and nothing else; hourly is
# already ten times more often than that promise requires, and it exists only
# so a swap shows up the same afternoon rather than the next morning.
REPORT_INTERVAL_SECONDS: Final = 3600

# Heartbeat far more often than we report. The two are separate because "the
# hub is alive" and "here is a new number" are different facts, and the server
# flips a connection to degraded on the first, not the second.
HEARTBEAT_INTERVAL_SECONDS: Final = 900

AGENT_VERSION: Final = "0.1.0"
