"""Shared entity base for the Dimmer Thermostat platforms."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .controller import DimmerThermostatController


class DimmerThermostatEntity(Entity):
    """Base entity: attaches to the thermostat device and follows the controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, controller: DimmerThermostatController, entry: ConfigEntry
    ) -> None:
        """Bind the entity to its controller and to the config entry's device."""
        self._controller = controller
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=MODEL,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to controller updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._controller.async_add_listener(self._handle_controller_update)
        )

    @callback
    def _handle_controller_update(self) -> None:
        """Write the new controller state out, if this entity is live enough to.

        The controller runs its first pass from inside the climate entity's
        `async_added_to_hass`, so a sibling entity can legitimately still be
        mid-registration when the first update arrives.
        """
        if self.hass is None or self.entity_id is None:
            return
        self.async_write_ha_state()
