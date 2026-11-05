"""Tests for the fork-only insteon.set_device_property action."""

from unittest.mock import AsyncMock, patch

from pyinsteon.config import LED_DIMMING, RAMP_RATE_IN_SEC, TOGGLE_BUTTON
from pyinsteon.constants import ResponseStatus, ToggleMode
import pytest
from pytest_homeassistant.common import MockConfigEntry, load_json_object_fixture

from homeassistant.components import insteon
from homeassistant.components.insteon.const import (
    ATTR_PROPERTY,
    ATTR_VALUE,
    DOMAIN,
    SRV_SET_DEVICE_PROPERTY,
)
from homeassistant.components.insteon.services import async_setup_services
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .mock_devices import MockDevices

KPL = "33.33.33"


@pytest.fixture(name="kpl_properties_data", scope="module")
def kpl_properties_data_fixture():
    """Load the KeypadLinc properties fixture."""
    return load_json_object_fixture("insteon/kpl_properties.json")


async def _setup(hass: HomeAssistant, kpl_properties_data) -> tuple[MockDevices, str]:
    """Register the action and a loaded entry with a device-registry entry for the mock KeypadLinc."""
    devices = MockDevices()
    await devices.async_load()
    devices.fill_properties(KPL, kpl_properties_data)
    devices[KPL].async_write_config = AsyncMock(return_value=ResponseStatus.SUCCESS)

    config_entry = MockConfigEntry(domain=DOMAIN)
    config_entry.add_to_hass(hass)
    config_entry.mock_state(hass, ConfigEntryState.LOADED)
    ha_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, KPL)}
    )
    async_setup_services(hass)
    return devices, ha_device.id


async def _call(hass: HomeAssistant, device_id: str, name: str, value: str) -> None:
    await hass.services.async_call(
        DOMAIN,
        SRV_SET_DEVICE_PROPERTY,
        {ATTR_DEVICE_ID: device_id, ATTR_PROPERTY: name, ATTR_VALUE: value},
        blocking=True,
    )


async def test_service_registered(hass: HomeAssistant) -> None:
    """The action is registered alongside the core ones."""
    async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SRV_SET_DEVICE_PROPERTY)


async def test_not_loaded(hass: HomeAssistant) -> None:
    """Like the core actions, it refuses to run while the entry is not loaded."""
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError, match="not.loaded"):
        await _call(hass, "any", "led_off", "true")


async def test_bool_operating_flag(hass: HomeAssistant, kpl_properties_data) -> None:
    """A boolean operating flag such as led_off is parsed from text and written."""
    devices, device_id = await _setup(hass, kpl_properties_data)
    prop = devices[KPL].configuration["led_off"]
    assert prop.value is False

    with patch.object(insteon.services, "devices", devices):
        await _call(hass, device_id, "led_off", "true")

    assert prop.new_value is True
    devices[KPL].async_write_config.assert_awaited_once()
    devices.async_save.assert_awaited_once()


async def test_int_property_in_range(hass: HomeAssistant, kpl_properties_data) -> None:
    """Integers are parsed and range-checked the same way the panel does it."""
    devices, device_id = await _setup(hass, kpl_properties_data)

    with patch.object(insteon.services, "devices", devices):
        await _call(hass, device_id, LED_DIMMING, "100")

    assert devices[KPL].configuration[LED_DIMMING].new_value == 100


async def test_derived_property(hass: HomeAssistant, kpl_properties_data) -> None:
    """Derived settings that exist only in device.configuration can be set.

    ramp_rate_in_seconds is not in operating_flags or properties; the old fork
    silently dropped it.
    """
    devices, device_id = await _setup(hass, kpl_properties_data)

    with patch.object(insteon.services, "devices", devices):
        await _call(hass, device_id, RAMP_RATE_IN_SEC, "4.5")

    assert devices[KPL].properties["ramp_rate"].new_value == 0x1A


async def test_advanced_property(hass: HomeAssistant, kpl_properties_data) -> None:
    """Advanced properties that are not in device.configuration are found too."""
    devices, device_id = await _setup(hass, kpl_properties_data)
    device = devices[KPL]
    name = next(
        n for n in device.properties if n not in device.configuration
        and device.properties[n].value_type is int
    )

    with patch.object(insteon.services, "devices", devices):
        await _call(hass, device_id, name, "1")

    assert device.properties[name].new_value == 1


async def test_mode_property(hass: HomeAssistant, kpl_properties_data) -> None:
    """Mode properties accept the mode name, case-insensitively."""
    devices, device_id = await _setup(hass, kpl_properties_data)
    prop = devices[KPL].configuration[f"{TOGGLE_BUTTON}_c"]
    assert prop.value == ToggleMode.TOGGLE

    with patch.object(insteon.services, "devices", devices):
        await _call(hass, device_id, f"{TOGGLE_BUTTON}_c", "on_only")

    assert prop.new_value == ToggleMode.ON_ONLY


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("no_such_property", "1", "property_not_found|does not exist"),
        ("led_off", "maybe", "invalid_value|not a valid value"),
        (LED_DIMMING, "200", "invalid_value|not a valid value"),
        (LED_DIMMING, "bright", "invalid_value|not a valid value"),
        (RAMP_RATE_IN_SEC, "fast", "invalid_value|not a valid value"),
        (f"{TOGGLE_BUTTON}_c", "sideways", "invalid_value|not a valid value"),
        ("radio_button_groups", "1,2", "invalid_value|Insteon panel"),
    ],
)
async def test_bad_input_raises(
    hass: HomeAssistant, kpl_properties_data, name: str, value: str, match: str
) -> None:
    """Unknown properties and unparsable or out-of-range values raise and write nothing."""
    devices, device_id = await _setup(hass, kpl_properties_data)

    with (
        patch.object(insteon.services, "devices", devices),
        pytest.raises(ServiceValidationError, match=match),
    ):
        await _call(hass, device_id, name, value)

    devices[KPL].async_write_config.assert_not_awaited()
    devices.async_save.assert_not_awaited()


async def test_unknown_device_raises(hass: HomeAssistant, kpl_properties_data) -> None:
    """A device id that is not in the registry is rejected."""
    devices, _ = await _setup(hass, kpl_properties_data)

    with (
        patch.object(insteon.services, "devices", devices),
        pytest.raises(ServiceValidationError, match="device_not_found|was not found"),
    ):
        await _call(hass, "does-not-exist", "led_off", "true")


async def test_write_failure_raises(hass: HomeAssistant, kpl_properties_data) -> None:
    """A device that rejects the write surfaces an error and is not saved."""
    devices, device_id = await _setup(hass, kpl_properties_data)
    devices[KPL].async_write_config = AsyncMock(return_value=ResponseStatus.FAILURE)

    with (
        patch.object(insteon.services, "devices", devices),
        pytest.raises(HomeAssistantError, match="write_failed|did not accept"),
    ):
        await _call(hass, device_id, "led_off", "true")

    devices.async_save.assert_not_awaited()
