"""HTTP client for the RE.FILL ingest endpoints."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import AGENT_VERSION

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=30)


class RefillError(Exception):
    """Something went wrong talking to RE.FILL."""


class RefillAuthError(RefillError):
    """
    The credential is dead.

    Split from RefillError because the two need opposite responses: a transport
    error means try again in an hour, and this means stop trying and tell the
    user. An agent that retries a revoked token forever is an agent that looks
    fine in the log and is doing nothing.
    """


class RefillClient:
    """Thin, outbound-only. Nothing here ever listens."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str | None = None) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token

    @property
    def base_url(self) -> str:
        return self._base_url

    def redirect_to(self, base_url: str) -> None:
        """Follow the address the server says it lives at. See const.DEFAULT_BASE_URL."""
        self._base_url = base_url.rstrip("/")

    async def pair(self, code: str) -> dict[str, Any]:
        return await self._post("/api/hub/pair", {"code": code, "agentVersion": AGENT_VERSION}, auth=False)

    async def report(self, devices: list[dict[str, Any]], readings: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._post(
            "/api/hub/report",
            {"devices": devices, "readings": readings, "agentVersion": AGENT_VERSION},
        )

    async def heartbeat(self) -> dict[str, Any]:
        return await self._post("/api/hub/heartbeat", {})

    async def _post(self, path: str, payload: dict[str, Any], auth: bool = True) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self._token:
                raise RefillAuthError("no agent token")
            headers["Authorization"] = f"Bearer {self._token}"

        url = f"{self._base_url}{path}"
        try:
            async with self._session.post(url, json=payload, headers=headers, timeout=TIMEOUT) as response:
                body: dict[str, Any] = {}
                if response.content_type == "application/json":
                    body = await response.json()

                if response.status in (401, 403):
                    raise RefillAuthError(_message(body, "This hub is no longer paired."))
                if response.status >= 400:
                    raise RefillError(_message(body, f"RE.FILL returned {response.status}"))

                # A 200 that is not JSON did not come from RE.FILL. Something in
                # front of it answered instead -- a login wall on the deployment,
                # a captive portal, a proxy error page -- and we followed the
                # redirect to it. Say so, because the alternative is returning an
                # empty dict and letting the caller die on a missing key, which
                # reads to the user as "unknown error" for a problem with a name.
                if response.content_type != "application/json":
                    raise RefillError(
                        f"{url} answered with {response.content_type}, not JSON. "
                        "Check the server address, and that the deployment is not "
                        "behind a login wall."
                    )
                return body
        except aiohttp.ClientError as err:
            # A house's uplink goes down; that is not a pairing problem, and
            # the server will notice the silence on its own soon enough.
            raise RefillError(f"could not reach RE.FILL: {err}") from err


def _message(body: dict[str, Any], fallback: str) -> str:
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return fallback
