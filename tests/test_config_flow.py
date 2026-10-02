"""The add, reconfigure and tuning forms."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.dimmer_thermostat.const import (
    CONF_BACKUP_SENSOR,
    CONF_DIMMER,
    CONF_MAX_OUTPUT,
    CONF_QUIET_SENSORS,
    CONF_SENSOR,
    CONF_TARGET_TEMP,
    CONF_TEMP_UNIT,
    DOMAIN,
)

from .conftest import BACKUP, LIGHT, SENSOR, FakeDimmer, SetupFn, set_temp

USER_INPUT = {
    "name": "Vivarium heat",
    CONF_SENSOR: SENSOR,
    CONF_DIMMER: LIGHT,
    CONF_TARGET_TEMP: 30,
    CONF_MAX_OUTPUT: 85,
}


async def _start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_user_flow_creates_entry(hass: HomeAssistant, dimmer: FakeDimmer) -> None:
    """A sensor and a dimmable light make an entry that records the unit."""
    set_temp(hass, SENSOR, 25)
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TEMP_UNIT] == "°C"
    assert CONF_BACKUP_SENSOR not in result["data"]
    assert result["result"].unique_id == LIGHT


async def test_user_flow_with_backup(hass: HomeAssistant, dimmer: FakeDimmer) -> None:
    """The optional backup sensor is stored when given."""
    set_temp(hass, SENSOR, 25)
    set_temp(hass, BACKUP, 25)
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**USER_INPUT, CONF_BACKUP_SENSOR: BACKUP}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BACKUP_SENSOR] == BACKUP


async def test_user_flow_rejects_same_sensor(
    hass: HomeAssistant, dimmer: FakeDimmer
) -> None:
    """The backup cannot be the primary sensor again."""
    set_temp(hass, SENSOR, 25)
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**USER_INPUT, CONF_BACKUP_SENSOR: SENSOR}
    )
    assert result["errors"] == {CONF_BACKUP_SENSOR: "same_sensor"}


async def test_user_flow_rejects_unit_mismatch(
    hass: HomeAssistant, dimmer: FakeDimmer
) -> None:
    """The backup must report in the primary sensor's unit."""
    set_temp(hass, SENSOR, 25)
    set_temp(hass, BACKUP, 77, unit="°F")
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**USER_INPUT, CONF_BACKUP_SENSOR: BACKUP}
    )
    assert result["errors"] == {CONF_BACKUP_SENSOR: "unit_mismatch"}


async def test_user_flow_rejects_switch_only_light(hass: HomeAssistant) -> None:
    """A light without brightness cannot be driven proportionally."""
    set_temp(hass, SENSOR, 25)
    hass.states.async_set(LIGHT, "off", {"supported_color_modes": ["onoff"]})
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["errors"] == {CONF_DIMMER: "not_dimmable"}


async def test_user_flow_aborts_on_same_dimmer(
    hass: HomeAssistant, setup_thermostat: SetupFn
) -> None:
    """One dimmer can only be driven by one thermostat."""
    set_temp(hass, SENSOR, 25)
    await setup_thermostat(heat=False)
    flow_id = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_removes_backup(
    hass: HomeAssistant, setup_thermostat: SetupFn
) -> None:
    """Leaving the backup blank in Reconfigure removes it."""
    set_temp(hass, SENSOR, 25)
    set_temp(hass, BACKUP, 25)
    entry = await setup_thermostat(backup=True, heat=False)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Vivarium heat", CONF_SENSOR: SENSOR, CONF_DIMMER: LIGHT},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert CONF_BACKUP_SENSOR not in entry.data
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_options_safety_saves_quiet_mode(
    hass: HomeAssistant, setup_thermostat: SetupFn
) -> None:
    """The Safety page stores the quiet-sensor dropdown as text."""
    set_temp(hass, SENSOR, 25)
    entry = await setup_thermostat(heat=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "safety"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "overtemp_margin": 1.5,
            CONF_QUIET_SENSORS: "auto",
            "sensor_max_age": 1200,
            "temp_min_valid": 5,
            "temp_max_valid": 55,
            "saturation_alert_seconds": 1800,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_QUIET_SENSORS] == "auto"
    assert entry.options["sensor_max_age"] == 1200.0
