"""
What leaves the house.

This module is the privacy promise expressed as code. RE.FILL tells people, on
four separate screens, that it reads consumable levels and stops there — and
the only place that can be true is here, in the function that decides what to
put in the payload. A reviewer who wants to check the claim reads this file and
nothing else.

The rule it enforces: an entity contributes a number only if that number is a
consumable level. Not lock state, not presence, not temperature, not power
draw, not camera anything. Home Assistant knows all of it and none of it is
collected, because the collection is an allowlist rather than a redaction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er

_LOGGER = logging.getLogger(__name__)

# --- The allowlist ----------------------------------------------------------

# Device classes that are, by definition, a consumable level.
LEVEL_DEVICE_CLASSES: frozenset[str] = frozenset({"battery"})

# Home Assistant has no device class for filter life, so it has to be matched
# by name. These are substrings of the *entity id*, checked only on sensors
# that already report a percentage — which is what stops "water_filter_flow_rate"
# or a switch called "filter" from being read as a level.
FILTER_HINTS: tuple[str, ...] = (
    "filter_life",
    "filter_remaining",
    "filter_left",
    "filter_time_left",
    "filter_status",
    "filter_health",
    "filter_used",
    "cartridge",
)

# Attributes on non-sensor entities that carry a filter percentage. Vacuums and
# air purifiers overwhelmingly report this way rather than as their own sensor.
FILTER_ATTRIBUTES: tuple[str, ...] = (
    "filter_life_remaining",
    "filter_life",
    "filter_left",
)

# Domains worth looking at for the attributes above. Deliberately short: a
# `lock` also has attributes, and none of them are ours.
ATTRIBUTE_DOMAINS: frozenset[str] = frozenset({"fan", "vacuum", "humidifier", "air_quality"})

# Percentage units, in the spellings integrations actually use.
PERCENT_UNITS: frozenset[str] = frozenset({"%", "percent", "percentage"})


@dataclass(slots=True)
class CollectedDevice:
    external_id: str
    name: str
    manufacturer: str | None = None
    model: str | None = None
    type_hint: str | None = None
    room: str | None = None
    radio_identity: dict[str, str] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "externalId": self.external_id,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "typeHint": self.type_hint,
            "room": self.room,
            "radioIdentity": self.radio_identity,
            "raw": self.raw,
        }


@dataclass(slots=True)
class CollectedReading:
    external_id: str
    spec_hint: str
    value: float
    observed_at: datetime

    def as_payload(self) -> dict[str, Any]:
        return {
            "externalId": self.external_id,
            "specHint": self.spec_hint,
            "value": round(self.value, 3),
            "observedAt": self.observed_at.astimezone(timezone.utc).isoformat(),
        }


def _percent(state: State) -> float | None:
    """The state as a 0-100 percentage, or None if it is not one."""
    unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
    if unit and unit not in PERCENT_UNITS:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        # 'unavailable', 'unknown', or a string state. Not a number, not a
        # reading, and emphatically not a zero.
        return None
    if value < 0 or value > 100:
        return None
    return value


def _spec_hint(entity_id: str, device_class: str | None) -> str | None:
    """Which consumable this level is about, or None if it is not one."""
    if device_class in LEVEL_DEVICE_CLASSES:
        return "battery"
    lowered = entity_id.lower()
    if any(hint in lowered for hint in FILTER_HINTS):
        return "filter"
    return None


def _radio_identity(device: dr.DeviceEntry) -> dict[str, str] | None:
    """
    Hard radio identity, when the hub exposes it.

    This is the entire reason M1 does hubs before cloud platforms. A Zigbee IEEE
    address is the same string no matter which system is looking at the device,
    which makes it the only signal strong enough to merge two records without
    asking a human first. Cloud platforms hide it behind their own opaque ids;
    a local hub is standing right next to the radio.

    Ordered by how much identity each one actually carries. A MAC address is
    last because plenty of integrations report the *hub's* MAC for every device
    behind it, and a shared identity would merge a whole house into one lock.
    """
    for domain, value in device.identifiers:
        if domain == "zha" and value:
            return {"kind": "zigbee-ieee", "value": str(value)}
        if domain == "matter" and value:
            return {"kind": "matter-unique-id", "value": str(value)}

    for conn_type, value in device.connections:
        if not value:
            continue
        if conn_type in ("zigbee", "zha"):
            return {"kind": "zigbee-ieee", "value": str(value)}
        if conn_type == "zwave":
            return {"kind": "zwave-dsk", "value": str(value)}

    for conn_type, value in device.connections:
        if conn_type == dr.CONNECTION_NETWORK_MAC and value:
            return {"kind": "mac", "value": str(value)}

    return None


def _identifiers_for_matching(device: dr.DeviceEntry | None) -> list[dict[str, str]]:
    """
    The evidence resolve_product() scores against.

    Weighted server-side, not here: a model number is strong evidence and a
    manufacturer name on its own is nearly none, and which is which is a
    property of the catalog rather than of this hub.
    """
    if device is None:
        return []
    out: list[dict[str, str]] = []
    if device.model:
        out.append({"platform": "home-assistant", "id_type": "model", "id_value": device.model})
    if device.manufacturer and device.model:
        out.append(
            {
                "platform": "home-assistant",
                "id_type": "manufacturer_model",
                "id_value": f"{device.manufacturer} {device.model}",
            }
        )
    return out


def _area_name(hass: HomeAssistant, area_id: str | None) -> str | None:
    if not area_id:
        return None
    from homeassistant.helpers import area_registry as ar

    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area else None


def collect(hass: HomeAssistant) -> tuple[list[CollectedDevice], list[CollectedReading]]:
    """
    Walk the registries once and build the report.

    Keyed on the *device* rather than the entity: a lock with a battery sensor
    and six other entities is one thing you buy batteries for, not seven. An
    entity with no device behind it stands in for itself, because a level with
    nowhere to attach is still a level someone wants reordered.
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    devices: dict[str, CollectedDevice] = {}
    readings: list[CollectedReading] = []

    for entry in entity_registry.entities.values():
        if entry.disabled_by is not None or entry.hidden_by is not None:
            continue

        state = hass.states.get(entry.entity_id)
        if state is None:
            continue

        device_class = entry.device_class or entry.original_device_class
        domain = entry.entity_id.split(".", 1)[0]

        value: float | None = None
        spec_hint: str | None = None

        if domain == "sensor":
            hint = _spec_hint(entry.entity_id, device_class)
            if hint is not None:
                value = _percent(state)
                spec_hint = hint
        elif domain in ATTRIBUTE_DOMAINS:
            for attribute in FILTER_ATTRIBUTES:
                if attribute not in state.attributes:
                    continue
                try:
                    candidate = float(state.attributes[attribute])
                except (TypeError, ValueError):
                    continue
                if 0 <= candidate <= 100:
                    value, spec_hint = candidate, "filter"
                    break

        # Everything that reaches here without a value is dropped, and dropped
        # is the default. A binary "battery_low" sensor lands here on purpose:
        # true is not a level, and turning it into one — 10%? 5%? — would put a
        # number the device never reported into a ledger the forecast regresses
        # over. An honest gap beats a plausible invention.
        if value is None or spec_hint is None:
            continue

        device_entry = device_registry.async_get(entry.device_id) if entry.device_id else None
        external_id = f"device:{device_entry.id}" if device_entry else f"entity:{entry.entity_id}"

        if external_id not in devices:
            area_id = (device_entry.area_id if device_entry else None) or entry.area_id
            devices[external_id] = CollectedDevice(
                external_id=external_id,
                name=(device_entry.name_by_user or device_entry.name) if device_entry else state.name,
                manufacturer=device_entry.manufacturer if device_entry else None,
                model=device_entry.model if device_entry else None,
                type_hint=device_class,
                room=_area_name(hass, area_id),
                radio_identity=_radio_identity(device_entry) if device_entry else None,
                raw={
                    "identifiers": _identifiers_for_matching(device_entry),
                    "entities": [],
                    "via_device_id": device_entry.via_device_id if device_entry else None,
                },
            )

        devices[external_id].raw["entities"].append(
            {"entity_id": entry.entity_id, "spec_hint": spec_hint}
        )

        readings.append(
            CollectedReading(
                external_id=external_id,
                spec_hint=spec_hint,
                value=value,
                # The time Home Assistant last saw this number change, not the
                # time we happened to look. Reporting "now" would hand the
                # forecast a flat line for any device that reports weekly.
                observed_at=state.last_updated or datetime.now(timezone.utc),
            )
        )

    _LOGGER.debug("collected %d devices, %d readings", len(devices), len(readings))
    return list(devices.values()), readings
