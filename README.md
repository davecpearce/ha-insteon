# Insteon (extended)

A [HACS](https://hacs.xyz) custom integration that **replaces the Home Assistant core
`insteon` integration** with a lightly extended fork. It keeps `domain: insteon`, so it
shadows core and everything (entities, events, the Insteon panel, existing automations)
keeps working unchanged.

This is a derivative of the Home Assistant core integration written and maintained by
[@teharris1](https://github.com/teharris1) and [@ssyrell](https://github.com/ssyrell),
licensed under Apache 2.0 (see `LICENSE.md`). All credit for the integration itself goes
to them and the Home Assistant project; this repo only adds a small service on top.

## What it adds

### `insteon.set_device_property` action

Write a device configuration property (operating flag or extended property) to an Insteon
device from an automation or script, then persist it. Core only exposes this through the
Insteon panel UI.

| Field | Type | Notes |
|---|---|---|
| `device_id` | device id | An Insteon device from the device registry |
| `property` | string | e.g. `led_off`, `led_dimming`, `on_level`, `ramp_rate_in_seconds`, `toggle_button_a` |
| `value` | string | Parsed by the property's type, see below |

Example: dim the keypad LEDs on every device labelled `Insteon LED Control`:

```yaml
action: insteon.set_device_property
data:
  device_id: "{{ device_id }}"
  property: led_off
  value: "true"
```

Raises a translated validation error if the device, address, property or value is not
valid, and a `HomeAssistantError` if the device rejects the write.

### Property names and values

`property` is the name the Insteon panel shows for the device, and the set available
depends on the device type. In the Actions panel both fields are dropdowns you can also
type into: `property` lists every known property, `value` lists the fixed choices
(booleans and mode names) and takes numbers as free text. `value` is always text; it is
parsed by the property's type:

| Type | Accepted `value` |
|---|---|
| bool | `true` / `false`, also `on` / `off`, `yes` / `no`, `1` / `0` |
| int | a whole number, e.g. `on_level` is 0 to 255 |
| float | a decimal, e.g. `ramp_rate_in_seconds` `4.5` |
| ToggleMode | `toggle`, `on_only`, `off_only` |
| RelayMode | `latching`, `momentary_a`, `momentary_b`, `momentary_c` |
| list | not supported by the action; `radio_button_groups` is edited in the panel |

Properties exposed by pyinsteon 1.6.4, grouped by device family. Per-button properties
take a suffix: `toggle_button_a` through `toggle_button_h`.

**Dimmers, switches and keypads** (SwitchLinc, KeypadLinc, LampLinc, outlets)

| Property | Type | Property | Type |
|---|---|---|---|
| `led_off` | bool | `led_dimming` | int |
| `on_level` | int (dimmers) | `ramp_rate_in_seconds` | float (dimmers) |
| `toggle_button_a` … `_h` | ToggleMode (keypads) | `radio_button_groups` | list (keypads, panel only) |
| `program_lock_on` | bool | `key_beep_on` | bool |
| `resume_dim_on` | bool | `load_sense_on` | bool (dimmers) |
| `blink_on_tx_on` | bool | `blink_on_error_on` / `blink_on_error_off` | bool |
| `powerline_disable_on` | bool | `rf_disable_on` | bool |
| `x10_off` | bool (dimmers) | `cleanup_report_on` | bool (dimmers) |
| `reversed_on` | bool (switches) | `three_way_on` | bool (switches) |
| `dual_line_on` | bool (switches) | `momentary_line_on` | bool (switches) |
| `crc_error_count`, `signal_to_noise_failure_count`, `database_delta` | int, read-only (dimmers) | | |

**I/O Linc** (relay/sensor modules)

| Property | Type |
|---|---|
| `relay_mode` | RelayMode |
| `momentary_delay` | float |
| `sense_sends_off` | bool |
| `program_lock_on`, `blink_on_tx_on`, `x10_off` | bool |

**Sensors** (motion, open/close, leak, door)

| Property | Type |
|---|---|
| `led_on`, `led_brightness` | bool, int |
| `motion_timeout`, `hardware_timeout` | int |
| `light_sensitivity`, `hardware_light_sensitivity`, `ambient_light_intensity` | int |
| `night_mode_only`, `hardware_night_mode` | bool |
| `send_on_only_on`, `hardware_send_on_only` | bool |
| `two_groups_on`, `multi_send_on`, `repeat_open_on`, `repeat_closed_on` | bool |
| `link_to_ff_group`, `ignore_jumper_on`, `software_support_on`, `cleanup_report_on`, `hardware_led_off`, `program_lock_on` | bool |
| `battery_level` | int, read-only |

**Remotes and mini-remotes**: `led_on`, `key_beep_on`, `grouped_on`, `stay_awake_on`,
`send_on_only_on`, `program_lock_on` (all bool).

**Thermostats**: `backlight`, `change_delay` (int); `celsius`, `time_24_hour_format`,
`button_lock_on`, `key_beep_on`, `led_on`, `program_lock_on` (bool).

**Window coverings** (micro open/close): `on_level`, `duration_high`, `duration_low` (int);
`ramp_rate_in_seconds` (float); `forward_on`, `not_3_way`, `dual_line_on`,
`momentary_line_on`, `led_off`, `blink_on_tx_on`, `blink_on_error_off`, `key_beep_on`,
`program_lock_on` (bool).

**Modem** (PLM or Hub): `auto_led`, `deadman`, `disable_auto_linking`, `monitor_mode` (bool).

Read-only properties are shown in the panel but rejected by the device on write.

**Advanced properties.** The dropdown lists the standard set above. The raw per-button
values behind them are accepted by name as well, exactly as the panel's "show advanced"
view exposes them: `on_mask`, `off_mask`, `non_toggle_mask`, `non_toggle_on_off_mask`,
`trigger_group_mask` (button bitmasks, limited to the device's buttons), `on_level_2`
through `_8` and `ramp_rate_2` through `_8` (per-button, raw 0 to 31 for ramp rate),
`x10_house` / `x10_unit` and their per-button variants, I/O Linc `delay`, `prescaler`,
`momentary_on`, `momentary_follow_sense`, `momentary_on_off_trigger`, `relay_on_sense_on`,
and thermostat `temp_offset`, `humidity_offset`. Prefer the derived setting where one
exists (`ramp_rate_in_seconds` over `ramp_rate`).

### Device configuration entities

A few device settings that people automate are exposed as regular entities, under
**Configuration** on the device page. A device gets an entity only when it has the
property, so relay switches get the LED pair and dimmers get all four:

| Entity | Property | Notes |
|---|---|---|
| `number.<device>_led_brightness` | `led_dimming` | 0 to 127 |
| `switch.<device>_led` | `led_off`, inverted | on means the LED is lit |
| `number.<device>_on_level` | `on_level` | shown as 0 to 100 %, stored as 0 to 255 |
| `select.<device>_ramp_rate` | `ramp_rate_in_seconds` | one of the device's 31 fixed rates, `0.1 s` to `8 min` |

The ramp rate is a select rather than a number because the device only supports 31
specific durations, unevenly spaced (0.1, 0.2, 0.3, 0.5, 2, 4.5 ... 47 s, then 1 to
8 min); pyinsteon would snap any other number to the nearest one.

They are **disabled by default**, so nothing appears until you enable the ones you want.
Values come from what pyinsteon has read or written for the device, which is also what
the Insteon panel shows, and update when the panel, the action or the device read changes
them. A write goes through the same call the panel uses; if the device rejects it the
entity keeps its previous state and the call raises. `homeassistant.update_entity` on one
of them re-reads the device's configuration.

The LED automation above becomes:

```yaml
action: switch.turn_off
target:
  entity_id: switch.bedroom_entrance_keypad_led
```

## Other differences from core

Documented here so they are deliberate rather than accidental. Measured against
core `2026.11.0`, which v1.2 is based on (`UPSTREAM_BASE` records the tag).

| Area | Change | Why |
|---|---|---|
| `services.py`, `schemas.py`, `const.py`, `services.yaml`, `strings.json`, `icons.json` | `set_device_property` action | Feature, proposed for core |
| `property_entity.py`, `number.py`, `select.py`, `switch.py`, `const.py`, `strings.json`, `icons.json` | LED brightness and on level numbers, ramp rate select, LED switch | Feature, proposed for core |
| `light.py` | Debug logging of brightness in `brightness` and `async_turn_on` | Harmless diagnostics |
| `api/__init__.py` | Registers the Insteon panel with a sidebar title and icon, so it appears in the sidebar as well as behind the integration's Configure gear | Feature |
| `manifest.json` | Adds `version` and `issue_tracker`, points `documentation` at this repo | Required for custom components |
| `brand/` | Insteon icon, the same asset core uses | Required by HACS |

Everything else is byte-identical to core 2026.11.0.

## Changes in v1.5.0

- **On level and ramp rate entities** on every dimmer: `number.<device>_on_level` as a
  percentage and `select.<device>_ramp_rate` offering the device's 31 fixed rates. Same
  base class, config category and disabled-by-default behaviour as the LED entities.
- Lint cleanup of `property_entity.py` against core's ruff rules, ahead of the core PR.

## Changes in v1.4.1

- **Ships `translations/en.json`.** Core generates it from `strings.json` at build time; a
  custom integration has to include it, and this one never did. Without it the action's
  names in the Actions panel, the translated error messages, and the names of the new LED
  entities all fell back to raw keys. `scripts/run-upstream-tests.sh` now regenerates the
  file with core's own script, so it cannot drift from `strings.json` again.

## Changes in v1.4.0

- **LED backlight entities**: a `number` for brightness and a `switch` for the LED on
  every device that has them, config category, disabled by default. The same thing
  core's feature-request discussion #4454 asked for, built as a companion to the action.

## Changes in v1.3.0

- **`update_property` is now `set_device_property`**, reshaped to Home Assistant core's
  conventions so it can be proposed upstream unchanged: the device is targeted through
  `device_id`, the fields are `property` and `value`, errors are translated messages,
  a rejected write raises `HomeAssistantError`, and the handler calls the panel's own
  `update_property` so the action and the panel cannot drift. **Automations that used
  `insteon.update_property` must be updated**; there is no alias.


- **Rebased on core 2026.11.0.** Picks up two core releases of Insteon work: the move from
  voluptuous to probatio throughout, `pyinsteon` 1.7.0 (i3 device link-database loads,
  modem write fixes, back-off for unresponsive devices, atomic device cache) and panel
  frontend 0.7.0, range and button-mask validation for integer properties in the panel,
  actions that refuse to run while the integration is not loaded, service signals that
  survive an entity rename, and the climate entity's new `native_*` properties.
- **Requires Home Assistant 2026.11 or newer.** The 2026.8 serializer shim is gone and the
  climate entity uses an API that only exists from 2026.11.
- **`update_property` matches the panel's new behaviour**: integers are range-checked
  (ramp rates 0 to 31, LED brightness 0 to 127, button masks limited to the device's
  buttons), advanced properties that are not part of `device.configuration` can be
  written by name, and the action refuses to run while the integration is not loaded.

## Changes in v1.1.3

- **The Insteon panel is back in the sidebar.** Core registers it only behind the
  integration's Configure gear; this fork also gives it a sidebar title and icon. No
  iframe involved, so no nested Home Assistant chrome. The gear link still works.

## Changes in v1.1.2

- **Dropdowns in the Actions panel** for `prop_name` (every known property) and
  `prop_value` (booleans and mode names), both still accepting typed values.
- **List-valued properties are rejected with a clear message** instead of being written as
  text. `radio_button_groups` is the only one; edit it in the Insteon panel.
- Property reference tables in this README, generated from pyinsteon 1.6.4.
- `scripts/upstream-diff.sh` and `UPSTREAM_BASE` for checking each Home Assistant release.

## Changes in v1.1.1

- **The Insteon panel's "show advanced" toggle works as in core again.** The old fork
  forced advanced properties on. Dropping that removes the last behavioural divergence
  from core and makes core's own test suite pass in full against this integration.

## Changes in v1.1.0

- **Loads on Home Assistant 2026.9.** Core removed `voluptuous_serialize` in 2026.9;
  the shim above picks whichever serialiser the running core ships.
- **Merged upstream 2026.9.** The panel's device view now receives category, model,
  firmware and button data, as the pinned frontend expects.
- **Fixed: the property editor dropped derived settings.** v1.0's panel handler and
  `update_property` action only looked in `operating_flags` and `properties`, so
  `radio_button_groups`, `momentary_delay`, `relay_mode`, per-button toggle modes and
  ramp rate in seconds logged "could not be found" and changed nothing. Both now use
  `device.configuration`, the union pyinsteon maintains.
- **`update_property` parses values by the property's type**: booleans accept
  true/false/on/off/yes/no/1/0, integers and floats are parsed, and mode properties
  accept their mode name. Bad input raises a validation error before anything is
  written, and a rejected write no longer saves the device store.
- **Dropped drift from the old fork**: the deprecated `via_device` argument (removal
  scheduled for HA 2027.8), the pre-2026 device-registry lookups, and dead YAML-import
  code in `__init__.py`.
- **Tests** for the action live in `tests/` and run under core's own harness.

## Compatibility

| Release | Home Assistant | Notes |
|---|---|---|
| v1.2.x | 2026.11.x | Based on core 2026.11.0. Verified against core's own Insteon test suite at 2026.11.0. |
| v1.1.x | 2026.8.x to 2026.10.x | Based on core 2026.9.1. Loads on 2026.11 too, with deprecation warnings in the log. |
| v1.0.x | 2026.8.x only | Behaviour-identical to the pre-HACS fork. |

Requirements are `pyinsteon>=1.7.0,<1.8` and `insteon-frontend-home-assistant>=0.7.0,<0.8`:
the versions core 2026.11 pins, with room for patch releases. Hassfest rejects exact pins
on packages core itself depends on. `probatio` is not declared as a requirement
on purpose: core pins it, and pinning it here would fight core's pin on future releases.

## Installation

The repository URL to add in HACS is:

```
https://github.com/davecpearce/ha-insteon
```

1. In Home Assistant open **HACS**, click the **⋮** menu in the top right, choose
   **Custom repositories**.
2. Paste the URL above, set **Type** to *Integration*, click **Add**.
3. Search HACS for **Insteon**, open it, click **Download**, pick the latest release.
4. If you previously ran this integration from a hand-copied `custom_components/insteon`,
   HACS overwrites that same directory. Nothing else to move.
5. Restart Home Assistant.

Your existing Insteon config entry, devices, entity IDs, and any renames are picked up
as-is: Home Assistant identifies the integration by its domain (`insteon`) and each entity
by its unique ID, and neither changes. HACS's display name in `hacs.json` is never read by
Home Assistant.

**Before the first install on a production system:** take a full Home Assistant backup,
and keep a copy of the current `custom_components/insteon` directory so you can drop it
back in if needed.

To go back to core, remove the integration in HACS and restart. The config entry survives.

## Development

Core's suite in `tests/components/insteon/` mocks `pyinsteon` and runs without a PLM,
so it is the strongest check available. This repo's own tests in `tests/` are copied
alongside it.

```bash
scripts/run-upstream-tests.sh 2026.11.0
```

Each run clones core at that tag, builds a venv (including core's in-repo
`pytest-homeassistant` package, which the harness needs from 2026.11), generates
translations, runs the suite on stock core for a baseline, then again with this
`custom_components/insteon` swapped in. Expected result: everything stock passes also passes here, plus this
repo's own tests. See the release notes for the exact numbers per tag.


## Keeping up with Home Assistant releases

This integration shadows core, so every core release can change the files it is based
on. `UPSTREAM_BASE` records the core tag the current code was taken from. On each new
Home Assistant release:

```bash
scripts/upstream-diff.sh 2026.12.0
```

That prints what changed in core's Insteon integration since the base tag, whether the
pinned requirements or Python version moved, and how this repo's copies of the touched
files differ from the new core. Then:

- **No upstream changes**: nothing to do. Optionally run `scripts/run-upstream-tests.sh
  <tag>` to prove it, then bump `UPSTREAM_BASE` and `hacs.json`.
- **Upstream changed files**: copy the new core files over `custom_components/insteon`,
  re-apply the fork touches listed under *Other differences from core* (they are small:
  `translations/en.json`, regenerated by the test script,
  the `compat.py` import in two `api/` files, the sidebar title and icon in
  `api/__init__.py`, `property_entity.py` + `number.py` + `select.py` and the lines in
  `switch.py` and `const.py` that wire the configuration entities in, the `set_device_property` action across
  `const.py`, `schemas.py`, `services.py`, `services.yaml`, `strings.json`, the
  `light.py` debug logging, and the `manifest.json` packaging keys), run
  `scripts/run-upstream-tests.sh <tag>` and expect zero failures, then bump
  `UPSTREAM_BASE`, the manifest version, tag, and release.
- **Requirements or Python moved**: same as above; the test script picks up the new
  interpreter from core's `pyproject.toml`.
