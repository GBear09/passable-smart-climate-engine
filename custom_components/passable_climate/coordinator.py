"""DataUpdateCoordinator for Passable Smart Climate Engine.
Coordinates weather ingestion, multi-zone micro-simulations, and circulation control.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util

from .const import (
    CONF_AQI_ENTITY,
    CONF_AQI_THRESHOLD,
    CONF_BEDTIME_START,
    CONF_CIRC_DELTA_THRESHOLD,
    CONF_CIRC_LOCKOUT_HOURS,
    CONF_CIRC_MAX_MINUTES,
    CONF_CIRC_MIN_MINUTES,
    CONF_CIRC_STALL_MARGIN,
    CONF_CIRCULATION_ACTIVE_BOOLEAN,
    CONF_CIRCULATION_ECO_BOOLEAN,
    CONF_CIRCULATION_ENABLED,
    CONF_CIRCULATION_SOURCE_TEMP,
    CONF_CLIMATE_ENTITY,
    CONF_CLOSE_TEMP_MARGIN,
    CONF_COMFORT_PROFILE,
    CONF_COMFORT_RECOVERY_BOOLEAN,
    CONF_FORECAST_LOOKAHEAD_MINUTES,
    CONF_HIGH_WIND_GUST,
    CONF_HIGH_WIND_SPEED,
    CONF_HOME_MODE_ENTITY,
    CONF_HUMIDITY_SENSOR,
    CONF_HVAC_TRANSITION_OFFSET,
    CONF_INFLUX_LOOKBACK_DAYS,
    CONF_MAX_DEW_POINT,
    CONF_MAX_PRECIPITATION_PROBABILITY,
    CONF_MIN_ENTHALPY_DELTA,
    CONF_MORNING_START,
    CONF_OPEN_DWELL_MINUTES,
    CONF_OPEN_TEMP_MARGIN,
    CONF_OPEN_WINDOWS_SENSOR,
    CONF_PARENT_BEDTIME,
    CONF_PREDICT_HEAT_BOOLEAN,
    CONF_PREDICTION_MESSAGE_TEXT,
    CONF_PRESENCE_ENTITY,
    CONF_RECOMMENDATION_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    CONF_SOLCAST_POWER_ENTITY,
    CONF_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_ECO_BOOLEAN,
    CONF_ZONE_NAME,
    CONF_ZONES,
    DEFAULT_AQI_THRESHOLD,
    DEFAULT_BEDTIME_START,
    DEFAULT_CIRC_DELTA_THRESHOLD,
    DEFAULT_CIRC_LOCKOUT_HOURS,
    DEFAULT_CIRC_MAX_MINUTES,
    DEFAULT_CIRC_MIN_MINUTES,
    DEFAULT_CIRC_STALL_MARGIN,
    DEFAULT_CLOSE_TEMP_MARGIN,
    DEFAULT_COMFORT_PROFILE_ENTITY,
    DEFAULT_COMFORT_RECOVERY_BOOLEAN,
    DEFAULT_FORECAST_LOOKAHEAD_MINUTES,
    DEFAULT_HIGH_WIND_GUST,
    DEFAULT_HIGH_WIND_SPEED,
    DEFAULT_HOME_MODE_ENTITY,
    DEFAULT_HVAC_TRANSITION_OFFSET,
    DEFAULT_HVAC_TRANSITION_OFFSET_ENTITY,
    DEFAULT_MAX_DEW_POINT,
    DEFAULT_MAX_PRECIPITATION_PROBABILITY,
    DEFAULT_MIN_ENTHALPY_DELTA,
    DEFAULT_MORNING_START,
    DEFAULT_OPEN_DWELL_MINUTES,
    DEFAULT_OPEN_TEMP_MARGIN,
    DEFAULT_PARENT_BEDTIME,
    DEFAULT_PREDICT_HEAT_BOOLEAN,
    DEFAULT_PREDICTION_MESSAGE_TEXT,
    DEFAULT_PRESENCE_ENTITY,
    DEFAULT_RECOMMENDATION_ENTITY,
    DEFAULT_SOLAR_POWER_ENTITY,
    DEFAULT_SOLCAST_POWER_ENTITY,
    DEFAULT_WEATHER_ENTITY,
    DOMAIN,
)
from .core.models import RateModel, SimulationAction, ZonePlanResult
from .core.psychrometrics import PsychrometricEngine
from .core.simulator import run_static_simulation
from .engines.advisor import (
    BAD_WEATHER_CONDITIONS,
    determine_seasonal_mode,
    evaluate_environmental_vetoes,
    evaluate_zone_plan,
    get_comfort_bounds,
)
from .engines.circulation import CirculationManager
from .engines.phases import PHASE_DAYTIME, determine_temporal_phase, resolve_zone_setpoints
from .training.influx_client import InfluxClient
from .training.trainer import train_all_models_sync

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY_MODELS = f"{DOMAIN}.models"
STORAGE_KEY_LEARNING = f"{DOMAIN}.learning_data"


class SmartClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central coordinator for Passable Smart Climate Engine."""

    def __init__(self, hass: HomeAssistant, entry_data: dict[str, Any], entry_options: dict[str, Any]) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Passable Smart Climate Engine",
            update_interval=datetime.timedelta(minutes=15),
        )
        self.entry_data = entry_data
        self.options = entry_options

        self.influx_client = InfluxClient(
            host=entry_data.get("influx_host", "localhost"),
            port=int(entry_data.get("influx_port", 8086)),
            version=str(entry_data.get("influx_version", "1")),
            database=entry_data.get("influx_database", "homeassistant"),
            username=entry_data.get("influx_username"),
            password=entry_data.get("influx_password"),
            token=entry_data.get("influx_token"),
            org=entry_data.get("influx_org"),
            bucket=entry_data.get("influx_bucket"),
        )

        self.models: dict[str, RateModel] = {}
        self.circulation_manager = CirculationManager()
        self._models_store = Store(hass, STORAGE_VERSION, STORAGE_KEY_MODELS)
        self._learning_store = Store(hass, STORAGE_VERSION, STORAGE_KEY_LEARNING)

        self.last_recommendation: str = "Close Windows"
        self.last_details: str = "Initializing Passable Smart Climate Engine..."
        self.last_plan_history: list[dict[str, float]] = []
        self.zone_results: dict[str, ZonePlanResult] = {}
        self.is_hazard_vetoed: bool = False
        self.hazard_veto_reason: str = ""

        # Advanced Phase, Scenario & Latch State
        self.current_phase: str = PHASE_DAYTIME
        self.is_bedtime: bool = False
        self.is_evening: bool = False
        self.current_scenario: str = "standard"
        self.current_target_time: str | None = None
        self.seasonal_mode: str = "normal"
        self.seasonal_details: str | None = None
        self.heat_prediction_alert: bool = False
        self.comfort_recovery_active: bool = False
        self._last_bad_weather_time: str | None = None

    async def async_initialize(self) -> None:
        """Loads cached models and circulation learning data from Home Assistant storage."""
        try:
            cached_models = await self._models_store.async_load()
            if cached_models and isinstance(cached_models, dict):
                for k, d in cached_models.items():
                    if isinstance(d, dict):
                        self.models[k] = RateModel(**d)
                _LOGGER.info("Passable Smart Climate: Loaded %d cached models from storage.", len(self.models))
        except Exception as err:
            _LOGGER.warning("Could not load cached models: %s", err)

        try:
            cached_learning = await self._learning_store.async_load()
            if cached_learning and isinstance(cached_learning, dict):
                self.circulation_manager.load_learning_data(cached_learning)
                _LOGGER.info("Passable Smart Climate: Loaded circulation learning records from storage.")
        except Exception as err:
            _LOGGER.warning("Could not load circulation learning records: %s", err)

    def async_setup_listeners(self) -> None:
        """Sets up reactive state listeners on window contact sensors and comfort profile."""
        from homeassistant.helpers.event import async_track_state_change_event
        entities_to_track: list[str] = []
        zones_config = self.entry_data.get(CONF_ZONES, {})
        for z_conf in zones_config.values():
            win_ent = z_conf.get(CONF_OPEN_WINDOWS_SENSOR)
            if win_ent and win_ent not in entities_to_track:
                entities_to_track.append(win_ent)

        cp_ent = self.options.get(CONF_COMFORT_PROFILE, self.entry_data.get(CONF_COMFORT_PROFILE, DEFAULT_COMFORT_PROFILE_ENTITY))
        if cp_ent and cp_ent not in entities_to_track:
            entities_to_track.append(cp_ent)

        # Track home state and profile helpers for instant reactive phase updates
        for phase_helper in ["input_select.home_state", "input_text.hvac_active_profile"]:
            if phase_helper not in entities_to_track:
                entities_to_track.append(phase_helper)

        if entities_to_track:
            async def _handle_tracked_state_change(event: Any) -> None:
                _LOGGER.debug("Reactive trigger from tracked entity %s; refreshing climate plan.", event.data.get("entity_id"))
                await self.async_request_refresh()

            self._unsub_track = async_track_state_change_event(
                self.hass, entities_to_track, _handle_tracked_state_change
            )

        # Reactive listener for manual circulation toggle switches
        circ_bools: list[str] = []
        for z_key, z_conf in zones_config.items():
            if z_conf.get(CONF_CIRCULATION_ENABLED, False):
                act_ent = z_conf.get(CONF_CIRCULATION_ACTIVE_BOOLEAN)
                if act_ent and act_ent not in circ_bools:
                    circ_bools.append(act_ent)

        if circ_bools:
            async def _handle_circ_toggle(event: Any) -> None:
                new_state = event.data.get("new_state")
                old_state = event.data.get("old_state")
                if not new_state or not old_state or new_state.state == old_state.state:
                    return
                ent_id = event.data.get("entity_id")
                for z_k, z_c in zones_config.items():
                    if z_c.get(CONF_CIRCULATION_ACTIVE_BOOLEAN) == ent_id:
                        climate_e = z_c.get(CONF_CLIMATE_ENTITY)
                        eco_e = z_c.get(CONF_CIRCULATION_ECO_BOOLEAN)
                        temp_e = z_c.get(CONF_TEMP_SENSOR)
                        now_u = dt_util.utcnow()
                        if new_state.state == "on":
                            _LOGGER.info("Passable Smart Climate: Manual toggle ON for %s circulation.", z_k)
                            if climate_e:
                                await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_e, "fan_mode": "on"})
                            if eco_e:
                                await self.hass.services.async_call("input_boolean", "turn_on", {"entity_id": eco_e})
                            in_t = float(self._get_val(temp_e, 70.0) or 70.0)
                            if not self.circulation_manager.state_memory[z_k].get("start_time"):
                                self.circulation_manager.state_memory[z_k]["start_time"] = now_u
                                self.circulation_manager.state_memory[z_k]["start_temp"] = in_t
                                await self._learning_store.async_save(self.circulation_manager.export_learning_data())
                        elif new_state.state == "off":
                            _LOGGER.info("Passable Smart Climate: Manual toggle OFF for %s circulation.", z_k)
                            if climate_e:
                                await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_e, "fan_mode": "auto"})
                            if eco_e:
                                await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": eco_e})
                            self.circulation_manager.state_memory[z_k]["start_time"] = None
                            self.circulation_manager.state_memory[z_k]["start_temp"] = None
                            await self._learning_store.async_save(self.circulation_manager.export_learning_data())

            self._unsub_circ = async_track_state_change_event(
                self.hass, circ_bools, _handle_circ_toggle
            )

    def async_unload(self) -> None:
        """Unsubscribe all listeners."""
        if hasattr(self, "_unsub_track") and self._unsub_track:
            self._unsub_track()
            self._unsub_track = None
        if hasattr(self, "_unsub_circ") and self._unsub_circ:
            self._unsub_circ()
            self._unsub_circ = None

    async def async_retrain_models(self) -> None:
        """Executes full historical regression training in a background executor thread."""
        lookback = int(self.options.get(CONF_INFLUX_LOOKBACK_DAYS, self.entry_data.get(CONF_INFLUX_LOOKBACK_DAYS, 365)))
        try:
            updated_models = await self.hass.async_add_executor_job(
                train_all_models_sync,
                self.influx_client,
                lookback,
            )
            if updated_models:
                self.models.update(updated_models)
                serialized = {k: m.__dict__ for k, m in self.models.items()}
                await self._models_store.async_save(serialized)
                _LOGGER.info("Passable Smart Climate: Retrained and saved %d models.", len(self.models))
                self.async_update_listeners()
        except Exception as err:
            _LOGGER.error("Model retraining failed: %s", err)

    @staticmethod
    def _parse_time(val: Any, default_hour: int, default_minute: int = 0) -> datetime.time:
        if val and ":" in str(val):
            try:
                parts = str(val).strip().split(":")
                return datetime.time(int(parts[0]), int(parts[1]))
            except Exception:
                pass
        return datetime.time(default_hour, default_minute)

    def _get_val(self, entity_id: str | None, default: Any = None) -> Any:
        if not entity_id:
            return default
        st = self.hass.states.get(entity_id)
        if st is None or st.state in ["unknown", "unavailable", "None", ""]:
            return default
        return st.state

    def _get_attr(self, entity_id: str | None, attr: str, default: Any = None) -> Any:
        if not entity_id:
            return default
        st = self.hass.states.get(entity_id)
        if st is None:
            return default
        return st.attributes.get(attr, default)

    async def _async_update_data(self) -> dict[str, Any]:
        """Runs the complete environmental arbitration, forward simulation, and circulation manager."""
        now = dt_util.now()
        now_utc = dt_util.as_utc(now)

        weather_ent = self.options.get(CONF_WEATHER_ENTITY, self.entry_data.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY))
        home_mode_ent = self.options.get(CONF_HOME_MODE_ENTITY, self.entry_data.get(CONF_HOME_MODE_ENTITY, DEFAULT_HOME_MODE_ENTITY))
        presence_ent = self.options.get(CONF_PRESENCE_ENTITY, self.entry_data.get(CONF_PRESENCE_ENTITY, DEFAULT_PRESENCE_ENTITY))
        comfort_profile_ent = self.options.get(CONF_COMFORT_PROFILE, self.entry_data.get(CONF_COMFORT_PROFILE, DEFAULT_COMFORT_PROFILE_ENTITY))

        # 1. Weather Forecast Extraction
        forecast_list: list[dict[str, Any]] = []
        try:
            resp = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_ent, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
            if resp and weather_ent in resp:
                forecast_list = resp[weather_ent].get("forecast", [])
        except Exception as err:
            _LOGGER.debug("Could not fetch hourly forecast via get_forecasts: %s", err)

        if not forecast_list:
            forecast_attr = self._get_attr(weather_ent, "forecast", [])
            forecast_list = list(forecast_attr) if isinstance(forecast_attr, list) else []

        w_state = self._get_val(weather_ent)
        w_temp = float(self._get_attr(weather_ent, "temperature", 70.0) or 70.0)
        w_wind = float(self._get_attr(weather_ent, "wind_speed", 0.0) or 0.0)
        w_gust = float(self._get_attr(weather_ent, "wind_gust", 0.0) or 0.0)

        # Track wet ground / storm quiet period
        if w_state and str(w_state).lower() in BAD_WEATHER_CONDITIONS:
            self._last_bad_weather_time = now_utc.isoformat()

        # Solar Coordinates
        lat = float(self.hass.config.latitude)
        lon = float(self.hass.config.longitude)

        # Environmental & Occupancy Veto Check
        home_mode = self._get_val(home_mode_ent, "Home")
        someone_is_home = (self._get_val(presence_ent, "on") == "on")
        precip_prob = float(forecast_list[0].get("precipitation_probability", 0.0)) if forecast_list else 0.0
        aqi_val = None
        aqi_ent = self.options.get(CONF_AQI_ENTITY, self.entry_data.get(CONF_AQI_ENTITY))
        if aqi_ent:
            try:
                aqi_val = float(self._get_val(aqi_ent, 0.0))
            except (ValueError, TypeError):
                aqi_val = None

        is_vetoed, veto_reason = evaluate_environmental_vetoes(
            weather_condition=w_state,
            precipitation_probability=precip_prob,
            wind_speed=w_wind,
            wind_gust=w_gust,
            aqi_value=aqi_val,
            home_mode=home_mode,
            someone_is_home=someone_is_home,
            high_wind_speed=float(self.options.get(CONF_HIGH_WIND_SPEED, DEFAULT_HIGH_WIND_SPEED)),
            high_wind_gust=float(self.options.get(CONF_HIGH_WIND_GUST, DEFAULT_HIGH_WIND_GUST)),
            max_precip_prob=float(self.options.get(CONF_MAX_PRECIPITATION_PROBABILITY, DEFAULT_MAX_PRECIPITATION_PROBABILITY)),
            aqi_threshold=float(self.options.get(CONF_AQI_THRESHOLD, DEFAULT_AQI_THRESHOLD)),
        )
        self.is_hazard_vetoed = is_vetoed
        self.hazard_veto_reason = veto_reason

        # Solcast PV Forecast & Real-Time Solar Cloud Factor Scaling
        solcast_forecast: list[dict[str, Any]] = []
        try:
            today_attrs = self.hass.states.get("sensor.solcast_pv_forecast_forecast_today")
            tomorrow_attrs = self.hass.states.get("sensor.solcast_pv_forecast_forecast_tomorrow")
            if today_attrs and "detailedForecast" in today_attrs.attributes:
                for item in today_attrs.attributes["detailedForecast"]:
                    dt_p = datetime.datetime.fromisoformat(item["period_start"])
                    solcast_forecast.append({"dt": dt_p, "power": float(item["pv_estimate"]) * 1000.0})
            if tomorrow_attrs and "detailedForecast" in tomorrow_attrs.attributes:
                for item in tomorrow_attrs.attributes["detailedForecast"]:
                    dt_p = datetime.datetime.fromisoformat(item["period_start"])
                    solcast_forecast.append({"dt": dt_p, "power": float(item["pv_estimate"]) * 1000.0})
        except Exception:
            pass

        solar_trans_ratio = 1.0
        try:
            solar_power_ent = self.options.get(CONF_SOLAR_POWER_ENTITY, self.entry_data.get(CONF_SOLAR_POWER_ENTITY, DEFAULT_SOLAR_POWER_ENTITY))
            solcast_power_ent = self.options.get(CONF_SOLCAST_POWER_ENTITY, self.entry_data.get(CONF_SOLCAST_POWER_ENTITY, DEFAULT_SOLCAST_POWER_ENTITY))
            actual_solar_kw = float(self._get_val(solar_power_ent, 0.0) or 0.0)
            actual_solar_w = actual_solar_kw * 1000.0
            solcast_pred_w = float(self._get_val(solcast_power_ent, 0.0) or 0.0)
            if solcast_pred_w > 100.0:
                solar_trans_ratio = min(1.2, max(0.0, actual_solar_w / solcast_pred_w))
        except Exception:
            solar_trans_ratio = 1.0

        if solcast_forecast and solar_trans_ratio != 1.0:
            for item in solcast_forecast:
                item["power"] = item["power"] * solar_trans_ratio

        # Comfort Profile Polynomial Bounds
        comfort_profile: dict[str, Any] | None = None
        if comfort_profile_ent:
            cp_st = self.hass.states.get(comfort_profile_ent)
            if cp_st:
                comfort_profile = {
                    "upper_profile": cp_st.attributes.get("upper_profile"),
                    "lower_profile": cp_st.attributes.get("lower_profile"),
                }

        # 2. Temporal Phasing Detection
        home_state_val = self._get_val("input_select.home_state")
        hvac_profile_val = self._get_val("input_text.hvac_active_profile")

        bedtime_start_ent = self.options.get(CONF_BEDTIME_START, self.entry_data.get(CONF_BEDTIME_START, DEFAULT_BEDTIME_START))
        parent_bedtime_ent = self.options.get(CONF_PARENT_BEDTIME, self.entry_data.get(CONF_PARENT_BEDTIME, DEFAULT_PARENT_BEDTIME))
        morning_start_ent = self.options.get(CONF_MORNING_START, self.entry_data.get(CONF_MORNING_START, DEFAULT_MORNING_START))

        bedtime_start_time = self._parse_time(self._get_val(bedtime_start_ent), 18, 0)
        parent_bedtime_time = self._parse_time(self._get_val(parent_bedtime_ent), 20, 30)
        morning_start_time = self._parse_time(self._get_val(morning_start_ent), 4, 30)

        phase_name, is_bedtime, is_evening = determine_temporal_phase(
            home_state=home_state_val,
            hvac_profile=hvac_profile_val,
            now=now,
            bedtime_start=bedtime_start_time,
            parent_bedtime=parent_bedtime_time,
            morning_start=morning_start_time,
        )
        self.current_phase = phase_name
        self.is_bedtime = is_bedtime
        self.is_evening = is_evening

        # 3. Zone Setpoint Resolution via Dual-Source Hierarchy
        zones_config = self.entry_data.get(CONF_ZONES, {})
        zone_setpoints: dict[str, tuple[float, float]] = {}
        zone_climates: dict[str, str] = {}
        overall_hvac_mode = "off"

        protect_cool = float(self._get_val("input_number.hvac_preset_protect_cool", 78.0) or 78.0)
        protect_heat = float(self._get_val("input_number.hvac_preset_protect_heat", 65.0) or 65.0)

        for z_key, z_conf in zones_config.items():
            climate_ent = z_conf.get(CONF_CLIMATE_ENTITY)
            c_state = self._get_val(climate_ent, "off")
            c_attrs = self.hass.states.get(climate_ent).attributes if (climate_ent and self.hass.states.get(climate_ent)) else {}
            zone_climates[z_key] = c_state
            if c_state in ["cool", "heat"]:
                overall_hvac_mode = c_state

            eco_bool_ent = z_conf.get(CONF_WINDOW_ECO_BOOLEAN, f"input_boolean.eco_mode_request_{z_key}_hvac_advisor")
            eco_active = (self._get_val(eco_bool_ent, "off") == "on")

            home_cool = float(self._get_val(f"input_number.hvac_preset_{z_key}_home_cool", 74.0) or 74.0)
            home_heat = float(self._get_val(f"input_number.hvac_preset_{z_key}_home_heat", 68.0) or 68.0)
            sleep_cool_raw = self._get_val(f"input_number.hvac_preset_{z_key}_sleep_cool")
            sleep_heat_raw = self._get_val(f"input_number.hvac_preset_{z_key}_sleep_heat")
            sleep_cool = float(sleep_cool_raw) if sleep_cool_raw is not None else None
            sleep_heat = float(sleep_heat_raw) if sleep_heat_raw is not None else None

            act_heat, act_cool = resolve_zone_setpoints(
                zone_id=z_key,
                is_bedtime=is_bedtime,
                is_evening=is_evening,
                climate_state=c_state,
                climate_attrs=c_attrs,
                eco_active=eco_active,
                preset_home_cool=home_cool,
                preset_home_heat=home_heat,
                preset_sleep_cool=sleep_cool,
                preset_sleep_heat=sleep_heat,
                protect_cool=protect_cool,
                protect_heat=protect_heat,
            )
            zone_setpoints[z_key] = (act_heat, act_cool)

        # Global Active Setpoints & Macro Seasonal Mode
        up_sp = zone_setpoints.get("upstairs", (68.0, 74.0))
        down_sp = zone_setpoints.get("downstairs", (68.0, 74.0))
        global_active_heat = (up_sp[0] + down_sp[0]) / 2.0
        global_active_cool = (up_sp[1] + down_sp[1]) / 2.0

        seasonal_mode, seasonal_details = determine_seasonal_mode(
            hvac_mode=overall_hvac_mode,
            forecast=forecast_list,
            comfort_profile=comfort_profile,
            active_heat_sp=global_active_heat,
            active_cool_sp=global_active_cool,
        )
        self.seasonal_mode = seasonal_mode
        self.seasonal_details = seasonal_details

        # 4. Tier 0: Global Comfort Recovery Handling
        recovery_bool_ent = self.options.get(CONF_COMFORT_RECOVERY_BOOLEAN, self.entry_data.get(CONF_COMFORT_RECOVERY_BOOLEAN, DEFAULT_COMFORT_RECOVERY_BOOLEAN))
        is_recovery_on = (self._get_val(recovery_bool_ent, "off") == "on")
        self.comfort_recovery_active = is_recovery_on

        up_conf = zones_config.get("upstairs", {})
        down_conf = zones_config.get("downstairs", {})
        up_temp = float(self._get_val(up_conf.get(CONF_TEMP_SENSOR), 72.0) or 72.0)
        down_temp = float(self._get_val(down_conf.get(CONF_TEMP_SENSOR), 72.0) or 72.0)
        up_hum = float(self._get_val(up_conf.get(CONF_HUMIDITY_SENSOR), 50.0) or 50.0)
        down_hum = float(self._get_val(down_conf.get(CONF_HUMIDITY_SENSOR), 50.0) or 50.0)

        if is_recovery_on:
            avg_temp = (up_temp + down_temp) / 2.0
            target = global_active_heat if overall_hvac_mode == "heat" else global_active_cool if overall_hvac_mode == "cool" else None
            is_restored = False
            if target:
                if abs(avg_temp - target) <= 0.25:
                    is_restored = True
                elif overall_hvac_mode == "cool" and avg_temp < target:
                    is_restored = True
                elif overall_hvac_mode == "heat" and avg_temp > target:
                    is_restored = True

            if is_restored:
                _LOGGER.info("Passable Smart Climate: Comfort recovered to target (%.1f°F). Disengaging recovery mode.", target or 72.0)
                await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": recovery_bool_ent})
                self.comfort_recovery_active = False
            else:
                # Force all zone window eco modes off so HVAC runs full power
                for z_c in zones_config.values():
                    e_b = z_c.get(CONF_WINDOW_ECO_BOOLEAN)
                    if e_b:
                        await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": e_b})

                ts_str = now.strftime("%-I:%M %p on %b %-d")
                recovery_details = (
                    f"**Action Plan as of {ts_str}:**\n\n"
                    f"**Comfort recovery in progress.**\n"
                    f"HVAC is running to reach the target of **{target:.1f}°F**."
                )
                self.last_recommendation = "Close Windows"
                self.last_details = recovery_details
                self.current_scenario = "comfort_recovery_in_progress"

                # Static closed plan for Card 0
                m_dt = now.replace(hour=morning_start_time.hour, minute=morning_start_time.minute, second=0, microsecond=0)
                if m_dt <= now:
                    m_dt += datetime.timedelta(days=1)
                sim_hours = max(4, int((m_dt - now).total_seconds() / 3600)) if is_bedtime else 8
                recov_plan = run_static_simulation(
                    initial_temp=(up_temp + down_temp) / 2.0,
                    initial_humidity=(up_hum + down_hum) / 2.0,
                    forecast=forecast_list[:sim_hours],
                    model_temp=self.models.get("upstairs_temp_profile_win_closed"),
                    model_hum=self.models.get("upstairs_humidity_profile_win_closed"),
                    strategy="closed",
                    lat=lat,
                    lon=lon,
                    solcast_forecast=solcast_forecast,
                )
                self.last_plan_history = recov_plan

                rec_ent = self.options.get(CONF_RECOMMENDATION_ENTITY, self.entry_data.get(CONF_RECOMMENDATION_ENTITY, DEFAULT_RECOMMENDATION_ENTITY))
                if rec_ent:
                    try:
                        await self.hass.services.async_call("input_text", "set_value", {"entity_id": rec_ent, "value": "Close Windows"})
                        self.hass.states.async_set(rec_ent, "Close Windows", {"details": recovery_details})
                    except Exception:
                        pass

                return {
                    "state": "Close Windows",
                    "details": recovery_details,
                    "history": recov_plan,
                    "is_vetoed": False,
                    "veto_reason": "",
                }

        # 5. Dual-Zone Evaluation
        zone_plans: dict[str, ZonePlanResult] = {}
        min_enthalpy = float(self.options.get(CONF_MIN_ENTHALPY_DELTA, DEFAULT_MIN_ENTHALPY_DELTA))
        max_dp = float(self.options.get(CONF_MAX_DEW_POINT, DEFAULT_MAX_DEW_POINT))
        open_margin = float(self.options.get(CONF_OPEN_TEMP_MARGIN, DEFAULT_OPEN_TEMP_MARGIN))
        close_margin = float(self.options.get(CONF_CLOSE_TEMP_MARGIN, DEFAULT_CLOSE_TEMP_MARGIN))

        any_recovery_triggered = False

        for z_key, z_conf in zones_config.items():
            z_name = z_conf.get(CONF_ZONE_NAME, z_key.capitalize())
            climate_ent = z_conf.get(CONF_CLIMATE_ENTITY)
            temp_ent = z_conf.get(CONF_TEMP_SENSOR)
            hum_ent = z_conf.get(CONF_HUMIDITY_SENSOR)
            win_ent = z_conf.get(CONF_OPEN_WINDOWS_SENSOR)

            c_st = zone_climates.get(z_key, "off")
            act_heat, act_cool = zone_setpoints.get(z_key, (68.0, 74.0))
            in_temp = float(self._get_val(temp_ent, 72.0) or 72.0)
            in_hum = float(self._get_val(hum_ent, 50.0) or 50.0)

            windows_count = int(self._get_val(win_ent, 0) or 0)
            is_open = windows_count > 0

            # Safe temperature bounds relative to active setpoints
            safe_min = min(65.0, act_heat) - 1.0
            safe_max = max(75.0, act_cool) + 1.0
            if seasonal_mode == "heatwave_prep":
                safe_min = max(60.0, act_heat - 2.0)

            z_models = {
                "temp_open": self.models.get(f"{z_key}_temp_profile_win_open") or RateModel("ransac", 0.3, 0.0),
                "temp_closed": self.models.get(f"{z_key}_temp_profile_win_closed") or RateModel("ransac", 0.05, 0.0),
                "humidity_open": self.models.get(f"{z_key}_humidity_profile_win_open"),
                "humidity_closed": self.models.get(f"{z_key}_humidity_profile_win_closed"),
            }

            plan = evaluate_zone_plan(
                zone_name=z_name,
                inside_temp=in_temp,
                inside_humidity=in_hum,
                forecast=forecast_list,
                current_outside_temp=w_temp,
                models=z_models,
                hvac_mode=c_st,
                seasonal_mode=seasonal_mode,
                comfort_profile=comfort_profile,
                safe_min_temp=safe_min,
                safe_max_temp=safe_max,
                lat=lat,
                lon=lon,
                is_bedtime=is_bedtime,
                is_evening=is_evening,
                parent_bedtime=parent_bedtime_time,
                bedtime_start=bedtime_start_time,
                morning_start=morning_start_time,
                active_heat_sp=act_heat,
                active_cool_sp=act_cool,
                now=now,
                solcast_forecast=solcast_forecast,
                is_currently_open=is_open,
                last_bad_weather_time=self._last_bad_weather_time,
                min_enthalpy_delta=min_enthalpy,
                max_dew_point=max_dp,
                open_temp_margin=open_margin,
                close_temp_margin=close_margin,
            )

            if plan.comfort_recovery_triggered:
                any_recovery_triggered = True

            if is_vetoed:
                plan.recommended_state = "Close Windows"
                plan.details_message = f"Keep {z_name} windows closed ({veto_reason})"
                plan.scenario = "hazard_vetoed"

            zone_plans[z_key] = plan

            # Sync Eco Mode request helper
            eco_bool = z_conf.get(CONF_WINDOW_ECO_BOOLEAN, f"input_boolean.eco_mode_request_{z_key}_hvac_advisor")
            if eco_bool:
                try:
                    target_svc = "turn_on" if plan.eco_mode_requested else "turn_off"
                    await self.hass.services.async_call("input_boolean", target_svc, {"entity_id": eco_bool})
                except Exception:
                    pass

            # Convective Circulation Management
            circ_enabled = self.options.get(
                f"{z_key}_circ_enabled",
                z_conf.get(CONF_CIRCULATION_ENABLED, False),
            )
            if circ_enabled:
                source_temp_ent = z_conf.get(CONF_CIRCULATION_SOURCE_TEMP)
                active_bool_ent = z_conf.get(CONF_CIRCULATION_ACTIVE_BOOLEAN)
                eco_bool_ent = z_conf.get(CONF_CIRCULATION_ECO_BOOLEAN)

                source_temp = float(self._get_val(source_temp_ent, in_temp) or in_temp)
                fan_is_active = (self._get_val(active_bool_ent, "off") == "on")

                circ_action, lockout = self.circulation_manager.evaluate_circulation(
                    z_key=z_key,
                    area_temp=in_temp,
                    source_temp=source_temp,
                    target_cool=act_cool,
                    target_heat=act_heat,
                    hvac_mode=c_st,
                    seasonal_mode=seasonal_mode,
                    windows_are_open=is_open,
                    out_temp=w_temp,
                    model=z_models.get("temp_closed"),
                    now_utc=now_utc,
                    is_active=fan_is_active,
                    delta_threshold=float(self.options.get(CONF_CIRC_DELTA_THRESHOLD, DEFAULT_CIRC_DELTA_THRESHOLD)),
                    min_runtime_minutes=int(self.options.get(CONF_CIRC_MIN_MINUTES, DEFAULT_CIRC_MIN_MINUTES)),
                    max_runtime_minutes=int(self.options.get(CONF_CIRC_MAX_MINUTES, DEFAULT_CIRC_MAX_MINUTES)),
                    stall_margin=float(self.options.get(CONF_CIRC_STALL_MARGIN, DEFAULT_CIRC_STALL_MARGIN)),
                    lockout_hours=int(self.options.get(CONF_CIRC_LOCKOUT_HOURS, DEFAULT_CIRC_LOCKOUT_HOURS)),
                )

                if circ_action == "turn_on" and not fan_is_active:
                    _LOGGER.info("Passable Smart Climate: Engaging circulation fan for %s.", z_name)
                    if active_bool_ent:
                        await self.hass.services.async_call("input_boolean", "turn_on", {"entity_id": active_bool_ent})
                    if eco_bool_ent:
                        await self.hass.services.async_call("input_boolean", "turn_on", {"entity_id": eco_bool_ent})
                    if climate_ent:
                        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_ent, "fan_mode": "on"})
                    self.circulation_manager.state_memory[z_key]["start_time"] = now_utc
                    self.circulation_manager.state_memory[z_key]["start_temp"] = in_temp
                    await self._learning_store.async_save(self.circulation_manager.export_learning_data())

                elif circ_action == "turn_off" and fan_is_active:
                    _LOGGER.info("Passable Smart Climate: Shutting off circulation fan for %s.", z_name)
                    if active_bool_ent:
                        await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": active_bool_ent})
                    if eco_bool_ent:
                        await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": eco_bool_ent})
                    if climate_ent:
                        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_ent, "fan_mode": "auto"})
                    if lockout > 0:
                        self.circulation_manager.state_memory[z_key]["lockout_until"] = now_utc + datetime.timedelta(hours=lockout)
                    self.circulation_manager.state_memory[z_key]["start_time"] = None
                    self.circulation_manager.state_memory[z_key]["start_temp"] = None
                    await self._learning_store.async_save(self.circulation_manager.export_learning_data())

        if any_recovery_triggered and recovery_bool_ent:
            try:
                await self.hass.services.async_call("input_boolean", "turn_on", {"entity_id": recovery_bool_ent})
                self.comfort_recovery_active = True
            except Exception as e:
                _LOGGER.error("Failed to turn on comfort recovery latch: %s", e)

        self.zone_results = zone_plans

        # 6. Combined Whole-House Formulation & Markdown Details
        any_open = any(p.recommended_state == "Open Windows" for p in zone_plans.values())
        final_state = "Open Windows" if any_open else "Close Windows"
        self.last_recommendation = final_state

        ts_str = now.strftime("%-I:%M %p on %b %-d")
        details = f"**Action Plan as of {ts_str}:**\n\n"
        up_plan = zone_plans.get("upstairs")
        down_plan = zone_plans.get("downstairs")

        primary_plan = up_plan or down_plan
        if primary_plan:
            self.current_scenario = primary_plan.scenario
            self.current_target_time = primary_plan.target_time

        if up_plan and down_plan:
            norm_up = up_plan.details_message.replace("Upstairs", "windows").replace("upstairs", "windows")
            norm_down = down_plan.details_message.replace("Downstairs", "windows").replace("downstairs", "windows")
            if norm_up == norm_down:
                clean_detail = norm_up.replace("windows windows", "windows")
                details += f"**Whole House:** {clean_detail}"
            else:
                details += f"**Upstairs:** {up_plan.details_message}\n**Downstairs:** {down_plan.details_message}"
        elif up_plan:
            details += f"**Upstairs:** {up_plan.details_message}"
        elif down_plan:
            details += f"**Downstairs:** {down_plan.details_message}"

        if seasonal_details:
            details += f"\n\n{seasonal_details}"

        # Average history for Card 0 trajectory plot
        avg_hist: list[dict[str, float]] = []
        if up_plan and down_plan and up_plan.history and down_plan.history:
            min_l = min(len(up_plan.history), len(down_plan.history))
            for idx in range(min_l):
                t_avg = round((up_plan.history[idx]["temp"] + down_plan.history[idx]["temp"]) / 2.0, 2)
                h_avg = round((up_plan.history[idx]["humidity"] + down_plan.history[idx]["humidity"]) / 2.0, 2)
                avg_hist.append({"temp": t_avg, "humidity": h_avg})
        elif up_plan and up_plan.history:
            avg_hist = up_plan.history
        elif down_plan and down_plan.history:
            avg_hist = down_plan.history
        self.last_plan_history = avg_hist

        # 7. Bedtime Overnight Heat Prediction Alert
        self.heat_prediction_alert = False
        if is_bedtime and (overall_hvac_mode in ["cool", "off"]) and avg_hist:
            min_comfort_temp = 68.0
            if comfort_profile and comfort_profile.get("lower_profile", {}).get("temperature_data_points"):
                min_comfort_temp = min(comfort_profile["lower_profile"]["temperature_data_points"])

            offset_val = float(self._get_val(CONF_HVAC_TRANSITION_OFFSET, DEFAULT_HVAC_TRANSITION_OFFSET) or 2.0)
            predict_heat_ent = self.options.get(CONF_PREDICT_HEAT_BOOLEAN, self.entry_data.get(CONF_PREDICT_HEAT_BOOLEAN, DEFAULT_PREDICT_HEAT_BOOLEAN))
            predict_msg_ent = self.options.get(CONF_PREDICTION_MESSAGE_TEXT, self.entry_data.get(CONF_PREDICTION_MESSAGE_TEXT, DEFAULT_PREDICTION_MESSAGE_TEXT))
            is_prediction_on = (self._get_val(predict_heat_ent, "off") == "on")

            transition_threshold = (min_comfort_temp - offset_val) if overall_hvac_mode == "cool" else min_comfort_temp

            first_breach_time: str | None = None
            first_breach_temp: float | None = None
            for idx, pt in enumerate(avg_hist):
                if pt["temp"] < transition_threshold and idx < len(forecast_list):
                    dt_raw = forecast_list[idx].get("datetime")
                    if dt_raw:
                        local_dt = dt_util.as_local(datetime.datetime.fromisoformat(str(dt_raw)))
                        h = local_dt.hour % 12 or 12
                        first_breach_time = f"{h}:{local_dt.strftime('%M %p')}"
                        first_breach_temp = pt["temp"]
                        break

            if first_breach_time:
                self.heat_prediction_alert = True
                msg = (
                    f"The advisor predicts the house will get cold enough to require heat overnight "
                    f"(dropping to {first_breach_temp:.1f}°F around {first_breach_time}). "
                    f"Would you like to turn the heat on now?"
                )
                if not is_prediction_on:
                    try:
                        await self.hass.services.async_call("input_text", "set_value", {"entity_id": predict_msg_ent, "value": msg})
                        await self.hass.services.async_call("input_boolean", "turn_on", {"entity_id": predict_heat_ent})
                    except Exception as e:
                        _LOGGER.error("Failed to set heat prediction entities: %s", e)

                details += "\n\n**Note:** A switch to **Heat Mode** may be required overnight."

        self.last_details = details

        # 8. Sync Overview Helper Directly
        rec_ent = self.options.get(CONF_RECOMMENDATION_ENTITY, self.entry_data.get(CONF_RECOMMENDATION_ENTITY, DEFAULT_RECOMMENDATION_ENTITY))
        if rec_ent:
            try:
                await self.hass.services.async_call(
                    "input_text",
                    "set_value",
                    {"entity_id": rec_ent, "value": final_state},
                )
                self.hass.states.async_set(rec_ent, final_state, {"details": details})
            except Exception:
                pass

        return {
            "state": final_state,
            "details": details,
            "history": avg_hist,
            "is_vetoed": is_vetoed,
            "veto_reason": veto_reason,
            "phase": self.current_phase,
            "scenario": self.current_scenario,
            "target_time": self.current_target_time,
            "seasonal_mode": self.seasonal_mode,
            "seasonal_details": self.seasonal_details,
            "heat_prediction_alert": self.heat_prediction_alert,
            "comfort_recovery_active": self.comfort_recovery_active,
        }
