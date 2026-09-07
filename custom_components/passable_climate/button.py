"""Button platform for Passable Smart Climate Engine."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartClimateCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up button entities for Passable Smart Climate Engine."""
    coordinator: SmartClimateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        PassableRetrainModelsButton(coordinator, entry),
        PassableRefreshAdvisorButton(coordinator, entry),
    ])

class PassableRetrainModelsButton(CoordinatorEntity[SmartClimateCoordinator], ButtonEntity):
    """Triggers an immediate background InfluxDB regression retraining of all 16 models."""

    def __init__(self, coordinator: SmartClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_retrain_models"
        self.entity_id = "button.passable_climate_retrain_thermal_models"
        self._attr_name = "Retrain Thermal Models"
        self._attr_icon = "mdi:brain"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_hub")},
            name="Passable Smart Climate Hub",
            manufacturer="Passable Systems",
            model="Smart Climate Engine Hub",
        )

    async def async_press(self) -> None:
        """Triggers the background training job."""
        self.hass.async_create_task(self.coordinator.async_retrain_models())

class PassableRefreshAdvisorButton(CoordinatorEntity[SmartClimateCoordinator], ButtonEntity):
    """Manually forces an immediate evaluation of the window advisor and simulation plan."""

    def __init__(self, coordinator: SmartClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_refresh_plan"
        self.entity_id = "button.passable_climate_refresh_window_recommendation"
        self._attr_name = "Refresh Window Recommendation"
        self._attr_icon = "mdi:refresh"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_hub")},
            name="Passable Smart Climate Hub",
            manufacturer="Passable Systems",
            model="Smart Climate Engine Hub",
        )

    async def async_press(self) -> None:
        """Forces an immediate coordinator refresh."""
        await self.coordinator.async_request_refresh()

