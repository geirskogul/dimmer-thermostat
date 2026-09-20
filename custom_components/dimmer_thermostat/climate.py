"""Climate platform: the thermostat entity itself."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DimmerThermostatConfigEntry
from .const import (
    ATTR_INTEGRAL,
    ATTR_OUTPUT,
    ATTR_STATUS,
    ATTR_STATUS_DETAIL,
    CONF_MAX_TEMP,
    CONF_MIN_TEMP,
    CONF_PRECISION,
    CONF_TEMP_UNIT,
    DEFAULTS,
)
from .entity import DimmerThermostatEntity

SERVICE_RESET_INTEGRAL = "reset_integral"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimmerThermostatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the thermostat entity and its entity services."""
    async_add_entities([DimmerThermostat(entry.runtime_data, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_RESET_INTEGRAL,
        {vol.Optional("preload"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100))},
        "async_reset_integral",
    )


class DimmerThermostat(DimmerThermostatEntity, ClimateEntity, RestoreEntity):
    """A heating thermostat whose actuator is a dimmer rather than a switch."""

    _attr_name = None
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, controller, entry: DimmerThermostatConfigEntry) -> None:
        """Read the display limits and unit fixed at configuration time."""
        super().__init__(controller, entry)
        self._attr_unique_id = entry.entry_id
        self._attr_temperature_unit = _temperature_unit(entry)
        self._attr_min_temp = _tunable(entry, CONF_MIN_TEMP)
        self._attr_max_temp = _tunable(entry, CONF_MAX_TEMP)
        self._attr_target_temperature_step = _tunable(entry, CONF_PRECISION)

    async def async_added_to_hass(self) -> None:
        """Restore the previous mode, setpoint and integral, then start the loop."""
        await super().async_added_to_hass()
        self._controller.async_restore(await self.async_get_last_state())
        await self._controller.async_start()

    # -- state ----------------------------------------------------------------

    @property
    def hvac_mode(self) -> HVACMode:
        """Whether the thermostat is heating or off."""
        return self._controller.hvac_mode

    @property
    def hvac_action(self) -> HVACAction:
        """Whether the lamp is actually drawing power right now."""
        return self._controller.hvac_action

    @property
    def current_temperature(self) -> float | None:
        """The last trustworthy reading from the source sensor."""
        return self._controller.current_temperature

    @property
    def target_temperature(self) -> float:
        """The active setpoint."""
        return self._controller.target_temperature

    @property
    def available(self) -> bool:
        """Unavailable only while no reading has ever been obtained."""
        return self._controller.current_temperature is not None

    @property
    def extra_state_attributes(self) -> dict[str, float | str]:
        """Expose the controller internals, which also makes them restorable."""
        controller = self._controller
        return {
            ATTR_INTEGRAL: round(controller.integral, 3),
            ATTR_OUTPUT: round(controller.output, 1),
            ATTR_STATUS: controller.status,
            ATTR_STATUS_DETAIL: controller.status_detail,
        }

    # -- commands -------------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the thermostat between heating and off."""
        await self._controller.async_set_hvac_mode(hvac_mode)

    async def async_turn_on(self) -> None:
        """Turn the thermostat on, which for a heater means heat mode."""
        await self._controller.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the thermostat off and park the dimmer."""
        await self._controller.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs) -> None:
        """Set a new setpoint."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._controller.async_set_target_temperature(float(temperature))

    async def async_reset_integral(self, preload: float | None = None) -> None:
        """Entity service: clear the integral term, optionally preloading it."""
        await self._controller.async_reset_integral(preload)


def _tunable(entry: DimmerThermostatConfigEntry, key: str) -> float:
    """Read a display tunable, preferring options over data over the default."""
    if key in entry.options:
        return float(entry.options[key])
    if key in entry.data:
        return float(entry.data[key])
    return float(DEFAULTS[key])


def _temperature_unit(entry: DimmerThermostatConfigEntry) -> UnitOfTemperature:
    """The unit the source sensor reports in, captured when the entry was made.

    All the gains and limits are in this unit, so the entity reports in it too
    and lets Home Assistant convert for display if the system unit differs.
    """
    stored = entry.data.get(CONF_TEMP_UNIT)
    if stored == UnitOfTemperature.FAHRENHEIT:
        return UnitOfTemperature.FAHRENHEIT
    return UnitOfTemperature.CELSIUS
