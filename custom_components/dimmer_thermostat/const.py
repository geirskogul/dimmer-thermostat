"""Constants and configuration defaults for the Dimmer Thermostat integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "dimmer_thermostat"
PLATFORMS: Final = [Platform.CLIMATE, Platform.SENSOR]

MANUFACTURER: Final = "Dimmer Thermostat"
MODEL: Final = "PI proportional heater"

# --- Config entry data keys (set at creation, changed via reconfigure) ---------

CONF_SENSOR: Final = "temperature_sensor"
CONF_BACKUP_SENSOR: Final = "backup_temperature_sensor"
CONF_DIMMER: Final = "dimmer_entity"
CONF_TEMP_UNIT: Final = "temperature_unit"

# --- Options keys (changed via the options flow) ------------------------------

CONF_KP: Final = "kp"
CONF_TI_MINUTES: Final = "ti_minutes"
CONF_CYCLE_SECONDS: Final = "cycle_seconds"

CONF_MIN_OUTPUT: Final = "min_output"
CONF_MAX_OUTPUT: Final = "max_output"
CONF_STARTUP_OUTPUT: Final = "startup_output"
CONF_SEND_DEADBAND: Final = "send_deadband"
CONF_RESEND_SECONDS: Final = "resend_seconds"

CONF_MIN_TEMP: Final = "min_temp"
CONF_MAX_TEMP: Final = "max_temp"
CONF_TARGET_TEMP: Final = "target_temp"
CONF_PRECISION: Final = "precision"

CONF_OVERTEMP_MARGIN: Final = "overtemp_margin"
CONF_SENSOR_MAX_AGE: Final = "sensor_max_age"
CONF_TEMP_MIN_VALID: Final = "temp_min_valid"
CONF_TEMP_MAX_VALID: Final = "temp_max_valid"
CONF_SATURATION_ALERT: Final = "saturation_alert_seconds"

# --- Defaults -----------------------------------------------------------------
#
# Gains are expressed in percent of dimmer output per degree, in whatever unit
# the source sensor reports. TI is the classic integral time: the integral term
# contributes KP / TI per degree-minute of accumulated error.

DEFAULTS: Final[dict[str, float | int]] = {
    CONF_KP: 12.0,
    CONF_TI_MINUTES: 30.0,
    CONF_CYCLE_SECONDS: 60,
    CONF_MIN_OUTPUT: 0.0,
    CONF_MAX_OUTPUT: 85.0,
    CONF_STARTUP_OUTPUT: 20.0,
    CONF_SEND_DEADBAND: 2.0,
    CONF_RESEND_SECONDS: 900,
    CONF_MIN_TEMP: 15.0,
    CONF_MAX_TEMP: 45.0,
    CONF_TARGET_TEMP: 30.0,
    CONF_PRECISION: 0.5,
    CONF_OVERTEMP_MARGIN: 1.5,
    CONF_SENSOR_MAX_AGE: 1200,
    CONF_TEMP_MIN_VALID: 5.0,
    CONF_TEMP_MAX_VALID: 55.0,
    CONF_SATURATION_ALERT: 1800,
}

# Domains accepted as the dimmer. `light` is driven with brightness_pct; the
# number domains are driven with set_value scaled across their own min/max.
LIGHT_DOMAIN: Final = "light"
NUMBER_DOMAINS: Final = ("number", "input_number")
SUPPORTED_DIMMER_DOMAINS: Final = (LIGHT_DOMAIN, *NUMBER_DOMAINS)

# Status strings published by the controller.
STATUS_OK: Final = "ok"
STATUS_DEGRADED: Final = "degraded"
STATUS_OFF: Final = "off"
STATUS_OVERTEMP: Final = "overtemp"
STATUS_FAILSAFE: Final = "failsafe"
STATUS_STARTING: Final = "starting"

ATTR_INTEGRAL: Final = "pi_integral"
ATTR_OUTPUT: Final = "output_percent"
ATTR_STATUS: Final = "controller_status"
ATTR_STATUS_DETAIL: Final = "controller_status_detail"
ATTR_TEMPERATURE_SOURCE: Final = "temperature_source"

NOTIFY_THROTTLE_SECONDS: Final = 1800
