"""Number platform for Passable Smart Climate Engine.
Provides interactive controls for the ASHRAE 55 Psychrometric Box comfort boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_COMFORT_DEW_POINT_MAX,
    CONF_COMFORT_HUMIDITY_MAX,
    CONF_COMFORT_HUMIDITY_MIN,
    CONF_COMFORT_TEMP_MAX,
    CONF_COMFORT_TEMP_MIN,
    DEFAULT_COMFORT_DEW_POINT_MAX,
    DEFAULT_COMFORT_HUMIDITY_MAX,
    DEFAULT_COMFORT_HUMIDITY_MIN,
    DEFAULT_COMFORT_TEMP_MAX,
    DEFAULT_COMFORT_TEMP_MIN,
    DOMAIN,
)
from .coordinator import SmartClimateCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PassableComfortNumberDescription(NumberEntityDescription):
    """Description for Passable Climate comfort number entities."""

    key: str
    default_value: float


COMFORT_NUMBER_DESCRIPTIONS: tuple[PassableComfortNumberDescription, ...] = (
    PassableComfortNumberDescription(
        key=CONF_COMFORT_TEMP_MIN,
        name="Comfort Temp Floor",
        icon="mdi:thermometer-chevron-down",
        native_min_value=60.0,
        native_max_value=72.0,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.SLIDER,
        default_value=DEFAULT_COMFORT_TEMP_MIN,
    ),
    PassableComfortNumberDescription(
        key=CONF_COMFORT_TEMP_MAX,
        name="Comfort Temp Ceiling",
        icon="mdi:thermometer-chevron-up",
        native_min_value=72.0,
        native_max_value=84.0,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.SLIDER,
        default_value=DEFAULT_COMFORT_TEMP_MAX,
    ),
    PassableComfortNumberDescription(
        key=CONF_COMFORT_HUMIDITY_MIN,
        name="Comfort Humidity Floor",
        icon="mdi:water-percent",
        native_min_value=15.0,
        native_max_value=35.0,
        native_step=1.0,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.HUMIDITY,
        mode=NumberMode.SLIDER,
        default_value=DEFAULT_COMFORT_HUMIDITY_MIN,
    ),
    PassableComfortNumberDescription(
        key=CONF_COMFORT_HUMIDITY_MAX,
        name="Comfort Humidity Ceiling (Mold Cap)",
        icon="mdi:water-percent",
        native_min_value=45.0,
        native_max_value=70.0,
        native_step=1.0,
        native_unit_of_measurement=PERCENTAGE,
        device_class=NumberDeviceClass.HUMIDITY,
        mode=NumberMode.SLIDER,
        default_value=DEFAULT_COMFORT_HUMIDITY_MAX,
    ),
    PassableComfortNumberDescription(
        key=CONF_COMFORT_DEW_POINT_MAX,
        name="Comfort Dew Point Ceiling",
        icon="mdi:water-thermometer-outline",
        native_min_value=50.0,
        native_max_value=65.0,
        native_step=0.5,
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        device_class=NumberDeviceClass.TEMPERATURE,
        mode=NumberMode.SLIDER,
        default_value=DEFAULT_COMFORT_DEW_POINT_MAX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Passable Climate number entities from config entry."""
    coordinator: SmartClimateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PassableComfortNumberEntity(coordinator, entry, desc)
        for desc in COMFORT_NUMBER_DESCRIPTIONS
    ]
    async_add_entities(entities)


class PassableComfortNumberEntity(CoordinatorEntity[SmartClimateCoordinator], RestoreNumber):
    """Representation of a Passable Climate comfort setting number entity."""

    entity_description: PassableComfortNumberDescription

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        description: PassableComfortNumberDescription,
    ) -> None:
        """Initialize the comfort number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self.entity_id = f"number.passable_climate_{description.key}"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Passable Smart Climate Engine",
            manufacturer="Passable Systems",
            model="Thermal & Psychrometric Optimizer",
        )
        self._current_value: float = description.default_value

    @property
    def native_value(self) -> float:
        """Return the current setting value."""
        return self.coordinator.comfort_settings.get(
            self.entity_description.key, self._current_value
        )

    async def async_added_to_hass(self) -> None:
        """Restore last state on HA startup."""
        await super().async_added_to_hass()
        last_number_data = await self.async_get_last_number_data()
        if last_number_data is not None and last_number_data.native_value is not None:
            val = float(last_number_data.native_value)
            self._current_value = val
            self.coordinator.comfort_settings[self.entity_description.key] = val
        else:
            self.coordinator.comfort_settings[self.entity_description.key] = (
                self.entity_description.default_value
            )

    async def async_set_native_value(self, value: float) -> None:
        """Set new value and trigger coordinator refresh."""
        self._current_value = float(value)
        self.async_write_ha_state()
        await self.coordinator.async_set_comfort_param(
            self.entity_description.key, float(value)
        )
