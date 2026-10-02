"""The Dimmer Thermostat integration.

Proportional (PI) temperature control of a heat source on a dimmer -- a reptile
basking or infrared lamp on a dimmer module (Zigbee, Wi-Fi, Z-Wave, ...), a heat mat on a dimmable
outlet, or anything else whose output is meaningfully variable rather than
on/off.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_registry import EventEntityRegistryUpdatedData
from homeassistant.helpers.event import async_track_entity_registry_updated_event

from .const import CONF_BACKUP_SENSOR, CONF_DIMMER, CONF_SENSOR, PLATFORMS
from .controller import DimmerThermostatController

_LOGGER = logging.getLogger(__name__)

type DimmerThermostatConfigEntry = ConfigEntry[DimmerThermostatController]

_ENTITY_KEYS = (CONF_SENSOR, CONF_BACKUP_SENSOR, CONF_DIMMER)


async def async_setup_entry(
    hass: HomeAssistant, entry: DimmerThermostatConfigEntry
) -> bool:
    """Set up one thermostat from a config entry.

    The controller is created here but started by the climate entity, which has
    to restore the previous mode, setpoint and integral term into it first.
    """
    controller = DimmerThermostatController(hass, entry)
    entry.runtime_data = controller

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_follow_renames(hass, entry)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: DimmerThermostatConfigEntry
) -> bool:
    """Stop the control loop and unload the platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


def _async_follow_renames(
    hass: HomeAssistant, entry: DimmerThermostatConfigEntry
) -> None:
    """Keep pointing at the sensors and dimmer if their entity ids are renamed.

    Without this, renaming the sensor would leave the thermostat in failsafe
    for good, and renaming the dimmer would leave it with nothing to drive.
    """
    tracked = [entry.data[key] for key in _ENTITY_KEYS if entry.data.get(key)]

    @callback
    def _async_registry_updated(event: Event[EventEntityRegistryUpdatedData]) -> None:
        data = event.data
        if data["action"] != "update" or "old_entity_id" not in data:
            return
        old_id, new_id = data["old_entity_id"], data["entity_id"]
        new_data = {
            key: new_id if value == old_id and key in _ENTITY_KEYS else value
            for key, value in entry.data.items()
        }
        if new_data == dict(entry.data):
            return
        _LOGGER.info("%s: following the rename of %s to %s", entry.title, old_id, new_id)
        unique_id = new_id if entry.unique_id == old_id else entry.unique_id
        hass.config_entries.async_update_entry(entry, data=new_data, unique_id=unique_id)
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        async_track_entity_registry_updated_event(
            hass, tracked, _async_registry_updated
        )
    )
