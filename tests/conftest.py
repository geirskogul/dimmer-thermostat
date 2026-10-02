"""Shared fixtures: a fake dimmer, fake sensors and a ready-made thermostat."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import custom_components
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.dimmer_thermostat.const import (
    CONF_BACKUP_SENSOR,
    CONF_DIMMER,
    CONF_MAX_OUTPUT,
    CONF_SENSOR,
    CONF_TARGET_TEMP,
    CONF_TEMP_UNIT,
    DOMAIN,
)

# The test harness ships its own `custom_components` package, which hides the
# one in this repository unless our folder is added to its search path.
_OURS = str(Path(__file__).resolve().parents[1] / "custom_components")
if _OURS not in custom_components.__path__:
    custom_components.__path__.append(_OURS)

SENSOR = "sensor.enclosure"
BACKUP = "sensor.enclosure_backup"
LIGHT = "light.lamp"
CLIMATE = "climate.vivarium_heat"
STATUS_SENSOR = "sensor.vivarium_heat_controller_status"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration under test."""


def set_temp(
    hass: HomeAssistant, entity_id: str, value: float | str, unit: str = "°C"
) -> None:
    """Publish a temperature reading from a fake sensor."""
    hass.states.async_set(
        entity_id,
        str(value),
        {"unit_of_measurement": unit, "device_class": "temperature"},
    )


class FakeDimmer:
    """A light whose turn_on / turn_off calls are recorded, and can be made to fail."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Register the light services and publish a dimmable light state."""
        self.hass = hass
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None
        self.set_available()
        hass.services.async_register("light", "turn_on", self._handle)
        hass.services.async_register("light", "turn_off", self._handle)

    async def _handle(self, call: ServiceCall) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append((call.service, dict(call.data)))

    def set_available(self) -> None:
        """Show the light as present and dimmable."""
        self.hass.states.async_set(
            LIGHT, "off", {"supported_color_modes": ["brightness"]}
        )

    def set_unavailable(self) -> None:
        """Show the light as dropped off the network."""
        self.hass.states.async_set(LIGHT, "unavailable", {})

    @property
    def level(self) -> float | None:
        """The level last commanded: 0 for off, brightness_pct otherwise."""
        if not self.calls:
            return None
        service, data = self.calls[-1]
        if service == "turn_off":
            return 0
        return data["brightness_pct"]


@pytest.fixture
def dimmer(hass: HomeAssistant) -> FakeDimmer:
    """A fake dimmable light."""
    return FakeDimmer(hass)


def make_entry(
    *,
    backup: bool = False,
    options: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """A config entry for one thermostat on the fake light."""
    entry_data = {
        CONF_NAME: "Vivarium heat",
        CONF_SENSOR: SENSOR,
        CONF_DIMMER: LIGHT,
        CONF_TARGET_TEMP: 30.0,
        CONF_MAX_OUTPUT: 85.0,
        CONF_TEMP_UNIT: "°C",
    }
    if backup:
        entry_data[CONF_BACKUP_SENSOR] = BACKUP
    entry_data.update(data or {})
    return MockConfigEntry(
        domain=DOMAIN,
        title="Vivarium heat",
        unique_id=LIGHT,
        data=entry_data,
        options=options or {},
    )


SetupFn = Callable[..., Awaitable[MockConfigEntry]]


@pytest.fixture
def setup_thermostat(hass: HomeAssistant, dimmer: FakeDimmer) -> SetupFn:
    """Set a thermostat up and, by default, switch it to heat."""

    async def _setup(
        *,
        backup: bool = False,
        options: dict[str, Any] | None = None,
        heat: bool = True,
        entry: MockConfigEntry | None = None,
    ) -> MockConfigEntry:
        entry = entry or make_entry(backup=backup, options=options)
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        if heat:
            await set_mode(hass, "heat")
        return entry

    return _setup


async def set_mode(hass: HomeAssistant, mode: str) -> None:
    """Switch the thermostat's HVAC mode through the climate service."""
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": CLIMATE, "hvac_mode": mode},
        blocking=True,
    )
    await hass.async_block_till_done()


def notifications(hass: HomeAssistant) -> dict[str, Any]:
    """The persistent notifications currently shown."""
    from homeassistant.components import persistent_notification

    return persistent_notification._async_get_or_create_notifications(hass)
