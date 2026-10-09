# RE.FILL for Home Assistant

Sends the consumable levels Home Assistant already knows — battery percentages,
filter life — out to RE.FILL, so it can tell you what runs out next and reorder
before it does.

**It only sends.** This integration opens no port, registers no webhook and
accepts no inbound connection. Nothing outside your house can reach it.

## Install

**HACS** — add `https://github.com/iamswift/refill-homeassistant` as a custom
repository of type *Integration*, install **RE.FILL**, restart Home Assistant.
Or use the one-click link:
[Add to HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=iamswift&repository=refill-homeassistant&category=integration).

**Manually** — copy `custom_components/refill` into your `config/custom_components/`
directory and restart.

Then **Settings → Devices & services → Add integration → RE.FILL**
([Add integration](https://my.home-assistant.io/redirect/config_flow_start/?domain=refill)),
and paste the pairing code RE.FILL shows you. Leave the address field at its
default unless RE.FILL tells you otherwise.

## Why there is no access token

Home Assistant's long-lived access tokens are **unscoped admin credentials**.
There is no read-only variant: a token that can read a battery percentage can
also unlock your doors, watch your cameras and read every automation you have
ever written.

RE.FILL tells people, on four separate screens, that it reads consumable levels
and stops there. Holding a credential that could do all of the above would make
that claim untrue no matter how carefully the code behaved — the promise would
rest on our restraint rather than on anything you could check.

So the direction is inverted. This integration runs *inside* Home Assistant,
where it already has access, and pushes out the narrow thing RE.FILL needs. The
credential it holds is a RE.FILL token, scoped to one connection, and the only
thing that token can do is append readings. It cannot read your account.

## What gets sent

`custom_components/refill/collector.py` is the whole answer, and it is short
enough to read in full. It is an **allowlist**, not a redaction: an entity
contributes a number only if that number is a consumable level.

Sent, per device:

- Battery percentage (`device_class: battery`, reporting `%`)
- Filter life percentage — from a sensor whose name says so, or from a
  `filter_life_remaining` attribute on a fan, vacuum or humidifier
- Its name, room, manufacturer and model, so RE.FILL can work out which part it takes
- Its Zigbee/Z-Wave/Matter identity, which is what lets RE.FILL tell that the
  lock your hub sees and the lock some other app sees are one lock
- When Home Assistant last saw that level change

Not sent — and there is a test for each:

- Lock, cover, alarm or switch state
- Cameras, in any form
- Presence, device trackers, person entities
- Temperature, humidity, power, energy, air quality
- Any entity you have disabled or hidden
- Any percentage that is not a consumable level — speaker volume and humidity
  are both `%`, and neither is a thing you reorder

A device that reports only a *low battery* boolean sends nothing. Turning `on`
into "10%" would put a number your device never reported into a ledger that
RE.FILL runs a regression over, and a forecast built on an invented number is
worse than no forecast.

An unavailable sensor sends nothing either — never `0`, which would read as
"empty" and order you batteries you don't need.

## How often

| | |
| --- | --- |
| Full report | hourly |
| Heartbeat | every 15 minutes |

The heartbeat exists so RE.FILL can tell "nothing changed" apart from "this hub
has been unplugged since Tuesday". If it stops hearing from you for six hours,
your connection shows as degraded and the dashboard starts showing levels with
the date they were read, rather than pretending they are current. Plug the hub
back in and it recovers on its own — there is nothing to reconnect.

`refill.sync_now` reports immediately, which is handy right after you swap a set
of batteries.

## If it stops working

If the pairing is revoked from RE.FILL, the integration will show as needing
attention and ask for a new pairing code. Your device history is kept: the
readings were facts about your house, not about the connection.

## Tests

```bash
# from the root of this repository
python3 tests/test_collector.py
# from the monorepo (iamswift/refill_me)
python3 agent/homeassistant/tests/test_collector.py
```

No Home Assistant install needed — the registries are stubbed. The tests worth
reading are the ones that put a lock, a camera and a thermostat in front of the
collector and check that nothing comes out.

## Source and license

This repository is a mirror, never hand-edited. The source of truth is the
`agent/homeassistant/` folder of
[iamswift/refill_me](https://github.com/iamswift/refill_me); changes made here
are overwritten on the next release.

Released under the MIT license (see `LICENSE`). The license covers this
mirrored folder only, not the rest of the RE.FILL project.
