"""Sensor platform for Passable Smart Climate Engine.
Registers all 16 canonical plot_data sensors and simulation trajectory entities.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ROOM_ENABLE_TELEMETRY,
    CONF_ROOM_NAME,
    CONF_ROOM_ZONE,
    CONF_WEATHER_ENTITY,
    CONF_ZONES,
    DEFAULT_WEATHER_ENTITY,
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
    ZONE_MLR_RATE_SPECS,
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


def _claim_legacy_entity_id(hass: HomeAssistant, desired_entity_id: str) -> None:
    """Release orphaned YAML registry entries so passable_climate claims the exact entity_id."""
    try:
        ent_reg = er.async_get(hass)
        existing = ent_reg.async_get(desired_entity_id)
        if existing is not None and existing.platform != DOMAIN:
            st = hass.states.get(desired_entity_id)
            if st is None or st.state in ("unavailable", "unknown"):
                ent_reg.async_remove(desired_entity_id)
    except Exception:
        pass


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

    # 3. Outdoor Enthalpy, Absolute Humidity & Total Open Windows Sensors
    entities.append(PassableOutdoorEnthalpySensor(coordinator, entry))
    entities.append(PassableAbsoluteHumiditySensor(coordinator, entry, "outside", "Outside"))
    entities.append(PassableOpenWindowsCountSensor(coordinator, entry, "total", "Total"))

    # 4. Generate Per-Zone Plot Data, MLR Rate, Filter, Runtime & Diagnostic Sensors
    zones = entry.data.get(CONF_ZONES, {})
    for zone_id in ["upstairs", "downstairs"]:
        zone_conf = zones.get(zone_id, {})
        zone_name = zone_conf.get("name", zone_id.capitalize())

        # Enthalpy Delta, Absolute Humidity, Open Windows & Fault-Tolerant Rate per Zone
        entities.append(PassableZoneEnthalpyDeltaSensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableAbsoluteHumiditySensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableOpenWindowsCountSensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableZoneFaultTolerantRateSensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableHvacLastActivitySensor(coordinator, entry, zone_id, zone_name))

        # Option 2B: Native Filter Life & Daily Runtime Sensors per Zone
        entities.append(PassableFilterLifeRemainingSensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableFilterRuntimeSensor(coordinator, entry, zone_id, zone_name))
        for run_mode in ("heating", "cooling", "fan"):
            entities.append(
                PassableDailyRuntimeSensor(coordinator, entry, zone_id, zone_name, run_mode)
            )

        # Physics-Based Time-to-Target & Health Diagnostics per Zone
        entities.append(PassableTimeToTempSensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableHvacHealthStatusSensor(coordinator, entry, zone_id, zone_name))
        entities.append(PassableHvacCyclesPerHourSensor(coordinator, entry, zone_id, zone_name))

        # The 8 Canonical MLR Telemetry Rate Sensors per Zone (Total: 16)
        for suffix, friendly_suffix, unit, icon, _mode_type in ZONE_MLR_RATE_SPECS:
            entities.append(
                PassableZoneMLRRateSensor(
                    coordinator=coordinator,
                    entry=entry,
                    zone_id=zone_id,
                    zone_name=zone_name,
                    suffix=suffix,
                    friendly_suffix=friendly_suffix,
                    unit=unit,
                    icon=icon,
                )
            )

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

    # 5. Per-Room Passive Heat Loss/Gain Telemetry Sensors
    for room_slug, room_conf in coordinator.get_configured_rooms().items():
        if not isinstance(room_conf, dict) or not room_conf.get(CONF_ROOM_ENABLE_TELEMETRY, True):
            continue
        room_name = str(room_conf.get(CONF_ROOM_NAME, room_slug.replace("_", " ").title()))
        room_zone = str(room_conf.get(CONF_ROOM_ZONE, "downstairs"))
        for win_state in ("closed", "open"):
            entities.append(
                PassableRoomMLRRateSensor(
                    coordinator=coordinator,
                    entry=entry,
                    room_slug=room_slug,
                    room_name=room_name,
                    zone_id=room_zone,
                    window_state=win_state,
                )
            )

    for ent in entities:
        if getattr(ent, "entity_id", None):
            _claim_legacy_entity_id(hass, ent.entity_id)

    async_add_entities(entities)

class PassablePlotDataSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Represents one of the 16 regression scatter plot data entities."""

    _unrecorded_attributes = frozenset({"x_data", "y_data", "fit_x", "fit_y"})

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
                "c_solar_west": None,
                "mlr_intercept": None,
                "mlr_data_points": 0,
                "norm_c_delta_t": None,
                "norm_c_wind_draft": None,
                "norm_c_clouds": None,
                "norm_c_irradiance": None,
                "norm_c_solar_east": None,
                "norm_c_solar_south": None,
                "norm_c_solar_west": None,
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
            "c_solar_west": model.c_solar_west,
            "mlr_intercept": model.mlr_intercept,
            "mlr_data_points": model.mlr_data_points,
            "norm_c_delta_t": model.norm_c_delta_t,
            "norm_c_wind_draft": model.norm_c_wind_draft,
            "norm_c_clouds": model.norm_c_clouds,
            "norm_c_irradiance": model.norm_c_irradiance,
            "norm_c_solar_east": model.norm_c_solar_east,
            "norm_c_solar_south": model.norm_c_solar_south,
            "norm_c_solar_west": model.norm_c_solar_west,
            "last_updated": model.last_updated,
        }

class PassableSimulationDataSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Publishes the 10-minute microstep forward trajectory for Lovelace Card 0."""

    _unrecorded_attributes = frozenset({"data"})
    _attr_device_class = SensorDeviceClass.TIMESTAMP

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
    def native_value(self) -> datetime.datetime | None:
        return self.coordinator.last_update_time

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
        weather_ent = self.coordinator.options.get(
            CONF_WEATHER_ENTITY,
            self.coordinator.entry_data.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY),
        )
        w_temp = self.coordinator._get_attr(weather_ent, "temperature")
        w_rh = self.coordinator._get_attr(weather_ent, "humidity")
        if w_temp is not None and w_rh is not None:
            try:
                t_unit, _ = self.coordinator._weather_units(weather_ent)
                temp_f = self.coordinator._to_f(float(w_temp), t_unit)
                rh_val = float(w_rh)
                return round(
                    PsychrometricEngine.specific_enthalpy(
                        temp_f, rh_val, pressure_hpa=self.coordinator.pressure_hpa
                    ),
                    2,
                )
            except (TypeError, ValueError):
                return None
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
        weather_ent = self.coordinator.options.get(
            CONF_WEATHER_ENTITY,
            self.coordinator.entry_data.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY),
        )

        in_temp_raw = self.coordinator._read_float(t_ent, lo=-10.0, hi=110.0)
        in_rh = self.coordinator._read_float(h_ent, lo=1.0, hi=100.0)
        out_temp_raw = self.coordinator._get_attr(weather_ent, "temperature")
        out_rh = self.coordinator._get_attr(weather_ent, "humidity")

        if in_temp_raw is not None and in_rh is not None and out_temp_raw is not None and out_rh is not None:
            try:
                t_unit, _ = self.coordinator._weather_units(weather_ent)
                out_temp = self.coordinator._to_f(float(out_temp_raw), t_unit)
                out_rh_val = float(out_rh)
                in_unit = self.coordinator._get_attr(t_ent, "unit_of_measurement", "°F")
                in_temp = self.coordinator._to_f(float(in_temp_raw), in_unit)
                h_in = PsychrometricEngine.specific_enthalpy(
                    in_temp, float(in_rh), pressure_hpa=self.coordinator.pressure_hpa
                )
                h_out = PsychrometricEngine.specific_enthalpy(
                    out_temp, out_rh_val, pressure_hpa=self.coordinator.pressure_hpa
                )
                return round(h_in - h_out, 2)
            except (TypeError, ValueError):
                return None
        return None


class PassableZoneMLRRateSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Represents one of the 16 canonical zone MLR telemetry rate sensors for InfluxDB ingestion."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
        suffix: str,
        friendly_suffix: str,
        unit: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self.full_key = f"{zone_id}_{suffix}"
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_mlr_{self.full_key}"
        self.entity_id = f"sensor.{self.full_key}"
        self._attr_name = f"{zone_name} {friendly_suffix}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.zone_mlr_rates.get(self.full_key) is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.zone_mlr_rates.get(self.full_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.zone_mlr_attrs.get(self.full_key, {})


class PassableRoomMLRRateSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Represents a per-room passive heat loss/gain telemetry sensor (windows closed or open)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°F/h"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        room_slug: str,
        room_name: str,
        zone_id: str,
        window_state: str,
    ) -> None:
        super().__init__(coordinator)
        self.room_slug = room_slug
        self.window_state = window_state
        self.full_key = f"{room_slug}_heat_loss_gain_windows_{window_state}"
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_room_{self.full_key}"
        self.entity_id = f"sensor.{self.full_key}"
        state_title = "Windows Closed" if window_state == "closed" else "Windows Open"
        self._attr_name = f"{room_name} Heat Loss/Gain ({state_title})"
        self._attr_icon = (
            "mdi:home-thermometer-outline" if window_state == "closed" else "mdi:window-open-variant"
        )

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_id.capitalize()}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.room_mlr_rates.get(self.full_key) is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.room_mlr_rates.get(self.full_key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.room_mlr_attrs.get(self.full_key, {})


class PassableAbsoluteHumiditySensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Magnus-formula Absolute Humidity sensor in g/m³."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "g/m³"
    _attr_icon = "mdi:water"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        location_key: str,
        location_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.location_key = location_key
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{location_key}_absolute_humidity"
        self.entity_id = f"sensor.{location_key}_absolute_humidity"
        self._attr_name = f"{location_name} Absolute Humidity"

        dev_id = f"{entry.entry_id}_hub" if location_key == "outside" else f"{entry.entry_id}_{location_key}"
        dev_name = "Passable Smart Climate Hub" if location_key == "outside" else f"Zone: {location_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dev_id)},
            name=dev_name,
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> float | None:
        return self.coordinator.abs_humidity.get(self.location_key)


class PassableOpenWindowsCountSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Counts open window binary sensors for Upstairs, Downstairs, or Total House."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "windows"
    _attr_icon = "mdi:window-open"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        scope_key: str,
        scope_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.scope_key = scope_key
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{scope_key}_open_windows_count"
        self.entity_id = (
            "sensor.open_windows_count"
            if scope_key == "total"
            else f"sensor.{scope_key}_open_windows_count"
        )
        self._attr_name = (
            "Open Windows Count" if scope_key == "total" else f"{scope_name} Open Windows Count"
        )

        dev_id = f"{entry.entry_id}_hub" if scope_key == "total" else f"{entry.entry_id}_{scope_key}"
        dev_name = "Passable Smart Climate Hub" if scope_key == "total" else f"Zone: {scope_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dev_id)},
            name=dev_name,
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> int:
        return int(self.coordinator.open_windows_counts.get(self.scope_key, 0))


class PassableZoneFaultTolerantRateSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Fault-tolerant 10-minute rolling temperature derivative across active zone rooms."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "°F/h"
    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{zone_id}_fault_tolerant_temp_rate"
        self.entity_id = f"sensor.{zone_id}_fault_tolerant_temp_rate"
        self._attr_name = f"{zone_name} Fault Tolerant Temp Rate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> float:
        return float(self.coordinator.zone_fault_tolerant_rates.get(self.zone_id, 0.0))


class PassableHvacLastActivitySensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Tracks the last HVAC active timestamp and mode for the 90-minute coasting gate."""

    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_hvac_last_activity_{zone_id}"
        self.entity_id = f"sensor.hvac_last_activity_{zone_id}"
        self._attr_name = f"HVAC Last Activity {zone_name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> str | None:
        return self.coordinator.telemetry_state.get(self.zone_id, {}).get("last_active_ts")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zt = self.coordinator.telemetry_state.get(self.zone_id, {})
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return {
            "last_hvac_mode": zt.get("last_hvac_mode", "idle"),
            "is_coasting": diag.get("is_coasting", False),
        }


class PassableFilterLifeRemainingSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Tracks remaining HVAC filter hours and last replacement date for passable-hvac-card."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "h"
    _attr_icon = "mdi:air-filter"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_filter_life_remaining_{zone_id}"
        self.entity_id = f"sensor.hvac_filter_life_remaining_{zone_id}"
        self._attr_name = f"HVAC Filter Life Remaining ({zone_name})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> float:
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return float(diag.get("filter_remaining_hours", 300.0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zt = self.coordinator.telemetry_state.get(self.zone_id, {})
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        last_rep = zt.get("filter_last_replaced", "Unknown")
        return {
            f"last_changed_{self.zone_id}": last_rep,
            "last_replaced": last_rep,
            "runtime_hours": round(float(zt.get("filter_runtime_hours", 0.0)), 2),
            "capacity_hours": diag.get("filter_capacity_hours", 300.0),
        }


class PassableFilterRuntimeSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Tracks cumulative HVAC filter runtime hours since last filter reset."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "h"
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{zone_id}_filter_runtime"
        self.entity_id = f"sensor.hvac_{zone_id}_filter_runtime"
        self._attr_name = f"HVAC {zone_name} Filter Runtime"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> float:
        zt = self.coordinator.telemetry_state.get(self.zone_id, {})
        return round(float(zt.get("filter_runtime_hours", 0.0)), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zt = self.coordinator.telemetry_state.get(self.zone_id, {})
        return {
            "last_replaced": zt.get("filter_last_replaced", "Unknown"),
        }


class PassableDailyRuntimeSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Tracks daily HVAC heating, cooling, or fan runtime in hours for passable-hvac-card."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "h"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
        run_mode: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self.run_mode = run_mode
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{zone_id}_{run_mode}_today"
        self.entity_id = f"sensor.hvac_{zone_id}_{run_mode}_today"
        self._attr_name = f"HVAC {zone_name} {run_mode.capitalize()} Today"
        self._attr_icon = (
            "mdi:fire"
            if run_mode == "heating"
            else ("mdi:snowflake" if run_mode == "cooling" else "mdi:fan")
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> float:
        zt = self.coordinator.telemetry_state.get(self.zone_id, {})
        return round(float(zt.get(f"{self.run_mode}_today", 0.0)), 2)


class PassableTimeToTempSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Physics-based estimated minutes to reach active HVAC setpoint."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:clock-fast"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_time_to_temp_{zone_id}"
        self.entity_id = f"sensor.hvac_time_to_temp_{zone_id}"
        self._attr_name = f"HVAC Time to Target ({zone_name})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> int:
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return int(diag.get("time_to_temp", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return diag.get("time_to_temp_attrs", {})


class PassableHvacHealthStatusSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Evaluates HVAC mechanical health (Optimal, Short Cycling, Thermal Stall, Filter Overdue)."""

    _attr_icon = "mdi:heart-pulse"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_health_status_{zone_id}"
        self.entity_id = f"sensor.hvac_health_status_{zone_id}"
        self._attr_name = f"HVAC Health Status ({zone_name})"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> str:
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return str(diag.get("health_status", "Optimal"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return diag.get("health_attrs", {})


class PassableHvacCyclesPerHourSensor(CoordinatorEntity[SmartClimateCoordinator], SensorEntity):
    """Tracks rolling 60-minute compressor start count for short-cycle protection."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "cycles/h"
    _attr_icon = "mdi:sync-alert"

    def __init__(
        self,
        coordinator: SmartClimateCoordinator,
        entry: ConfigEntry,
        zone_id: str,
        zone_name: str,
    ) -> None:
        super().__init__(coordinator)
        self.zone_id = zone_id
        self._attr_unique_id = f"passable_climate_{entry.entry_id}_{zone_id}_cycles_per_hour"
        self.entity_id = f"sensor.hvac_{zone_id}_cycles_per_hour"
        self._attr_name = f"HVAC {zone_name} Cycles Per Hour"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{zone_id}")},
            name=f"Zone: {zone_name}",
            manufacturer="Passable Systems",
            model="Thermal Zone Controller",
        )

    @property
    def native_value(self) -> int:
        diag = self.coordinator.zone_diagnostics.get(self.zone_id, {})
        return int(diag.get("cycles_per_hour", 0))

