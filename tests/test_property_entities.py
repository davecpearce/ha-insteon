"""Tests for the fork-only device configuration entities (number, select, switch)."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from pubsub import pub
from pyinsteon.config import LED_DIMMING, LED_OFF, ON_LEVEL, RAMP_RATE, RAMP_RATE_IN_SEC
from pyinsteon.constants import ResponseStatus
import pytest
from pytest_homeassistant.common import MockConfigEntry, load_json_object_fixture

from homeassistant.components import insteon
from homeassistant.components.insteon import (
    DOMAIN,
    entity as insteon_entity,
    property_entity,
    utils as insteon_utils,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    DOMAIN as NUMBER_DOMAIN,
    SERVICE_SET_VALUE,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from .const import MOCK_USER_INPUT_PLM
from .mock_devices import MockDevices

KPL = "33.33.33"

devices = MockDevices()


@contextmanager
def publish_to_exact_listeners_only():
    """Deliver a property's set_value only to its own subscribers.

    pyinsteon publishes a property change under the device's address, where its
    status handler also listens; in tests that leaves a pending ack task behind.
    Upstream's tests mute the publish entirely; these entities need their own
    subscriber called, so notify the exact topic's listeners and nothing else.
    """

    def _publish(topic, **kwargs):
        topic_obj = pub.getDefaultTopicMgr().getTopic(topic, okIfNone=True)
        if topic_obj is None:
            return
        listeners = topic_obj.getListeners()
        for listener in listeners:
            listener.getCallable()(**kwargs)

    with patch("pyinsteon.subscriber_base.publish_topic", _publish):
        yield


async def mock_connection(*args, **kwargs):
    """Return a successful connection."""
    return True


@pytest.fixture(autouse=True)
def platforms_only():
    """Only set up the platforms under test."""
    with patch(
        "homeassistant.components.insteon.INSTEON_PLATFORMS",
        (Platform.NUMBER, Platform.SELECT, Platform.SWITCH),
    ):
        yield


@pytest.fixture(autouse=True)
def enabled_by_default():
    """The entities are disabled by default; enable them for the tests."""
    with patch.object(
        property_entity.InsteonPropertyEntity,
        "_attr_entity_registry_enabled_default",
        True,
    ):
        yield


@pytest.fixture(autouse=True)
def patch_setup_and_devices():
    """Patch the Insteon setup process and devices for the whole test."""
    with (
        patch.object(insteon, "async_connect", new=mock_connection),
        patch.object(insteon, "async_close"),
        patch.object(insteon, "devices", devices),
        patch.object(insteon_utils, "devices", devices),
        patch.object(insteon_entity, "devices", devices),
        patch.object(property_entity, "devices", devices),
    ):
        yield


@pytest.fixture(name="kpl_properties_data", scope="module")
def kpl_properties_data_fixture():
    """Load the KeypadLinc properties fixture."""
    return load_json_object_fixture("insteon/kpl_properties.json")


async def _setup(hass: HomeAssistant, kpl_properties_data) -> tuple[str, str]:
    """Set up the integration and return the LED entity ids for the KeypadLinc."""
    await devices.async_load()
    devices.fill_properties(KPL, kpl_properties_data)
    device = devices[KPL]

    async def write_config():
        """Behave like pyinsteon: a successful write applies the new values."""
        with publish_to_exact_listeners_only():
            for prop in (
                device.properties[LED_DIMMING],
                device.operating_flags[LED_OFF],
                device.properties[ON_LEVEL],
                device.properties[RAMP_RATE],
            ):
                if prop.is_dirty:
                    prop.set_value(prop.new_value)
        return ResponseStatus.SUCCESS

    device.async_write_config = AsyncMock(side_effect=write_config)
    devices.async_save.reset_mock()

    config_entry = MockConfigEntry(domain=DOMAIN, data=MOCK_USER_INPUT_PLM)
    config_entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    number_id = registry.async_get_entity_id(
        NUMBER_DOMAIN, DOMAIN, f"{device.id}_{LED_DIMMING}"
    )
    switch_id = registry.async_get_entity_id(
        SWITCH_DOMAIN, DOMAIN, f"{device.id}_{LED_OFF}"
    )
    assert number_id and switch_id
    return number_id, switch_id


async def test_entities_created_with_fixture_values(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """Both entities exist and show the values pyinsteon has for the device."""
    number_id, switch_id = await _setup(hass, kpl_properties_data)

    number = hass.states.get(number_id)
    assert number.state == "10"
    assert number.attributes["min"] == 0
    assert number.attributes["max"] == 127
    assert number.attributes["insteon_property"] == LED_DIMMING
    # led_off is False in the fixture, so the LED is lit
    assert hass.states.get(switch_id).state == STATE_ON


async def test_set_brightness(hass: HomeAssistant, kpl_properties_data) -> None:
    """Setting the number writes the property and the state follows."""
    number_id, _ = await _setup(hass, kpl_properties_data)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: number_id, ATTR_VALUE: 40},
        blocking=True,
    )
    await hass.async_block_till_done()
    devices[KPL].async_write_config.assert_awaited_once()
    assert devices[KPL].properties[LED_DIMMING].value == 40
    assert hass.states.get(number_id).state == "40"
    devices.async_save.assert_awaited()


async def test_switch_is_inverted_led_off(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """Turning the switch off sets led_off, turning it on clears it."""
    _, switch_id = await _setup(hass, kpl_properties_data)
    flag = devices[KPL].operating_flags[LED_OFF]

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: switch_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert flag.value is True
    assert hass.states.get(switch_id).state == STATE_OFF

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: switch_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert flag.value is False
    assert hass.states.get(switch_id).state == STATE_ON


async def test_unchanged_value_does_not_write(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """Writing the current value is a no-op, like the panel."""
    number_id, _ = await _setup(hass, kpl_properties_data)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: number_id, ATTR_VALUE: 10},
        blocking=True,
    )
    devices[KPL].async_write_config.assert_not_awaited()


async def test_write_failure_keeps_state(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """A rejected write raises and leaves the entity and property unchanged."""
    number_id, _ = await _setup(hass, kpl_properties_data)
    devices[KPL].async_write_config = AsyncMock(return_value=ResponseStatus.FAILURE)

    with pytest.raises(HomeAssistantError, match="write_failed|did not accept"):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: number_id, ATTR_VALUE: 99},
            blocking=True,
        )
    assert devices[KPL].properties[LED_DIMMING].value == 10
    assert devices[KPL].properties[LED_DIMMING].is_dirty is False
    assert hass.states.get(number_id).state == "10"


async def test_external_change_updates_state(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """A value learned from the device (panel read, action) updates the entity."""
    number_id, _ = await _setup(hass, kpl_properties_data)

    with publish_to_exact_listeners_only():
        devices[KPL].properties[LED_DIMMING].set_value(77)
    await hass.async_block_till_done()
    assert hass.states.get(number_id).state == "77"


def _entity_id(hass: HomeAssistant, platform: str, prop: str) -> str:
    """Return the KeypadLinc's entity id for one property."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{devices[KPL].id}_{prop}"
    )
    assert entity_id
    return entity_id


async def test_on_level_is_a_percentage(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """The on level shows 0-255 as a percentage and writes it back as 0-255."""
    await _setup(hass, kpl_properties_data)
    number_id = _entity_id(hass, NUMBER_DOMAIN, ON_LEVEL)

    state = hass.states.get(number_id)
    assert state.state == "100"  # fixture on_level is 255
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["max"] == 100

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: number_id, ATTR_VALUE: 40},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert devices[KPL].properties[ON_LEVEL].value == 102
    assert hass.states.get(number_id).state == "40"


async def test_ramp_rate_select_options_and_write(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """The ramp rate is one of the device's fixed rates, labelled in seconds."""
    await _setup(hass, kpl_properties_data)
    select_id = _entity_id(hass, SELECT_DOMAIN, RAMP_RATE_IN_SEC)

    state = hass.states.get(select_id)
    assert state.state == "0.5 s"  # fixture ramp_rate code 28
    options = state.attributes["options"]
    assert len(options) == 31
    assert options[0] == "0.1 s"
    assert options[-1] == "8 min"
    assert "1.5 min" in options

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: select_id, ATTR_OPTION: "2 min"},
        blocking=True,
    )
    await hass.async_block_till_done()
    devices[KPL].async_write_config.assert_awaited_once()
    assert devices[KPL].properties[RAMP_RATE].value == 0x0A  # 120 s
    assert devices[KPL].configuration[RAMP_RATE_IN_SEC].value == 120
    assert hass.states.get(select_id).state == "2 min"


async def test_ramp_rate_same_option_does_not_write(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """Selecting the current ramp rate is a no-op."""
    await _setup(hass, kpl_properties_data)
    select_id = _entity_id(hass, SELECT_DOMAIN, RAMP_RATE_IN_SEC)

    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: select_id, ATTR_OPTION: "0.5 s"},
        blocking=True,
    )
    devices[KPL].async_write_config.assert_not_awaited()


async def test_ramp_rate_follows_raw_property(
    hass: HomeAssistant, kpl_properties_data
) -> None:
    """A ramp rate learned from the device updates the select."""
    await _setup(hass, kpl_properties_data)
    select_id = _entity_id(hass, SELECT_DOMAIN, RAMP_RATE_IN_SEC)

    with publish_to_exact_listeners_only():
        devices[KPL].properties[RAMP_RATE].set_value(0x1B)  # 2 s
    await hass.async_block_till_done()
    assert hass.states.get(select_id).state == "2 s"
