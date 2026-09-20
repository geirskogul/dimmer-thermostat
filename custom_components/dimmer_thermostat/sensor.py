"""Sensor platform: the controller's internals, so they can be charted."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DimmerThermostatConfigEntry
from .controller import DimmerThermostatController
from .entity import DimmerThermostatEntity


@dataclass(frozen=True, kw_only=True)
class DimmerSensorDescription(SensorEntityDescription):
    """A sensor description that knows how to read its own value."""

    value_fn: Callable[[DimmerThermostatController], float | str | None]


SENSORS: tuple[DimmerSensorDescription, ...] = (
    DimmerSensorDescription(
        key="output",
        translation_key="output",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:brightness-percent",
        value_fn=lambda controller: round(controller.output, 1),
    ),
    DimmerSensorDescription(
        key="integral",
        translation_key="integral",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:sigma",
        value_fn=lambda controller: round(controller.integral, 2),
    ),
    DimmerSensorDescription(
        key="status",
        translation_key="status",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:stethoscope",
        value_fn=lambda controller: controller.status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimmerThermostatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one sensor per description."""
    controller = entry.runtime_data
    async_add_entities(
        DimmerThermostatSensor(controller, entry, description)
        for description in SENSORS
    )


class DimmerThermostatSensor(DimmerThermostatEntity, SensorEntity):
    """Reads one value off the controller."""

    entity_description: DimmerSensorDescription

    def __init__(
        self,
        controller: DimmerThermostatController,
        entry: DimmerThermostatConfigEntry,
        description: DimmerSensorDescription,
    ) -> None:
        """Bind this sensor to one controller reading."""
        super().__init__(controller, entry)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float | str | None:
        """The current value of this reading."""
        return self.entity_description.value_fn(self._controller)

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Carry the human-readable detail alongside the status sensor."""
        if self.entity_description.key != "status":
            return None
        return {"detail": self._controller.status_detail}
