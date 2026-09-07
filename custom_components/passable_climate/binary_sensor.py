"""Binary sensor platform for Passable Smart Climate Engine."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ZONES, DOMAIN
from .coordinator import SmartClimateCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up the binary sensor entities for Passable Smart Climate Engine."""
    coordinator: SmartClimateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        PassableHazardVetoBinarySensor(coordinator, entry),
    ]

    zones = entry.data.get(CONF_ZONES, {})
    for zone_id in ["upstairs", "downstairs"]:
        zone_conf = zones.get(zone_id, {})
        zone_name = zone_conf.get("name", zone_id.capitalize())
        entities.append(PassableFreeCoolingBinarySensor(coordinator, entry, zone_id, zone_name))

    async_add_entities(entities)

class PassableHazardVetoBinarySensor(CoordinatorEntity[SmartClimateCoordinator], BinarySensorEntity):
    """Signals if any secondary environmental hazard (rain, high wind, AQI) suppresses window opening."""

    def __init__(self, coordinator: SmartClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_hazard_veto"
        self.entity_id = "binary_sensor.passable_climate_weather_hazard_veto"
        self._attr_name = "Weather Hazard Veto"
        self._attr_device_class = BinarySensorDeviceClass.SAFETY

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_hub")},
            name="Passable Smart Climate Hub",
            manufacturer="Passable Systems",
            model="Smart Climate Engine Hub",
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_hazard_vetoed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "hazard_reason": self.coordinator.hazard_veto_reason,
        }

class PassableFreeCoolingBinarySensor(CoordinatorEntity[SmartClimateCoordinator], BinarySensorEntity):
    """Signals whether free passive cooling/conditioning is currently favorable for this zone."""

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{zone_id}_free_cooling"
        self.entity_id = f"binary_sensor.passable_climate_{zone_id}_free_cooling"
        self._attr_name = f"{zone_name} Free Conditioning Available"
        self._attr_icon = "mdi:air-conditioner"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def is_on(self) -> bool:
        plan = self.coordinator.zone_results.get(self.zone_id)
        if plan:
            return plan.recommended_state == "Open Windows"
        return False
