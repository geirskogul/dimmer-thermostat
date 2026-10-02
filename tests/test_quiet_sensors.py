"""How sensors that only report on change are judged."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import BACKUP, SENSOR, FakeDimmer, SetupFn, set_temp

HOUR = 3600


def _register(
    hass: HomeAssistant, entity_id: str, platform: str, unique_id: str
) -> str:
    """Put a sensor (and a battery sibling) on a device from the given integration."""
    source = MockConfigEntry(domain=platform)
    source.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=source.entry_id, identifiers={(platform, unique_id)}
    )
    registry = er.async_get(hass)
    object_id = entity_id.split(".", 1)[1]
    registry.async_get_or_create(
        "sensor",
        platform,
        unique_id,
        device_id=device.id,
        suggested_object_id=object_id,
        config_entry=source,
    )
    battery = registry.async_get_or_create(
        "sensor",
        platform,
        f"{unique_id}_battery",
        device_id=device.id,
        suggested_object_id=f"{object_id}_battery",
        config_entry=source,
    )
    return battery.entity_id


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def _run_quiet(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    """Let time pass in one-minute control cycles."""
    for _ in range(int(seconds // 60)):
        await _tick(hass, freezer, 60)


async def test_sibling_report_counts_as_life(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A battery report from the same device keeps a steady temperature fresh."""
    battery = _register(hass, SENSOR, "zha", "zha-temp")
    set_temp(hass, SENSOR, 29)
    entry = await setup_thermostat()
    await _run_quiet(hass, freezer, 900)
    hass.states.async_set(battery, "90")
    await _run_quiet(hass, freezer, 900)
    assert entry.runtime_data.status == "ok"


async def test_trusted_zha_sensor_quiet_for_hours(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """In automatic mode a ZHA sensor is held until ZHA marks it unavailable."""
    _register(hass, SENSOR, "zha", "zha-temp")
    set_temp(hass, SENSOR, 29)
    entry = await setup_thermostat(options={"quiet_sensor_handling": "auto"})
    await _tick(hass, freezer, 10 * HOUR)
    assert entry.runtime_data.status == "ok"
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    assert entry.runtime_data.status == "failsafe"


async def test_z2m_heartbeat_window(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A Zigbee2MQTT sensor gets two hours, then fails safe."""
    _register(hass, SENSOR, "mqtt", "0x00158d0001_temperature_zigbee2mqtt")
    set_temp(hass, SENSOR, 29)
    entry = await setup_thermostat(options={"quiet_sensor_handling": "auto"})
    await _tick(hass, freezer, 1.5 * HOUR)
    assert entry.runtime_data.status == "ok"
    await _tick(hass, freezer, 0.75 * HOUR)
    assert entry.runtime_data.status == "failsafe"


async def test_plain_mqtt_uses_timeout(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An MQTT sensor that is not from Zigbee2MQTT gets the staleness timeout."""
    _register(hass, SENSOR, "mqtt", "garage_temperature")
    set_temp(hass, SENSOR, 29)
    entry = await setup_thermostat(options={"quiet_sensor_handling": "auto"})
    await _tick(hass, freezer, 1500)
    assert entry.runtime_data.status == "failsafe"


async def test_unregistered_sensor_uses_timeout(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A sensor with no registry entry gets the staleness timeout."""
    set_temp(hass, SENSOR, 29)
    entry = await setup_thermostat(options={"quiet_sensor_handling": "auto"})
    await _tick(hass, freezer, 1500)
    assert entry.runtime_data.status == "failsafe"


async def test_mixed_zha_primary_z2m_backup(
    hass: HomeAssistant,
    dimmer: FakeDimmer,
    setup_thermostat: SetupFn,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Each sensor is judged by its own integration."""
    _register(hass, SENSOR, "zha", "zha-temp")
    _register(hass, BACKUP, "mqtt", "0x00158d0002_temperature_zigbee2mqtt")
    set_temp(hass, SENSOR, 29)
    set_temp(hass, BACKUP, 29)
    entry = await setup_thermostat(
        backup=True, options={"quiet_sensor_handling": "auto"}
    )
    await _tick(hass, freezer, 3 * HOUR)
    controller = entry.runtime_data
    assert controller.status == "degraded"
    assert controller.temperature_source == SENSOR
