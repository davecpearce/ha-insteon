"""Support for Insteon device configuration values as number entities."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .property_entity import (
    InsteonLedBrightnessNumber,
    InsteonOnLevelNumber,
    async_setup_property_entities,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Insteon number entities from a config entry."""
    async_setup_property_entities(hass, async_add_entities, InsteonLedBrightnessNumber)
    async_setup_property_entities(hass, async_add_entities, InsteonOnLevelNumber)
