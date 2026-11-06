"""Support for Insteon device configuration values as select entities."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .property_entity import InsteonRampRateSelect, async_setup_property_entities


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Insteon select entities from a config entry."""
    async_setup_property_entities(hass, async_add_entities, InsteonRampRateSelect)
