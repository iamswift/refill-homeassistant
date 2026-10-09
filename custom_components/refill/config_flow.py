"""Pairing, as a Home Assistant config flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RefillAuthError, RefillClient, RefillError
from .const import (
    CONF_AGENT_TOKEN,
    CONF_BASE_URL,
    CONF_CONNECTION_ID,
    CONF_PAIRING_CODE,
    DEFAULT_BASE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PAIRING_CODE): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
    }
)


class RefillConfigFlow(ConfigFlow, domain=DOMAIN):
    """
    One text field.

    Note what this flow does *not* ask for: a Home Assistant long-lived access
    token. Those are unscoped admin credentials — there is no read-only variant
    — and handing one to a cloud service would contradict, in a single step,
    the promise RE.FILL makes on four screens. The direction of this
    integration is the answer to that: nothing reaches in, so there is nothing
    to hand over.
    """

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).strip()
            code = user_input[CONF_PAIRING_CODE].strip()
            client = RefillClient(async_get_clientsession(self.hass), base_url)

            try:
                result = await client.pair(code)
            except RefillAuthError:
                # Unknown, already used, or expired — the server does not say
                # which, and neither do we. The action is the same: go back to
                # RE.FILL and draw a fresh code.
                errors["base"] = "invalid_code"
            except RefillError as err:
                _LOGGER.warning("pairing failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                connection_id = result["connectionId"]
                await self.async_set_unique_id(connection_id)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="RE.FILL",
                    data={
                        # What the server says it is, not what was typed. A
                        # user pasting an old address gets corrected once, here,
                        # instead of every hour forever.
                        CONF_BASE_URL: result.get("canonicalBaseUrl") or base_url,
                        CONF_AGENT_TOKEN: result["agentToken"],
                        CONF_CONNECTION_ID: connection_id,
                    },
                )

        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """A revoked token brings the user back to the same one field."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            client = RefillClient(async_get_clientsession(self.hass), base_url)
            try:
                result = await client.pair(user_input[CONF_PAIRING_CODE].strip())
            except RefillAuthError:
                errors["base"] = "invalid_code"
            except RefillError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_AGENT_TOKEN: result["agentToken"],
                        CONF_CONNECTION_ID: result["connectionId"],
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PAIRING_CODE): str}),
            errors=errors,
        )
