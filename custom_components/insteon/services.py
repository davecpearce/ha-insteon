"""Utilities used by insteon component."""

import asyncio
import logging

import probatio
from pyinsteon import devices
from pyinsteon.address import Address
from pyinsteon.constants import RelayMode, ResponseStatus, ToggleMode
from pyinsteon.managers.link_manager import (
    async_enter_linking_mode,
    async_enter_unlinking_mode,
)
from pyinsteon.managers.scene_manager import (
    async_trigger_scene_off,
    async_trigger_scene_on,
)
from pyinsteon.managers.x10_manager import (
    async_x10_all_lights_off,
    async_x10_all_lights_on,
    async_x10_all_units_off,
)
from pyinsteon.x10_address import create as create_x10_address

from homeassistant.const import (
    CONF_ADDRESS,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    ENTITY_MATCH_ALL,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
    dispatcher_send,
)
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    CONF_CAT,
    CONF_DIM_STEPS,
    CONF_HOUSECODE,
    CONF_PROP_NAME,
    CONF_PROP_VALUE,
    CONF_SUBCAT,
    CONF_TARGET_DEVICE,
    CONF_UNITCODE,
    DOMAIN,
    SIGNAL_ADD_DEFAULT_LINKS,
    SIGNAL_ADD_DEVICE_OVERRIDE,
    SIGNAL_ADD_X10_DEVICE,
    SIGNAL_LOAD_ALDB,
    SIGNAL_PRINT_ALDB,
    SIGNAL_REMOVE_DEVICE_OVERRIDE,
    SIGNAL_REMOVE_ENTITY,
    SIGNAL_REMOVE_HA_DEVICE,
    SIGNAL_REMOVE_INSTEON_DEVICE,
    SIGNAL_REMOVE_X10_DEVICE,
    SIGNAL_SAVE_DEVICES,
    SRV_ADD_ALL_LINK,
    SRV_ADD_DEFAULT_LINKS,
    SRV_ALL_LINK_GROUP,
    SRV_ALL_LINK_MODE,
    SRV_CONTROLLER,
    SRV_DEL_ALL_LINK,
    SRV_HOUSECODE,
    SRV_LOAD_ALDB,
    SRV_LOAD_DB_RELOAD,
    SRV_PRINT_ALDB,
    SRV_PRINT_IM_ALDB,
    SRV_SCENE_OFF,
    SRV_SCENE_ON,
    SRV_UPDATE_PROPERTY,
    SRV_X10_ALL_LIGHTS_OFF,
    SRV_X10_ALL_LIGHTS_ON,
    SRV_X10_ALL_UNITS_OFF,
)
from .schemas import (
    ADD_ALL_LINK_SCHEMA,
    ADD_DEFAULT_LINKS_SCHEMA,
    DEL_ALL_LINK_SCHEMA,
    LOAD_ALDB_SCHEMA,
    PRINT_ALDB_SCHEMA,
    TRIGGER_SCENE_SCHEMA,
    UPDATE_PROPERTY_SCHEMA,
    X10_HOUSECODE_SCHEMA,
)
from .api.properties import _validate_int
from .utils import print_aldb_to_log

_LOGGER = logging.getLogger(__name__)


def _coerce_property_value(prop, name: str, raw: str, groups):
    """Convert the string a service call carries into the property's native type.

    Mirrors what the Insteon panel does through the websocket API, but the
    service schema only accepts strings so the parsing lives here. Integers go
    through the same range and button-mask validation as the panel.
    """
    value_type = prop.value_type
    if value_type is bool:
        lowered = raw.strip().lower()
        if lowered in ("true", "on", "yes", "1"):
            return True
        if lowered in ("false", "off", "no", "0"):
            return False
        raise ValueError(f"expected true/false, got {raw!r}")
    if value_type is int:
        return _validate_int(name, raw, groups)
    if value_type is float:
        return float(raw)
    if value_type in (ToggleMode, RelayMode):
        try:
            return getattr(value_type, raw.strip().upper())
        except AttributeError as err:
            raise ValueError(
                f"expected one of {[m.name.lower() for m in value_type]}, got {raw!r}"
            ) from err
    if value_type is list:
        # radio_button_groups: a list of button groups. Not expressible as one
        # text field; the Insteon panel edits it with a proper multi-select.
        raise ValueError("list-valued properties can only be set from the Insteon panel")
    return raw


@callback
def _async_check_loaded(hass: HomeAssistant) -> None:
    """Raise if the Insteon config entry is not loaded."""
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )


@callback
def async_setup_services(hass: HomeAssistant) -> None:  # noqa: C901
    """Register services used by insteon component."""

    save_lock = asyncio.Lock()

    async def async_srv_add_all_link(service: ServiceCall) -> None:
        """Add an INSTEON All-Link between two devices."""
        _async_check_loaded(hass)
        group = service.data[SRV_ALL_LINK_GROUP]
        mode = service.data[SRV_ALL_LINK_MODE]
        link_mode = mode.lower() == SRV_CONTROLLER
        await async_enter_linking_mode(link_mode, group)

    async def async_srv_del_all_link(service: ServiceCall) -> None:
        """Delete an INSTEON All-Link between two devices."""
        _async_check_loaded(hass)
        group = service.data.get(SRV_ALL_LINK_GROUP)
        await async_enter_unlinking_mode(group)

    async def async_srv_load_aldb(service: ServiceCall) -> None:
        """Load the device All-Link database."""
        _async_check_loaded(hass)
        entity_id = service.data[CONF_ENTITY_ID]
        reload = service.data[SRV_LOAD_DB_RELOAD]
        if entity_id.lower() == ENTITY_MATCH_ALL:
            await async_srv_load_aldb_all(reload)
        else:
            signal = f"{entity_id}_{SIGNAL_LOAD_ALDB}"
            async_dispatcher_send(hass, signal, reload)

    async def async_srv_load_aldb_all(reload):
        """Load the All-Link database for all devices."""
        # Cannot be done concurrently due to issues with the underlying protocol.
        for address in devices:
            device = devices[address]
            if device != devices.modem and device.cat != 0x03:
                await device.aldb.async_load(refresh=reload)
                await async_srv_save_devices()

    async def async_srv_save_devices():
        """Write the Insteon device configuration to file."""
        async with save_lock:
            _LOGGER.debug("Saving Insteon devices")
            await devices.async_save(hass.config.config_dir)

    def print_aldb(service: ServiceCall) -> None:
        """Print the All-Link Database for a device."""
        _async_check_loaded(hass)
        # For now this sends logs to the log file.
        # Future direction is to create an INSTEON control panel.
        entity_id = service.data[CONF_ENTITY_ID]
        signal = f"{entity_id}_{SIGNAL_PRINT_ALDB}"
        dispatcher_send(hass, signal)

    def print_im_aldb(service: ServiceCall) -> None:
        """Print the All-Link Database for a device."""
        _async_check_loaded(hass)
        # For now this sends logs to the log file.
        # Future direction is to create an INSTEON control panel.
        print_aldb_to_log(devices.modem.aldb)

    async def async_update_property(service: ServiceCall) -> None:
        """Write one configuration property to an Insteon device and persist it.

        Fork addition: the same operation the Insteon panel performs, exposed as
        an action so automations can change device settings (LED brightness,
        ramp rate, ...).
        """
        _async_check_loaded(hass)
        target_device = service.data[CONF_TARGET_DEVICE]
        prop_name = service.data[CONF_PROP_NAME]
        prop_value = service.data[CONF_PROP_VALUE]

        dev_registry = dr.async_get(hass)
        ha_device = dev_registry.async_get(target_device)
        if not ha_device:
            raise ServiceValidationError(
                f"Home Assistant device not found for device ID {target_device}"
            )

        insteon_address = next(
            (ident[1] for ident in ha_device.identifiers if ident[0] == DOMAIN),
            None,
        )
        if not insteon_address:
            raise ServiceValidationError(
                f"Device {target_device} is not an Insteon device"
            )

        try:
            insteon_device = devices[Address(insteon_address)]
        except (KeyError, ValueError) as err:
            raise ServiceValidationError(
                f"Insteon device not found for address {insteon_address}: {err}"
            ) from err
        if insteon_device is None:
            raise ServiceValidationError(
                f"Insteon device not found for address {insteon_address}"
            )

        # Same lookup order as the panel: configuration holds the standard
        # properties and derived settings, the other two hold advanced ones.
        for props in (
            insteon_device.configuration,
            insteon_device.operating_flags,
            insteon_device.properties,
        ):
            if prop_name in props:
                prop = props[prop_name]
                break
        else:
            raise ServiceValidationError(
                f"Property {prop_name} not found on device {insteon_address}"
            )

        try:
            prop.new_value = _coerce_property_value(
                prop, prop_name, prop_value, insteon_device.groups
            )
        except (TypeError, ValueError, probatio.Invalid) as err:
            raise ServiceValidationError(
                f"Invalid value for property {prop_name}: {err}"
            ) from err

        result = await insteon_device.async_write_config()
        if result not in (ResponseStatus.SUCCESS, ResponseStatus.RUN_ON_WAKE):
            raise ServiceValidationError(
                f"Device {insteon_address} rejected the write of {prop_name}: {result}"
            )
        async with save_lock:
            await devices.async_save(workdir=hass.config.config_dir)

    async def async_srv_x10_all_units_off(service: ServiceCall) -> None:
        """Send the X10 All Units Off command."""
        _async_check_loaded(hass)
        housecode = service.data.get(SRV_HOUSECODE)
        await async_x10_all_units_off(housecode)

    async def async_srv_x10_all_lights_off(service: ServiceCall) -> None:
        """Send the X10 All Lights Off command."""
        _async_check_loaded(hass)
        housecode = service.data.get(SRV_HOUSECODE)
        await async_x10_all_lights_off(housecode)

    async def async_srv_x10_all_lights_on(service: ServiceCall) -> None:
        """Send the X10 All Lights On command."""
        _async_check_loaded(hass)
        housecode = service.data.get(SRV_HOUSECODE)
        await async_x10_all_lights_on(housecode)

    async def async_srv_scene_on(service: ServiceCall) -> None:
        """Trigger an INSTEON scene ON."""
        _async_check_loaded(hass)
        group = service.data.get(SRV_ALL_LINK_GROUP)
        await async_trigger_scene_on(group)

    async def async_srv_scene_off(service: ServiceCall) -> None:
        """Trigger an INSTEON scene ON."""
        _async_check_loaded(hass)
        group = service.data.get(SRV_ALL_LINK_GROUP)
        await async_trigger_scene_off(group)

    @callback
    def async_add_default_links(service: ServiceCall) -> None:
        """Add the default All-Link entries to a device."""
        _async_check_loaded(hass)
        entity_id = service.data[CONF_ENTITY_ID]
        signal = f"{entity_id}_{SIGNAL_ADD_DEFAULT_LINKS}"
        async_dispatcher_send(hass, signal)

    async def async_add_device_override(override):
        """Remove an Insten device and associated entities."""
        address = Address(override[CONF_ADDRESS])
        await async_remove_ha_device(address)
        devices.set_id(address, override[CONF_CAT], override[CONF_SUBCAT], 0)
        await async_srv_save_devices()

    async def async_remove_device_override(address):
        """Remove an Insten device and associated entities."""
        address = Address(address)
        await async_remove_ha_device(address)
        devices.set_id(address, None, None, None)
        await devices.async_identify_device(address)
        await async_srv_save_devices()

    @callback
    def async_add_x10_device(x10_config):
        """Add X10 device."""
        housecode = x10_config[CONF_HOUSECODE]
        unitcode = x10_config[CONF_UNITCODE]
        platform = x10_config[CONF_PLATFORM]
        steps = x10_config.get(CONF_DIM_STEPS, 22)
        x10_type = "on_off"
        if platform == "light":
            x10_type = "dimmable"
        elif platform == "binary_sensor":
            x10_type = "sensor"
        _LOGGER.debug(
            "Adding X10 device to Insteon: %s %d %s", housecode, unitcode, x10_type
        )
        # This must be run in the event loop
        devices.add_x10_device(housecode, unitcode, x10_type, steps)

    async def async_remove_x10_device(housecode, unitcode):
        """Remove an X10 device and associated entities."""
        address = create_x10_address(housecode, unitcode)
        devices.pop(address)
        await async_remove_ha_device(address)

    async def async_remove_ha_device(address: Address, remove_all_refs: bool = False):
        """Remove the device and all entities from hass."""
        signal = f"{address.id}_{SIGNAL_REMOVE_ENTITY}"
        async_dispatcher_send(hass, signal)
        dev_registry = dr.async_get(hass)
        config_entry = hass.config_entries.async_entries(DOMAIN)[0]
        device = dev_registry.async_get_device_by_identifier(
            (DOMAIN, str(address)), config_entry.entry_id
        )
        if device:
            dev_registry.async_remove_device(device.id)

    async def async_remove_insteon_device(
        address: Address, remove_all_refs: bool = False
    ):
        """Remove the underlying Insteon device from the network."""
        await devices.async_remove_device(
            address=address, force=False, remove_all_refs=remove_all_refs
        )
        await async_srv_save_devices()

    async_register_admin_service(
        hass,
        DOMAIN,
        SRV_ADD_ALL_LINK,
        async_srv_add_all_link,
        schema=ADD_ALL_LINK_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SRV_DEL_ALL_LINK,
        async_srv_del_all_link,
        schema=DEL_ALL_LINK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SRV_LOAD_ALDB, async_srv_load_aldb, schema=LOAD_ALDB_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SRV_PRINT_ALDB, print_aldb, schema=PRINT_ALDB_SCHEMA
    )
    hass.services.async_register(DOMAIN, SRV_PRINT_IM_ALDB, print_im_aldb, schema=None)
    hass.services.async_register(
        DOMAIN,
        SRV_X10_ALL_UNITS_OFF,
        async_srv_x10_all_units_off,
        schema=X10_HOUSECODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SRV_X10_ALL_LIGHTS_OFF,
        async_srv_x10_all_lights_off,
        schema=X10_HOUSECODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SRV_X10_ALL_LIGHTS_ON,
        async_srv_x10_all_lights_on,
        schema=X10_HOUSECODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SRV_SCENE_ON, async_srv_scene_on, schema=TRIGGER_SCENE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SRV_SCENE_OFF, async_srv_scene_off, schema=TRIGGER_SCENE_SCHEMA
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        SRV_ADD_DEFAULT_LINKS,
        async_add_default_links,
        schema=ADD_DEFAULT_LINKS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SRV_UPDATE_PROPERTY,
        async_update_property,
        schema=UPDATE_PROPERTY_SCHEMA,
    )
    async_dispatcher_connect(hass, SIGNAL_SAVE_DEVICES, async_srv_save_devices)
    async_dispatcher_connect(
        hass, SIGNAL_ADD_DEVICE_OVERRIDE, async_add_device_override
    )
    async_dispatcher_connect(
        hass, SIGNAL_REMOVE_DEVICE_OVERRIDE, async_remove_device_override
    )
    async_dispatcher_connect(hass, SIGNAL_ADD_X10_DEVICE, async_add_x10_device)
    async_dispatcher_connect(hass, SIGNAL_REMOVE_X10_DEVICE, async_remove_x10_device)
    async_dispatcher_connect(hass, SIGNAL_REMOVE_HA_DEVICE, async_remove_ha_device)
    async_dispatcher_connect(
        hass, SIGNAL_REMOVE_INSTEON_DEVICE, async_remove_insteon_device
    )
    _LOGGER.debug("Insteon Services registered")
