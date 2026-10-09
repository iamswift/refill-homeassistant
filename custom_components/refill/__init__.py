"""
The RE.FILL agent.

Outbound only. This integration opens no port, registers no webhook and
exposes no endpoint; it wakes on a timer, reads consumable levels out of the
local registries, and posts them. Everything about how it is set up follows
from that one decision — see config_flow.py for what it consequently never has
to ask for.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import RefillAuthError, RefillClient, RefillError
from .collector import collect
from .const import (
    CONF_AGENT_TOKEN,
    CONF_BASE_URL,
    DEFAULT_BASE_URL,
    DOMAIN,
    HEARTBEAT_INTERVAL_SECONDS,
    REPORT_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

RefillConfigEntry = ConfigEntry  # ConfigEntry[RefillAgent] once 3.12 is the floor


class RefillAgent:
    """Owns the two timers and the one client."""

    def __init__(self, hass: HomeAssistant, entry: RefillConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.client = RefillClient(
            async_get_clientsession(hass),
            entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            entry.data[CONF_AGENT_TOKEN],
        )

    async def async_report(self, _now=None) -> None:
        devices, readings = collect(self.hass)

        if not devices:
            # Nothing in this house has a consumable we can track. Still worth
            # calling in: the server's alternative reading of silence is that
            # the hub is unplugged, and this is 3c rather than 3b.
            self._follow_canonical_url(await self._call(self.client.heartbeat()))
            return

        result = await self._call(
            self.client.report(
                [d.as_payload() for d in devices],
                [r.as_payload() for r in readings],
            )
        )
        self._follow_canonical_url(result)
        if result is not None:
            _LOGGER.debug(
                "reported %d devices, %d readings: %s", len(devices), len(readings), result
            )

    async def async_heartbeat(self, _now=None) -> None:
        self._follow_canonical_url(await self._call(self.client.heartbeat()))

    def _follow_canonical_url(self, result: dict | None) -> None:
        """
        RE.FILL tells us where it lives on every response. If that has moved,
        follow it and write it down, so the change survives a restart and so a
        domain migration does not need a person to open Home Assistant.

        Only ever moves us to somewhere the server we are already talking to
        pointed at — this is not discovery, it is a forwarding address from a
        host that has already authenticated our token.
        """
        if not result:
            return
        canonical = result.get("canonicalBaseUrl")
        if not isinstance(canonical, str) or not canonical:
            return
        if canonical.rstrip("/") == self.client.base_url:
            return

        _LOGGER.info("RE.FILL moved to %s; following", canonical)
        self.client.redirect_to(canonical)
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_BASE_URL: canonical}
        )

    async def _call(self, coro):
        """
        One place that decides what an error means.

        A dead token is not a transient failure and must not be retried on a
        timer — it needs the user, so it raises into Home Assistant's reauth
        flow and the integration shows as needing attention. Everything else is
        the internet being the internet: log it, and let the next tick try
        again. The server is already watching for the silence.
        """
        try:
            return await coro
        except RefillAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except RefillError as err:
            _LOGGER.warning("RE.FILL sync failed, will retry: %s", err)
            return None


async def async_setup_entry(hass: HomeAssistant, entry: RefillConfigEntry) -> bool:
    agent = RefillAgent(hass, entry)
    entry.runtime_data = agent

    # Report once on startup so pairing produces a visible result immediately
    # rather than an hour of an empty dashboard.
    await agent.async_report()

    entry.async_on_unload(
        async_track_time_interval(
            hass, agent.async_report, timedelta(seconds=REPORT_INTERVAL_SECONDS)
        )
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, agent.async_heartbeat, timedelta(seconds=HEARTBEAT_INTERVAL_SECONDS)
        )
    )

    async def _handle_sync_now(call: ServiceCall) -> None:
        await agent.async_report()

    hass.services.async_register(DOMAIN, "sync_now", _handle_sync_now)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RefillConfigEntry) -> bool:
    hass.services.async_remove(DOMAIN, "sync_now")
    return True
