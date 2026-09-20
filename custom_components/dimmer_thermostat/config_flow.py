"""Config and options flows: the UI for adding and tuning a thermostat."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CYCLE_SECONDS,
    CONF_DIMMER,
    CONF_KP,
    CONF_MAX_OUTPUT,
    CONF_MAX_TEMP,
    CONF_MIN_OUTPUT,
    CONF_MIN_TEMP,
    CONF_OVERTEMP_MARGIN,
    CONF_PRECISION,
    CONF_RESEND_SECONDS,
    CONF_SATURATION_ALERT,
    CONF_SEND_DEADBAND,
    CONF_SENSOR,
    CONF_SENSOR_MAX_AGE,
    CONF_STARTUP_OUTPUT,
    CONF_TARGET_TEMP,
    CONF_TEMP_MAX_VALID,
    CONF_TEMP_MIN_VALID,
    CONF_TEMP_UNIT,
    CONF_TI_MINUTES,
    DEFAULTS,
    DOMAIN,
    LIGHT_DOMAIN,
    SUPPORTED_DIMMER_DOMAINS,
)


# -- selector shorthands -------------------------------------------------------


def _number(minimum: float, maximum: float, step: float, unit: str | None = None):
    """A box-style number selector over the given range."""
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement=unit,
        )
    )


def _entity(domain: str | list[str], device_class: str | None = None):
    """An entity picker restricted to the given domain(s)."""
    config = selector.EntitySelectorConfig(domain=domain)
    if device_class is not None:
        config["device_class"] = device_class
    return selector.EntitySelector(config)


# -- schemas -------------------------------------------------------------------

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Vivarium heat"): selector.TextSelector(),
        vol.Required(CONF_SENSOR): _entity("sensor", device_class="temperature"),
        vol.Required(CONF_DIMMER): _entity(list(SUPPORTED_DIMMER_DOMAINS)),
        vol.Required(CONF_TARGET_TEMP, default=DEFAULTS[CONF_TARGET_TEMP]): _number(
            0, 100, 0.5
        ),
        vol.Required(CONF_MAX_OUTPUT, default=DEFAULTS[CONF_MAX_OUTPUT]): _number(
            1, 100, 1, "%"
        ),
    }
)

REGULATION_KEYS = (
    (CONF_KP, _number(0, 200, 0.5)),
    (CONF_TI_MINUTES, _number(0.5, 600, 0.5, "min")),
    (CONF_CYCLE_SECONDS, _number(10, 600, 5, "s")),
    (CONF_STARTUP_OUTPUT, _number(0, 100, 1, "%")),
    (CONF_SEND_DEADBAND, _number(0, 50, 0.5, "%")),
    (CONF_RESEND_SECONDS, _number(60, 7200, 30, "s")),
)

LIMITS_KEYS = (
    (CONF_MIN_OUTPUT, _number(0, 100, 1, "%")),
    (CONF_MAX_OUTPUT, _number(1, 100, 1, "%")),
    (CONF_MIN_TEMP, _number(-50, 200, 0.5)),
    (CONF_MAX_TEMP, _number(-50, 200, 0.5)),
    (CONF_PRECISION, _number(0.1, 5, 0.1)),
)

SAFETY_KEYS = (
    (CONF_OVERTEMP_MARGIN, _number(0.1, 20, 0.1)),
    (CONF_SENSOR_MAX_AGE, _number(60, 21600, 60, "s")),
    (CONF_TEMP_MIN_VALID, _number(-50, 200, 0.5)),
    (CONF_TEMP_MAX_VALID, _number(-50, 200, 0.5)),
    (CONF_SATURATION_ALERT, _number(300, 21600, 60, "s")),
)


# -- shared validation ---------------------------------------------------------


def _resolve_temperature_unit(hass, sensor_entity_id: str) -> str:
    """The unit the chosen sensor reports in, falling back to the system unit."""
    state = hass.states.get(sensor_entity_id)
    unit = state.attributes.get("unit_of_measurement") if state else None
    if unit in (UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT):
        return unit
    return hass.config.units.temperature_unit


def _dimmer_error(hass, dimmer_entity_id: str) -> str | None:
    """Return an error key if the chosen dimmer cannot accept a level."""
    domain = dimmer_entity_id.split(".", 1)[0]
    if domain not in SUPPORTED_DIMMER_DOMAINS:
        return "unsupported_dimmer"
    if domain != LIGHT_DOMAIN:
        return None

    state = hass.states.get(dimmer_entity_id)
    if state is None:
        return None
    modes = state.attributes.get("supported_color_modes")
    if modes is None:
        return None
    if not any(mode in ("brightness", "color_temp", "hs", "rgb", "rgbw", "rgbww", "xy")
               for mode in modes):
        return "not_dimmable"
    return None


def _validate_user_input(hass, user_input: dict[str, Any]) -> dict[str, str]:
    """Check the entity choices, returning a field-keyed error dict."""
    errors: dict[str, str] = {}
    dimmer_error = _dimmer_error(hass, user_input[CONF_DIMMER])
    if dimmer_error:
        errors[CONF_DIMMER] = dimmer_error
    return errors


def _entry_data(hass, user_input: dict[str, Any]) -> dict[str, Any]:
    """Build the config entry data, capturing the sensor's temperature unit."""
    return {
        CONF_NAME: user_input[CONF_NAME],
        CONF_SENSOR: user_input[CONF_SENSOR],
        CONF_DIMMER: user_input[CONF_DIMMER],
        CONF_TARGET_TEMP: float(user_input[CONF_TARGET_TEMP]),
        CONF_MAX_OUTPUT: float(user_input[CONF_MAX_OUTPUT]),
        CONF_TEMP_UNIT: _resolve_temperature_unit(hass, user_input[CONF_SENSOR]),
    }


# -- config flow ---------------------------------------------------------------


class DimmerThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a thermostat from the UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the name, the sensor, the dimmer and a starting setpoint."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA)

        errors = _validate_user_input(self.hass, user_input)
        if errors:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
            )

        await self.async_set_unique_id(user_input[CONF_DIMMER])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=user_input[CONF_NAME], data=_entry_data(self.hass, user_input)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the sensor or the dimmer of an existing thermostat."""
        entry = self._get_reconfigure_entry()
        if user_input is None:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA, entry.data
                ),
            )

        errors = _validate_user_input(self.hass, user_input)
        if errors:
            return self.async_show_form(
                step_id="reconfigure",
                data_schema=self.add_suggested_values_to_schema(
                    STEP_USER_SCHEMA, user_input
                ),
                errors=errors,
            )

        await self.async_set_unique_id(user_input[CONF_DIMMER])
        self._abort_if_unique_id_mismatch()
        return self.async_update_reload_and_abort(
            entry, title=user_input[CONF_NAME], data=_entry_data(self.hass, user_input)
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the tuning flow."""
        return DimmerThermostatOptionsFlow()


# -- options flow --------------------------------------------------------------


class DimmerThermostatOptionsFlow(OptionsFlow):
    """Tune an existing thermostat without recreating it."""

    def __init__(self) -> None:
        """Collect edits across steps before writing them back in one go."""
        self._collected: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the three groups of tunables."""
        return self.async_show_menu(
            step_id="init", menu_options=["regulation", "limits", "safety"]
        )

    async def async_step_regulation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Gains, integral time and how often the loop runs."""
        return await self._async_handle_group("regulation", REGULATION_KEYS, user_input)

    async def async_step_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Output range and the setpoint range shown on the thermostat card."""
        return await self._async_handle_group("limits", LIMITS_KEYS, user_input)

    async def async_step_safety(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Over-temperature cut, staleness timeout and plausibility window."""
        return await self._async_handle_group("safety", SAFETY_KEYS, user_input)

    # -- shared group handling ------------------------------------------------

    async def _async_handle_group(
        self,
        step_id: str,
        keys: tuple[tuple[str, Any], ...],
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Show or save one group of tunables."""
        if user_input is None:
            return self.async_show_form(
                step_id=step_id, data_schema=self._build_schema(keys)
            )

        options = dict(self.config_entry.options)
        options.update({key: float(value) for key, value in user_input.items()})
        return self.async_create_entry(data=options)

    def _build_schema(self, keys: tuple[tuple[str, Any], ...]) -> vol.Schema:
        """Build a schema for one group, prefilled with the current values."""
        fields: dict[Any, Any] = {}
        for key, field_selector in keys:
            fields[vol.Required(key, default=self._current(key))] = field_selector
        return vol.Schema(fields)

    def _current(self, key: str) -> float:
        """The value in force for a tunable right now."""
        entry = self.config_entry
        if key in entry.options:
            return float(entry.options[key])
        if key in entry.data:
            return float(entry.data[key])
        return float(DEFAULTS[key])
