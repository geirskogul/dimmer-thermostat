"""Dimmer faults, restarts, renames, the startup grace and turning fully off."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.dimmer_thermostat import controller as controller_module
from custom_components.dimmer_thermostat.const import (
    CONF_DIMMER,
    CONF_MAX_OUTPUT,
    CONF_SENSOR,
    CONF_TARGET_TEMP,
)

from .conftest import (
    CLIMATE,
    LIGHT,
    SENSOR,
    FakeDimmer,
    SetupFn,
    notifications,
    set_mode,
    set_temp,
)


def _kinds(hass: HomeAssistant) -> set[str]:
    """The kinds of thermostat notification currently shown."""
    return {key.rsplit("_", 1)[1] for key in notifications(hass)}


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# -- the dimmer ----------------------------------------------------------------


async def test_unavailable_dimmer_reported_and_resent_on_return(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A dropped dimmer is reported, and gets its level again when it returns."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat(options={"ti_minutes": 600})
    controller = entry.runtime_data
    sent = len(dimmer.calls)

    dimmer.set_unavailable()
    await _tick(hass, freezer, 60)
    assert controller.status == "degraded"
    assert "unavailable" in controller.status_detail
    assert "dimmer" in _kinds(hass)
    assert len(dimmer.calls) == sent

    dimmer.set_available()
    await hass.async_block_till_done()
    assert len(dimmer.calls) == sent + 1
    assert dimmer.level > 0
    assert controller.status == "ok"
    assert "dimmer" not in _kinds(hass)


async def test_missing_dimmer_reported(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A dimmer that no longer exists is reported rather than silently skipped."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()
    hass.states.async_remove(LIGHT)
    await entry.runtime_data.async_control()
    assert "does not exist" in entry.runtime_data.status_detail
    assert "dimmer" in _kinds(hass)


async def test_dimmer_error_is_caught_and_retried(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A rejected command does not stop the loop, and is retried next pass."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat(options={"ti_minutes": 600})
    controller = entry.runtime_data
    dimmer.error = HomeAssistantError("Failed to send request: device did not respond")
    controller._last_sent_output = None  # make the next pass send
    await _tick(hass, freezer, 60)
    assert controller.status == "degraded"
    assert "did not respond" in controller.status_detail
    assert "dimmer" in _kinds(hass)

    dimmer.error = None
    sent = len(dimmer.calls)
    await _tick(hass, freezer, 60)
    assert len(dimmer.calls) == sent + 1
    assert controller.status == "ok"


async def test_hung_dimmer_times_out(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dimmer call that never returns cannot stall the loop."""
    monkeypatch.setattr(controller_module, "ACTUATOR_TIMEOUT_SECONDS", 0.05)
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()

    async def _hang(call) -> None:
        await asyncio.Event().wait()

    hass.services.async_register("light", "turn_off", _hang)
    await set_mode(hass, "off")
    assert "did not respond" in entry.runtime_data.status_detail


async def test_unload_survives_failing_dimmer(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A dimmer error while unloading cannot leave the entry stuck."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()
    dimmer.error = HomeAssistantError("boom")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_overtemp_with_unreachable_dimmer_says_so(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """The over-temperature alert never claims a cut that did not happen."""
    set_temp(hass, SENSOR, 28)
    await setup_thermostat()
    dimmer.set_unavailable()
    set_temp(hass, SENSOR, 33)
    await hass.async_block_till_done()
    message = next(
        n["message"] for key, n in notifications(hass).items() if key.endswith("overtemp")
    )
    assert "could NOT be switched off" in message


async def test_no_control_after_stop(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A trigger arriving after unload cannot turn the lamp back on."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()
    controller = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    sent = len(dimmer.calls)
    await controller.async_control()
    assert len(dimmer.calls) == sent
    assert dimmer.level == 0


# -- fully off on every safety path ---------------------------------------------


async def test_safety_paths_switch_fully_off_above_min_output(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """With a minimum output set, failsafe, overtemp, off and unload still go to 0."""
    set_temp(hass, SENSOR, 30.4)
    entry = await setup_thermostat(options={"min_output": 20})
    assert dimmer.level >= 20

    set_temp(hass, SENSOR, 32)
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "overtemp"
    assert dimmer.level == 0

    set_temp(hass, SENSOR, 29)
    await hass.async_block_till_done()
    assert dimmer.level >= 20
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "failsafe"
    assert dimmer.level == 0

    set_temp(hass, SENSOR, 29)
    await hass.async_block_till_done()
    await set_mode(hass, "off")
    assert dimmer.level == 0

    await set_mode(hass, "heat")
    assert dimmer.level >= 20
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert dimmer.level == 0


async def test_turning_off_updates_the_card_at_once(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """The climate state shows off straight after the service call."""
    set_temp(hass, SENSOR, 28)
    await setup_thermostat()
    await hass.services.async_call(
        "climate", "turn_off", {"entity_id": CLIMATE}, blocking=True
    )
    assert hass.states.get(CLIMATE).state == "off"


# -- notifications ---------------------------------------------------------------


async def test_notifications_cleared_on_recovery(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A failsafe notification goes away when the sensor comes back."""
    set_temp(hass, SENSOR, 28)
    await setup_thermostat()
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert "failsafe" in _kinds(hass)
    set_temp(hass, SENSOR, 28)
    await hass.async_block_till_done()
    assert "failsafe" not in _kinds(hass)
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert "failsafe" in _kinds(hass)  # reported again, not throttled


async def test_reload_clears_leftover_notifications(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A notification raised before a reload is cleared once things are fine."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert "failsafe" in _kinds(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    set_temp(hass, SENSOR, 28)
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "ok"
    assert "failsafe" not in _kinds(hass)


# -- restarts -------------------------------------------------------------------


async def test_restore_while_unavailable(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Saved while unavailable, mode, setpoint and integral still come back."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(CLIMATE, "unavailable"),
                {"hvac_mode": "heat", "target_temperature": 32.0, "integral": 40.0},
            )
        ],
    )
    set_temp(hass, SENSOR, 32)
    entry = await setup_thermostat(heat=False)
    controller = entry.runtime_data
    assert controller.hvac_mode == "heat"
    assert controller.target_temperature == 32.0
    assert round(controller.integral) == 40
    assert controller.restore_data()["hvac_mode"] == "heat"


async def test_startup_grace_is_quiet(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """While HA starts, a missing sensor cuts the heat without notifying."""
    mock_restore_cache_with_extra_data(
        hass, [(State(CLIMATE, "unavailable"), {"hvac_mode": "heat"})]
    )
    hass.set_state(CoreState.starting)
    entry = await setup_thermostat(heat=False)
    controller = entry.runtime_data
    assert controller.status == "starting"
    assert dimmer.level == 0
    assert not _kinds(hass)

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    assert controller.status == "starting"
    assert not _kinds(hass)

    await _tick(hass, freezer, 120)
    assert controller.status == "failsafe"
    assert "failsafe" in _kinds(hass)


async def test_startup_sensor_arrives_in_grace(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A sensor that turns up during startup takes over without any alert."""
    mock_restore_cache_with_extra_data(
        hass, [(State(CLIMATE, "unavailable"), {"hvac_mode": "heat"})]
    )
    hass.set_state(CoreState.starting)
    entry = await setup_thermostat(heat=False)
    set_temp(hass, SENSOR, 28)
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "ok"
    assert dimmer.level > 0
    assert not _kinds(hass)


# -- renames --------------------------------------------------------------------


async def test_follows_sensor_rename(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Renaming the sensor's entity id keeps the thermostat working."""
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "demo", "t1", suggested_object_id="enclosure")
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()

    registry.async_update_entity(SENSOR, new_entity_id="sensor.terrarium")
    set_temp(hass, "sensor.terrarium", 28)
    await hass.async_block_till_done()

    assert entry.data[CONF_SENSOR] == "sensor.terrarium"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.temperature_source == "sensor.terrarium"


async def test_follows_dimmer_rename(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Renaming the dimmer updates the entry and its unique id."""
    registry = er.async_get(hass)
    hass.states.async_remove(LIGHT)
    registry.async_get_or_create("light", "demo", "l1", suggested_object_id="lamp")
    dimmer.set_available()
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()

    registry.async_update_entity(LIGHT, new_entity_id="light.basking_lamp")
    hass.states.async_set(
        "light.basking_lamp", "on", {"supported_color_modes": ["brightness"]}
    )
    await hass.async_block_till_done()

    assert entry.data[CONF_DIMMER] == "light.basking_lamp"
    assert entry.unique_id == "light.basking_lamp"
    assert entry.state is ConfigEntryState.LOADED


# -- forms ----------------------------------------------------------------------


async def test_options_reject_inverted_output_range(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Minimum output at or above maximum is refused."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat(heat=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "limits"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "min_output": 50,
            "max_output": 40,
            "min_temp": 15,
            "max_temp": 45,
            "precision": 0.5,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"max_output": "output_range"}


async def test_options_reject_inverted_plausible_range(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """The lowest plausible reading must be below the highest."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat(heat=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "safety"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "overtemp_margin": 1.5,
            "quiet_sensor_handling": "timeout",
            "sensor_max_age": 1200,
            "temp_min_valid": 60,
            "temp_max_valid": 55,
            "saturation_alert_seconds": 1800,
        },
    )
    assert result["errors"] == {"temp_max_valid": "plausible_range"}


async def test_options_save_reloads(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Saving tuning reloads the entry so it takes effect."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()
    old_controller = entry.runtime_data
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "regulation"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "kp": 8,
            "ti_minutes": 40,
            "cycle_seconds": 60,
            "startup_output": 20,
            "send_deadband": 2,
            "resend_seconds": 900,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.runtime_data is not old_controller
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_keeps_starting_values_and_reloads_once(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Reconfigure keeps the setpoint and max output, without the old double reload."""
    set_temp(hass, SENSOR, 28)
    set_temp(hass, "sensor.other", 28)
    entry = await setup_thermostat(heat=False)
    result = await entry.start_reconfigure_flow(hass)
    assert CONF_TARGET_TEMP not in result["data_schema"].schema
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Vivarium heat", CONF_SENSOR: "sensor.other", CONF_DIMMER: LIGHT},
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_SENSOR] == "sensor.other"
    assert entry.data[CONF_TARGET_TEMP] == 30.0
    assert entry.data[CONF_MAX_OUTPUT] == 85.0
    assert "update listener" not in caplog.text
