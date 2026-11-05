"""Entities that expose Insteon device configuration properties.

The Insteon panel can read and write every operating flag and extended property
of a device. These entities surface a few of them, the ones people automate, as
regular Home Assistant entities: they are config-category, disabled by default,
and write through the same pyinsteon calls the panel uses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import functools
import logging
from typing import Any

from pyinsteon import devices
from pyinsteon.config import LED_DIMMING, LED_OFF
from pyinsteon.constants import DeviceAction, ResponseStatus

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api.properties import LED_BRIGHTNESS_MAX
from .const import DOMAIN, SIGNAL_REMOVE_ENTITY

_LOGGER = logging.getLogger(__name__)

# devices.async_save rewrites the whole device file; serialise concurrent writes.
_SAVE_LOCK = asyncio.Lock()


def find_property(device, name: str):
    """Return the property object for name, searching like the panel does."""
    for props in (device.configuration, device.operating_flags, device.properties):
        if name in props:
            return props[name]
    return None


class InsteonPropertyEntity(Entity):
    """Base entity bound to one configuration property of one device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    property_name: str

    def __init__(self, device) -> None:
        """Bind to the device's property."""
        self._insteon_device = device
        self._prop = find_property(device, self.property_name)
        self._attr_unique_id = f"{device.id}_{self.property_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device.address))},
            name=f"{device.description} {device.address}",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the address and property so the link to the panel is clear."""
        return {
            "insteon_address": str(self._insteon_device.address),
            "insteon_property": self.property_name,
        }

    async def async_added_to_hass(self) -> None:
        """Follow the property and the device-removal signal."""
        self._prop.subscribe(self._async_property_updated)
        self.async_on_remove(
            functools.partial(self._prop.unsubscribe, self._async_property_updated)
        )
        remove_signal = f"{self._insteon_device.address.id}_{SIGNAL_REMOVE_ENTITY}"
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                remove_signal,
                functools.partial(self.async_remove, force_remove=True),
            )
        )

    @callback
    def _async_property_updated(self, name, value) -> None:
        """pyinsteon reports a new value after a read or a confirmed write."""
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Re-read the device's configuration on an explicit update request."""
        await self._insteon_device.async_read_config(read_aldb=False)

    async def _async_write(self, value) -> None:
        """Write one value to the device, exactly as the panel does."""
        self._prop.new_value = value
        if not self._prop.is_dirty:
            return
        result = await self._insteon_device.async_write_config()
        if result not in (ResponseStatus.SUCCESS, ResponseStatus.RUN_ON_WAKE):
            self._prop.new_value = None
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={
                    "property": self.property_name,
                    "address": str(self._insteon_device.address),
                    "result": str(result),
                },
            )
        async with _SAVE_LOCK:
            await devices.async_save(workdir=self.hass.config.config_dir)


class InsteonLedBrightnessNumber(InsteonPropertyEntity, NumberEntity):
    """LED backlight brightness of a dimmer, switch or keypad."""

    property_name = LED_DIMMING
    _attr_translation_key = "led_brightness"
    _attr_native_min_value = 0
    _attr_native_max_value = LED_BRIGHTNESS_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int | None:
        """Return the brightness as pyinsteon last read or wrote it."""
        return self._prop.value

    async def async_set_native_value(self, value: float) -> None:
        """Write the brightness."""
        await self._async_write(int(value))


class InsteonLedSwitch(InsteonPropertyEntity, SwitchEntity):
    """Whether the LED backlight is lit.

    The device flag is led_off; the switch shows it the way round people expect,
    so "on" means the LED is lit.
    """

    property_name = LED_OFF
    _attr_translation_key = "led"

    @property
    def is_on(self) -> bool | None:
        """Return True when the LED is lit."""
        if self._prop.value is None:
            return None
        return not self._prop.value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Light the LED."""
        await self._async_write(False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Extinguish the LED."""
        await self._async_write(True)


@callback
def async_setup_property_entities(
    hass: HomeAssistant,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_cls: type[InsteonPropertyEntity],
) -> None:
    """Add entity_cls for every device that has its property, now and later."""

    def _entities_for(device) -> list[InsteonPropertyEntity]:
        if device is devices.modem or find_property(device, entity_cls.property_name) is None:
            return []
        return [entity_cls(device)]

    async_add_entities(
        [entity for address in devices for entity in _entities_for(devices[address])]
    )

    @callback
    def _async_new_device(address, action: DeviceAction) -> None:
        if action == DeviceAction.ADDED:
            async_add_entities(_entities_for(devices[address]))

    devices.subscribe(_async_new_device, force_strong_ref=True)
