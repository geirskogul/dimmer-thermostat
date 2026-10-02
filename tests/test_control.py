"""The control loop: regulation, sensor guards, actuation and restore."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    mock_restore_cache,
)

from homeassistant.core import HomeAssistant, State

from .conftest import (
    BACKUP,
    CLIMATE,
    SENSOR,
    STATUS_SENSOR,
    FakeDimmer,
    SetupFn,
    make_entry,
    notifications,
    set_mode,
    set_temp,
)


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Move the clock on and let the periodic control pass run."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_heats_below_setpoint(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Below setpoint the dimmer is driven to a proportional level."""
    set_temp(hass, SENSOR, 28)
    entry = await setup_thermostat()
    controller = entry.runtime_data
    assert controller.status == "ok"
    assert 0 < dimmer.level <= 85
    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 28
    assert hass.states.get(STATUS_SENSOR).state == "ok"


async def test_backup_higher_reading_wins(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """With two sensors the hotter one is in control."""
    set_temp(hass, SENSOR, 27)
    set_temp(hass, BACKUP, 29)
    entry = await setup_thermostat(backup=True)
    assert entry.runtime_data.current_temperature == 29
    assert entry.runtime_data.temperature_source == BACKUP


async def test_overtemp_from_backup_cuts(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Either sensor can trip the over-temperature cut."""
    set_temp(hass, SENSOR, 27)
    set_temp(hass, BACKUP, 27)
    entry = await setup_thermostat(backup=True)
    assert dimmer.level > 0
    set_temp(hass, BACKUP, 32)
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "overtemp"
    assert dimmer.level == 0


async def test_one_sensor_offline_degrades(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """If one of two sensors drops out the other carries on."""
    set_temp(hass, SENSOR, 27)
    set_temp(hass, BACKUP, 27)
    entry = await setup_thermostat(backup=True)
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "degraded"
    assert entry.runtime_data.temperature_source == BACKUP
    assert dimmer.level > 0


async def test_both_sensors_offline_fail_safe(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """With both sensors gone the heat goes off."""
    set_temp(hass, SENSOR, 27)
    set_temp(hass, BACKUP, 27)
    entry = await setup_thermostat(backup=True)
    hass.states.async_set(SENSOR, "unavailable")
    hass.states.async_set(BACKUP, "unknown")
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "failsafe"
    assert dimmer.level == 0


async def test_implausibly_hot_never_ignored(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A reading above the plausible range fails safe even with a good backup."""
    set_temp(hass, SENSOR, 27)
    set_temp(hass, BACKUP, 27)
    entry = await setup_thermostat(backup=True)
    set_temp(hass, SENSOR, 80)
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "failsafe"
    assert dimmer.level == 0


async def test_implausibly_cold_ignored_with_backup(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A reading below the plausible range is dropped when there is a backup."""
    set_temp(hass, SENSOR, 27)
    set_temp(hass, BACKUP, 27)
    entry = await setup_thermostat(backup=True)
    set_temp(hass, SENSOR, -20)
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "degraded"
    assert dimmer.level > 0


async def test_single_sensor_unavailable_fails_safe(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """A lone sensor going unavailable turns the heat off and notifies."""
    set_temp(hass, SENSOR, 27)
    entry = await setup_thermostat()
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "failsafe"
    assert dimmer.level == 0
    assert any("failsafe" in key for key in notifications(hass))


async def test_non_numeric_reading_fails_safe(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Garbage from the sensor is treated as no reading."""
    set_temp(hass, SENSOR, 27)
    entry = await setup_thermostat()
    hass.states.async_set(SENSOR, "banana")
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "failsafe"


async def test_off_mode_tracks_temperature(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Switched off, the dimmer is off but the temperature still shows."""
    set_temp(hass, SENSOR, 27)
    entry = await setup_thermostat(heat=False)
    assert entry.runtime_data.status == "off"
    assert dimmer.level == 0
    set_temp(hass, SENSOR, 26)
    await hass.async_block_till_done()
    assert hass.states.get(CLIMATE).attributes["current_temperature"] == 26


async def test_stale_sensor_fails_safe(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """In timeout mode a sensor silent past the timeout fails safe."""
    set_temp(hass, SENSOR, 27)
    entry = await setup_thermostat()
    await _tick(hass, freezer, 600)
    assert entry.runtime_data.status == "ok"
    await _tick(hass, freezer, 700)
    assert entry.runtime_data.status == "failsafe"
    assert dimmer.level == 0


async def test_send_deadband_and_keepalive(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Small changes are held back, but the level is re-sent periodically."""
    set_temp(hass, SENSOR, 29.5)
    await setup_thermostat(options={"ti_minutes": 600})
    sent = len(dimmer.calls)
    for _ in range(14):
        await _tick(hass, freezer, 60)
    assert len(dimmer.calls) == sent
    await _tick(hass, freezer, 120)
    assert len(dimmer.calls) == sent + 1


async def test_number_dimmer_scaled_to_range(
    hass: HomeAssistant, setup_thermostat: SetupFn
) -> None:
    """A number entity is driven across its own min and max."""
    calls = []

    async def _set_value(call):
        calls.append(call.data["value"])

    hass.services.async_register("number", "set_value", _set_value)
    hass.states.async_set("number.heater", "0", {"min": 0, "max": 255})
    set_temp(hass, SENSOR, 28)
    entry = make_entry(data={"dimmer_entity": "number.heater"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await set_mode(hass, "heat")
    output = entry.runtime_data.output
    assert calls[-1] == round(255 * output / 100, 2)


async def test_reset_integral_service(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """The reset service clears and preloads the integral."""
    set_temp(hass, SENSOR, 30)
    entry = await setup_thermostat()
    await hass.services.async_call(
        "dimmer_thermostat",
        "reset_integral",
        {"entity_id": CLIMATE, "preload": 42},
        blocking=True,
    )
    assert round(entry.runtime_data.integral) == 42


async def test_unload_parks_dimmer(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Removing or reloading the integration turns the lamp off."""
    set_temp(hass, SENSOR, 27)
    entry = await setup_thermostat()
    assert dimmer.level > 0
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert dimmer.level == 0


async def test_restore_from_last_state(
    hass: HomeAssistant, dimmer: FakeDimmer, setup_thermostat: SetupFn
) -> None:
    """Mode, setpoint and integral survive a restart (pre-0.4 state format)."""
    mock_restore_cache(
        hass,
        [State(CLIMATE, "heat", {"temperature": 31.5, "pi_integral": 37.0})],
    )
    set_temp(hass, SENSOR, 31.5)
    entry = await setup_thermostat(heat=False)
    controller = entry.runtime_data
    assert controller.hvac_mode == "heat"
    assert controller.target_temperature == 31.5
    assert round(controller.integral) == 37
    assert dimmer.level > 0
