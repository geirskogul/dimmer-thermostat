"""The Dimmer Thermostat integration.

Proportional (PI) temperature control of a heat source on a dimmer -- a reptile
basking or infrared lamp on a dimmer module (Zigbee, Wi-Fi, Z-Wave, ...), a heat mat on a dimmable
outlet, or anything else whose output is meaningfully variable rather than
on/off.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS
from .controller import DimmerThermostatController

_LOGGER = logging.getLogger(__name__)

type DimmerThermostatConfigEntry = ConfigEntry[DimmerThermostatController]


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
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: DimmerThermostatConfigEntry
) -> bool:
    """Stop the control loop and unload the platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.async_stop()
    return unload_ok


async def async_reload_entry(
    hass: HomeAssistant, entry: DimmerThermostatConfigEntry
) -> None:
    """Reload the entry so changed options take effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)
