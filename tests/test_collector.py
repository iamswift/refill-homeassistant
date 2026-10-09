"""
What the collector refuses to send.

Runs without Home Assistant installed: the registries and State objects it
reads are small enough to stand in for, and standing in for them is what makes
this runnable in CI next to the TypeScript tests rather than only on a machine
with a hub on it.

The assertions that matter are the negative ones. RE.FILL tells people on four
screens that it reads consumable levels and nothing else, and the way that
claim stops being marketing is a test that puts a lock, a camera and a
thermostat in front of the collector and checks that none of them come out.

    python3 agent/homeassistant/tests/test_collector.py
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --- Enough Home Assistant to import the module under test ------------------

CONNECTION_NETWORK_MAC = "mac"


@dataclass
class FakeState:
    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)
    name: str = ""
    last_updated: datetime = field(default_factory=lambda: datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc))


@dataclass
class FakeEntity:
    entity_id: str
    device_id: str | None = None
    device_class: str | None = None
    original_device_class: str | None = None
    area_id: str | None = None
    disabled_by: object | None = None
    hidden_by: object | None = None


@dataclass
class FakeDevice:
    id: str
    name: str | None = None
    name_by_user: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area_id: str | None = None
    via_device_id: str | None = None
    identifiers: set = field(default_factory=set)
    connections: set = field(default_factory=set)


@dataclass
class FakeArea:
    name: str


class FakeStates:
    def __init__(self, states):
        self._states = {s.entity_id: s for s in states}

    def get(self, entity_id):
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self, entities, devices, states, areas=None):
        self.states = FakeStates(states)
        self._entities = entities
        self._devices = {d.id: d for d in devices}
        self._areas = areas or {}


def _install_fake_homeassistant(hass_holder):
    """Register just enough of the homeassistant namespace to satisfy imports."""
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = FakeHass
    core.State = FakeState

    helpers = types.ModuleType("homeassistant.helpers")

    dr = types.ModuleType("homeassistant.helpers.device_registry")
    dr.CONNECTION_NETWORK_MAC = CONNECTION_NETWORK_MAC
    dr.DeviceEntry = FakeDevice
    dr.async_get = lambda hass: types.SimpleNamespace(async_get=lambda did: hass._devices.get(did))

    er = types.ModuleType("homeassistant.helpers.entity_registry")
    er.async_get = lambda hass: types.SimpleNamespace(
        entities=types.SimpleNamespace(values=lambda: hass._entities)
    )

    ar = types.ModuleType("homeassistant.helpers.area_registry")
    ar.async_get = lambda hass: types.SimpleNamespace(async_get_area=lambda aid: hass._areas.get(aid))

    for name, module in [
        ("homeassistant", ha),
        ("homeassistant.core", core),
        ("homeassistant.helpers", helpers),
        ("homeassistant.helpers.device_registry", dr),
        ("homeassistant.helpers.entity_registry", er),
        ("homeassistant.helpers.area_registry", ar),
    ]:
        sys.modules[name] = module


_install_fake_homeassistant(None)

# Loaded by path rather than as `refill.collector`, so importing it does not
# drag in the integration's __init__, which needs the real Home Assistant.
# Nothing in collector.py imports from its own package — deliberately, since it
# is the file a reviewer should be able to read and run on its own.
_spec = importlib.util.spec_from_file_location(
    "refill_collector",
    Path(__file__).resolve().parents[1] / "custom_components" / "refill" / "collector.py",
)
collector = importlib.util.module_from_spec(_spec)
# Registered before exec because @dataclass resolves annotations through
# sys.modules, and a module missing from it fails with a confusing AttributeError.
sys.modules[_spec.name] = collector
_spec.loader.exec_module(collector)


class CollectorTest(unittest.TestCase):
    def collect(self, entities, devices, states, areas=None):
        return collector.collect(FakeHass(entities, devices, states, areas))

    def test_battery_level_is_collected(self):
        device = FakeDevice(
            id="dev1",
            name="Front Door Lock",
            manufacturer="Yale",
            model="YRD256",
            area_id="a1",
            identifiers={("zha", "00:12:4b:00:1c:a2:9e:01")},
        )
        entity = FakeEntity("sensor.front_door_lock_battery", device_id="dev1", original_device_class="battery")
        state = FakeState("sensor.front_door_lock_battery", "41", {"unit_of_measurement": "%"})

        devices, readings = self.collect([entity], [device], [state], {"a1": FakeArea("Entryway")})

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].name, "Front Door Lock")
        self.assertEqual(devices[0].room, "Entryway")
        self.assertEqual(devices[0].radio_identity, {"kind": "zigbee-ieee", "value": "00:12:4b:00:1c:a2:9e:01"})
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0].value, 41)
        self.assertEqual(readings[0].spec_hint, "battery")

    def test_model_is_offered_to_the_matcher(self):
        device = FakeDevice(id="d", name="Lock", manufacturer="Yale", model="YRD256")
        entity = FakeEntity("sensor.lock_battery", device_id="d", original_device_class="battery")
        state = FakeState("sensor.lock_battery", "50", {"unit_of_measurement": "%"})

        devices, _ = self.collect([entity], [device], [state])
        identifiers = devices[0].raw["identifiers"]

        self.assertIn({"platform": "home-assistant", "id_type": "model", "id_value": "YRD256"}, identifiers)

    def test_nothing_but_levels_leaves_the_house(self):
        """The whole promise, as one test."""
        device = FakeDevice(id="d", name="Front Door Lock", model="YRD256")
        entities = [
            FakeEntity("lock.front_door", device_id="d"),
            FakeEntity("sensor.front_door_temperature", device_id="d", original_device_class="temperature"),
            FakeEntity("binary_sensor.front_door_tamper", device_id="d", original_device_class="tamper"),
            FakeEntity("camera.front_door", device_id="d"),
            FakeEntity("device_tracker.dana_phone", device_id="d"),
            FakeEntity("sensor.front_door_power", device_id="d", original_device_class="power"),
        ]
        states = [
            FakeState("lock.front_door", "locked"),
            FakeState("sensor.front_door_temperature", "21.5", {"unit_of_measurement": "°C"}),
            FakeState("binary_sensor.front_door_tamper", "off"),
            FakeState("camera.front_door", "recording"),
            FakeState("device_tracker.dana_phone", "home"),
            FakeState("sensor.front_door_power", "4.2", {"unit_of_measurement": "W"}),
        ]

        devices, readings = self.collect(entities, [device], states)

        self.assertEqual(devices, [])
        self.assertEqual(readings, [])

    def test_a_percentage_that_is_not_a_consumable_is_ignored(self):
        """Humidity and volume are percentages too. The unit is not the signal."""
        device = FakeDevice(id="d", name="Purifier")
        entities = [
            FakeEntity("sensor.purifier_humidity", device_id="d", original_device_class="humidity"),
            FakeEntity("sensor.speaker_volume", device_id="d"),
        ]
        states = [
            FakeState("sensor.purifier_humidity", "55", {"unit_of_measurement": "%"}),
            FakeState("sensor.speaker_volume", "80", {"unit_of_measurement": "%"}),
        ]

        _, readings = self.collect(entities, [device], states)
        self.assertEqual(readings, [])

    def test_filter_life_is_collected_by_name(self):
        device = FakeDevice(id="d", name="Air Purifier")
        entity = FakeEntity("sensor.air_purifier_filter_life_remaining", device_id="d")
        state = FakeState("sensor.air_purifier_filter_life_remaining", "83", {"unit_of_measurement": "%"})

        _, readings = self.collect([entity], [device], [state])
        self.assertEqual(readings[0].spec_hint, "filter")
        self.assertEqual(readings[0].value, 83)

    def test_filter_attribute_on_a_vacuum(self):
        device = FakeDevice(id="d", name="Robot Vacuum")
        entity = FakeEntity("vacuum.robot", device_id="d")
        state = FakeState("vacuum.robot", "docked", {"filter_life_remaining": 52})

        _, readings = self.collect([entity], [device], [state])
        self.assertEqual(readings[0].spec_hint, "filter")
        self.assertEqual(readings[0].value, 52)

    def test_a_low_battery_boolean_invents_no_number(self):
        """
        Plenty of Zigbee devices only report a low-battery flag. Turning 'true'
        into 10% would put a number the device never reported into a ledger the
        forecast runs a regression over. An honest gap beats a plausible
        invention.
        """
        device = FakeDevice(id="d", name="Motion Sensor")
        entity = FakeEntity("binary_sensor.motion_battery_low", device_id="d", original_device_class="battery")
        state = FakeState("binary_sensor.motion_battery_low", "on")

        _, readings = self.collect([entity], [device], [state])
        self.assertEqual(readings, [])

    def test_unavailable_is_not_zero(self):
        device = FakeDevice(id="d", name="Lock")
        entity = FakeEntity("sensor.lock_battery", device_id="d", original_device_class="battery")
        state = FakeState("sensor.lock_battery", "unavailable", {"unit_of_measurement": "%"})

        _, readings = self.collect([entity], [device], [state])
        self.assertEqual(readings, [], "an unreachable device must not report 0% and trigger a refill")

    def test_disabled_and_hidden_entities_are_skipped(self):
        device = FakeDevice(id="d", name="Lock")
        entities = [
            FakeEntity("sensor.a_battery", device_id="d", original_device_class="battery", disabled_by="user"),
            FakeEntity("sensor.b_battery", device_id="d", original_device_class="battery", hidden_by="user"),
        ]
        states = [
            FakeState("sensor.a_battery", "40", {"unit_of_measurement": "%"}),
            FakeState("sensor.b_battery", "40", {"unit_of_measurement": "%"}),
        ]

        devices, readings = self.collect(entities, [device], states)
        self.assertEqual((devices, readings), ([], []))

    def test_one_device_not_seven_entities(self):
        device = FakeDevice(id="d", name="Lock")
        entities = [
            FakeEntity("sensor.lock_battery", device_id="d", original_device_class="battery"),
            FakeEntity("sensor.lock_filter_life", device_id="d"),
            FakeEntity("lock.lock", device_id="d"),
        ]
        states = [
            FakeState("sensor.lock_battery", "41", {"unit_of_measurement": "%"}),
            FakeState("sensor.lock_filter_life", "90", {"unit_of_measurement": "%"}),
            FakeState("lock.lock", "locked"),
        ]

        devices, readings = self.collect(entities, [device], states)
        self.assertEqual(len(devices), 1, "a lock with two consumables is one thing you buy for")
        self.assertEqual(len(readings), 2)

    def test_observed_at_is_when_the_device_reported(self):
        """
        Not when we looked. A device reporting weekly would otherwise hand the
        forecast a flat line of identical values at hourly intervals.
        """
        seen = datetime(2026, 8, 14, 6, 30, tzinfo=timezone.utc)
        device = FakeDevice(id="d", name="Lock")
        entity = FakeEntity("sensor.lock_battery", device_id="d", original_device_class="battery")
        state = FakeState("sensor.lock_battery", "41", {"unit_of_measurement": "%"}, last_updated=seen)

        _, readings = self.collect([entity], [device], [state])
        self.assertEqual(readings[0].observed_at, seen)
        self.assertEqual(readings[0].as_payload()["observedAt"], "2026-08-14T06:30:00+00:00")

    def test_a_shared_mac_does_not_become_identity(self):
        """
        Several integrations report the *hub's* MAC for every device behind it.
        Trusting that as radio identity would merge a whole house into one lock,
        so a MAC is only reached for when nothing better exists — and Zigbee
        IEEE, when present, wins.
        """
        device = FakeDevice(
            id="d",
            name="Lock",
            identifiers={("zha", "00:12:4b:aa")},
            connections={(CONNECTION_NETWORK_MAC, "ab:cd:ef:00:11:22")},
        )
        entity = FakeEntity("sensor.lock_battery", device_id="d", original_device_class="battery")
        state = FakeState("sensor.lock_battery", "41", {"unit_of_measurement": "%"})

        devices, _ = self.collect([entity], [device], [state])
        self.assertEqual(devices[0].radio_identity["kind"], "zigbee-ieee")

    def test_an_entity_with_no_device_still_reports(self):
        entity = FakeEntity("sensor.orphan_battery", original_device_class="battery")
        state = FakeState("sensor.orphan_battery", "22", {"unit_of_measurement": "%"}, name="Orphan Battery")

        devices, readings = self.collect([entity], [], [state])
        self.assertEqual(devices[0].external_id, "entity:sensor.orphan_battery")
        self.assertEqual(readings[0].value, 22)


if __name__ == "__main__":
    unittest.main(verbosity=2)
