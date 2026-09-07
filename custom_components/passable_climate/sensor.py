"""Sensor platform for Passable Smart Climate Engine.
Registers all 16 canonical plot_data sensors and simulation trajectory entities.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ZONES,
    DOMAIN,
    METRIC_HUMIDITY_CLOSED,
    METRIC_HUMIDITY_COOL,
    METRIC_HUMIDITY_HEAT,
    METRIC_HUMIDITY_OPEN,
    METRIC_TEMP_CLOSED,
    METRIC_TEMP_COOL,
    METRIC_TEMP_HEAT,
    METRIC_TEMP_OPEN,
    PLOT_METRIC_KEYS,
)
from .coordinator import SmartClimateCoordinator
from .core.models import RateModel
from .core.psychrometrics import PsychrometricEngine

METRIC_FRIENDLY_NAMES = {
    METRIC_TEMP_CLOSED: "Temp Profile Win Closed",
    METRIC_TEMP_OPEN: "Temp Profile Win Open",
    METRIC_TEMP_HEAT: "Temp Profile HVAC Heat",
    METRIC_TEMP_COOL: "Temp Profile HVAC Cool",
    METRIC_HUMIDITY_CLOSED: "Humidity Profile Win Closed",
    METRIC_HUMIDITY_OPEN: "Humidity Profile Win Open",
    METRIC_HUMIDITY_HEAT: "Humidity Profile HVAC Heat",
    METRIC_HUMIDITY_COOL: "Humidity Profile HVAC Cool",
}

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Sets up the sensor entities for Passable Smart Climate Engine."""
    coordinator: SmartClimateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    # 1. Global Simulation Data Sensor (matches Card 0 in Lovelace View 16)
    entities.append(PassableSimulationDataSensor(coordinator, entry))

    # 2. Main Advisory Recommendation Sensor
    entities.append(PassableAdvisorRecommendationSensor(coordinator, entry))

    # 3. Outdoor Enthalpy Sensor
    entities.append(PassableOutdoorEnthalpySensor(coordinator, entry))

    # 4. Generate the 16 Canonical Plot Data Sensors across configured zones
    zones = entry.data.get(CONF_ZONES, {})
    for zone_id in ["upstairs", "downstairs"]:
        zone_conf = zones.get(zone_id, {})
        zone_name = zone_conf.get("name", zone_id.capitalize())

        # Enthalpy Delta Sensor per Zone
        entities.append(PassableZoneEnthalpyDeltaSensor(coordinator, entry, zone_id, zone_name))

        # The 8 Plot Data Sensors per Zone (Total: 16)
        for metric_key in PLOT_METRIC_KEYS:
            entities.append(
                PassablePlotDataSensor(
                    coordinator=coordinator,
                    entry=entry,
                    zone_id=zone_id,
                    zone_name=zone_name,
                    metric_key=metric_key,
                )
            )

    async_add_entities(entities)

class PassablePlotDataSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Represents one of the 16 regression scatter plot data entities."""

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
        metric_key: str,
    ) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.zone_id = zone_id
        self.metric_key = metric_key
        series_id = f"{zone_id}_{metric_key}"
        self.series_id = series_id

        # Unique ID for HA Entity Registry
        self._attr_unique_id = f"passable_climate_{series_id}"

        # Hard-pin canonical entity_id for 100% dashboard backward compatibility
        self.entity_id = f"sensor.plot_data_{series_id}"

        friendly_title = METRIC_FRIENDLY_NAMES.get(metric_key, metric_key)
        self._attr_name = f"Plot Data: {zone_name} {friendly_title}"
        self._attr_icon = "mdi:chart-scatter-plot"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def _model(self) -> RateModel | None:
        return self.coordinator.models.get(self.series_id)

    @property
    def native_value(self) -> int:
        """Returns the number of inlier points in the dataset (matching legacy behavior)."""
        model = self._model
        if model and model.x_data:
            return len(model.x_data)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Exposes the exact attribute dictionary expected by Lovelace Plotly cards."""
        model = self._model
        if not model:
            return {
                "x_data": [],
                "y_data": [],
                "slope": None,
                "intercept": None,
                "equation": None,
                "fit_x": [],
                "fit_y": [],
                "uses_true_solcast": True,
                "c_delta_t": None,
                "c_wind_draft": None,
                "c_clouds": None,
                "c_irradiance": None,
                "c_solar_east": None,
                "c_solar_south": None,
                "mlr_intercept": None,
                "mlr_data_points": 0,
                "norm_c_delta_t": None,
                "norm_c_wind_draft": None,
                "norm_c_clouds": None,
                "norm_c_irradiance": None,
                "norm_c_solar_east": None,
                "norm_c_solar_south": None,
                "last_updated": None,
            }

        return {
            "x_data": model.x_data,
            "y_data": model.y_data,
            "slope": model.slope,
            "intercept": model.intercept,
            "equation": model.equation,
            "fit_x": model.fit_x,
            "fit_y": model.fit_y,
            "uses_true_solcast": model.uses_true_solcast,
            "c_delta_t": model.c_delta_t,
            "c_wind_draft": model.c_wind_draft,
            "c_clouds": model.c_clouds,
            "c_irradiance": model.c_irradiance,
            "c_solar_east": model.c_solar_east,
            "c_solar_south": model.c_solar_south,
            "mlr_intercept": model.mlr_intercept,
            "mlr_data_points": model.mlr_data_points,
            "norm_c_delta_t": model.norm_c_delta_t,
            "norm_c_wind_draft": model.norm_c_wind_draft,
            "norm_c_clouds": model.norm_c_clouds,
            "norm_c_irradiance": model.norm_c_irradiance,
            "norm_c_solar_east": model.norm_c_solar_east,
            "norm_c_solar_south": model.norm_c_solar_south,
            "last_updated": model.last_updated,
        }

class PassableSimulationDataSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Publishes the 10-minute microstep forward trajectory for Lovelace Card 0."""

    def __init__(self, coordinator: SmartClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_simulation_data"
        self.entity_id = "sensor.advisor_simulation_data"
        self._attr_name = "Advisor Simulation Data"
        self._attr_icon = "mdi:chart-line"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_hub")},
            name="Passable Smart Climate Hub",
            manufacturer="Passable Systems",
            model="Smart Climate Engine Hub",
        )

    @property
    def native_value(self) -> str:
        return datetime.datetime.now().isoformat()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "friendly_name": "Advisor Simulation Data",
            "icon": "mdi:chart-line",
            "data": json.dumps({"plan": self.coordinator.last_plan_history}),
            "is_bedtime": self.coordinator.is_bedtime,
            "is_evening": self.coordinator.is_evening,
            "is_bedtime_prediction": self.coordinator.is_bedtime or self.coordinator.is_evening,
            "phase": self.coordinator.current_phase,
            "seasonal_mode": self.coordinator.seasonal_mode,
        }

class PassableAdvisorRecommendationSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Publishes the unified window recommendation and Markdown action plan."""

    def __init__(self, coordinator: SmartClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_recommendation"
        self.entity_id = "sensor.passable_climate_window_recommendation"
        self._attr_name = "Window Advisory Recommendation"
        self._attr_icon = "mdi:window-open-variant"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_hub")},
            name="Passable Smart Climate Hub",
            manufacturer="Passable Systems",
            model="Smart Climate Engine Hub",
        )

    @property
    def native_value(self) -> str:
        return self.coordinator.last_recommendation

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "details": self.coordinator.last_details,
            "is_hazard_vetoed": self.coordinator.is_hazard_vetoed,
            "hazard_veto_reason": self.coordinator.hazard_veto_reason,
            "phase": self.coordinator.current_phase,
            "scenario": self.coordinator.current_scenario,
            "target_time": self.coordinator.current_target_time,
            "seasonal_mode": self.coordinator.seasonal_mode,
            "seasonal_details": self.coordinator.seasonal_details,
            "heat_prediction_alert": self.coordinator.heat_prediction_alert,
            "comfort_recovery_active": self.coordinator.comfort_recovery_active,
        }

class PassableOutdoorEnthalpySensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Calculates outdoor moist air specific enthalpy."""

    def __init__(self, coordinator: SmartClimateCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_outdoor_enthalpy"
        self.entity_id = "sensor.outdoor_specific_enthalpy"
        self._attr_name = "Outdoor Specific Enthalpy"
        self._attr_native_unit_of_measurement = "BTU/lb"
        self._attr_icon = "mdi:air-filter"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_hub")},
            name="Passable Smart Climate Hub",
            manufacturer="Passable Systems",
            model="Smart Climate Engine Hub",
        )

    @property
    def native_value(self) -> float | None:
        w_temp = self.coordinator._get_attr("weather.home", "temperature")
        w_rh = self.coordinator._get_attr("weather.home", "humidity")
        if w_temp is not None and w_rh is not None:
            return round(PsychrometricEngine.specific_enthalpy(float(w_temp), float(w_rh)), 2)
        return None

class PassableZoneEnthalpyDeltaSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Calculates specific enthalpy delta (Indoor - Outdoor) for free cooling evaluation."""

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{zone_id}_enthalpy_delta"
        self.entity_id = f"sensor.{zone_id}_enthalpy_delta"
        self._attr_name = f"{zone_name} Enthalpy Delta"
        self._attr_native_unit_of_measurement = "BTU/lb"
        self._attr_icon = "mdi:delta"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> float | None:
        zones = self.coordinator.entry_data.get(CONF_ZONES, {})
        z_conf = zones.get(self.zone_id, {})
        t_ent = z_conf.get("temperature_sensor")
        h_ent = z_conf.get("humidity_sensor")

        in_temp = self.coordinator._get_val(t_ent)
        in_rh = self.coordinator._get_val(h_ent)
        out_temp = self.coordinator._get_attr("weather.home", "temperature")
        out_rh = self.coordinator._get_attr("weather.home", "humidity")

        if in_temp is not None and in_rh is not None and out_temp is not None and out_rh is not None:
            h_in = PsychrometricEngine.specific_enthalpy(float(in_temp), float(in_rh))
            h_out = PsychrometricEngine.specific_enthalpy(float(out_temp), float(out_rh))
            return round(h_in - h_out, 2)
        return None
