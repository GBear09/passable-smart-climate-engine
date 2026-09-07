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
    CONF_ZONES,
    DEFAULT_AQI_THRESHOLD,
    DEFAULT_CIRC_DELTA_THRESHOLD,
    DEFAULT_CIRC_LOCKOUT_HOURS,
    DEFAULT_CIRC_MAX_MINUTES,
    DEFAULT_CIRC_MIN_MINUTES,
    DEFAULT_CIRC_STALL_MARGIN,
    DEFAULT_CLOSE_TEMP_MARGIN,
    DEFAULT_FORECAST_LOOKAHEAD_MINUTES,
    DEFAULT_HIGH_WIND_GUST,
    DEFAULT_HIGH_WIND_SPEED,
    DEFAULT_MAX_DEW_POINT,
    DEFAULT_MAX_PRECIPITATION_PROBABILITY,
    DEFAULT_MIN_ENTHALPY_DELTA,
    DEFAULT_OPEN_DWELL_MINUTES,
    DEFAULT_OPEN_TEMP_MARGIN,
    DOMAIN,
)
from .core.models import RateModel, ZonePlanResult
from .core.psychrometrics import PsychrometricEngine
from .engines.advisor import (
    determine_seasonal_mode,
    evaluate_environmental_vetoes,
    evaluate_zone_plan,
    get_comfort_bounds,
)
from .engines.circulation import CirculationManager
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

        cp_ent = self.options.get(CONF_COMFORT_PROFILE, self.entry_data.get(CONF_COMFORT_PROFILE))
        if cp_ent and cp_ent not in entities_to_track:
            entities_to_track.append(cp_ent)

        if entities_to_track:
            async def _handle_tracked_state_change(event: Any) -> None:
                _LOGGER.debug("Reactive trigger from tracked entity %s; refreshing climate plan.", event.data.get("entity_id"))
                await self.async_request_refresh()

            self._unsub_track = async_track_state_change_event(
                self.hass, entities_to_track, _handle_tracked_state_change
            )

    def async_unload(self) -> None:
        """Unsubscribe all listeners."""
        if hasattr(self, "_unsub_track") and self._unsub_track:
            self._unsub_track()
            self._unsub_track = None

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
                # Persist to disk
                serialized = {k: m.__dict__ for k, m in self.models.items()}
                await self._models_store.async_save(serialized)
                _LOGGER.info("Passable Smart Climate: Retrained and saved %d models.", len(self.models))
                self.async_update_listeners()
        except Exception as err:
            _LOGGER.error("Model retraining failed: %s", err)

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

        weather_ent = self.options.get(CONF_WEATHER_ENTITY, self.entry_data.get(CONF_WEATHER_ENTITY, "weather.home"))
        home_mode_ent = self.options.get(CONF_HOME_MODE_ENTITY, self.entry_data.get(CONF_HOME_MODE_ENTITY))
        presence_ent = self.options.get(CONF_PRESENCE_ENTITY, self.entry_data.get(CONF_PRESENCE_ENTITY))
        comfort_profile_ent = self.options.get(CONF_COMFORT_PROFILE, self.entry_data.get(CONF_COMFORT_PROFILE))

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

        # Fallback to weather entity state attributes if get_forecasts was empty
        if not forecast_list:
            forecast_attr = self._get_attr(weather_ent, "forecast", [])
            forecast_list = list(forecast_attr) if isinstance(forecast_attr, list) else []

        w_state = self._get_val(weather_ent)
        w_temp = float(self._get_attr(weather_ent, "temperature", 70.0) or 70.0)
        w_humidity = float(self._get_attr(weather_ent, "humidity", 50.0) or 50.0)
        w_wind = float(self._get_attr(weather_ent, "wind_speed", 0.0) or 0.0)
        w_gust = float(self._get_attr(weather_ent, "wind_gust", 0.0) or 0.0)

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

        # Solcast PV Forecast
        solcast_forecast: list[dict[str, Any]] = []
        try:
            today_attrs = self.hass.states.get("sensor.solcast_pv_forecast_forecast_today")
            if today_attrs and "detailedForecast" in today_attrs.attributes:
                for item in today_attrs.attributes["detailedForecast"]:
                    dt_p = datetime.datetime.fromisoformat(item["period_start"])
                    solcast_forecast.append({"dt": dt_p, "power": float(item["pv_estimate"]) * 1000.0})
        except Exception:
            pass

        # Comfort Profile Polynomial Bounds
        comfort_profile: dict[str, Any] | None = None
        if comfort_profile_ent:
            cp_st = self.hass.states.get(comfort_profile_ent)
            if cp_st:
                comfort_profile = {
                    "upper_profile": cp_st.attributes.get("upper_profile"),
                    "lower_profile": cp_st.attributes.get("lower_profile"),
                }

        # 2. Dual-Zone Evaluation (Upstairs & Downstairs)
        zones_config = self.entry_data.get(CONF_ZONES, {})
        zone_plans: dict[str, ZonePlanResult] = {}

        min_enthalpy = float(self.options.get(CONF_MIN_ENTHALPY_DELTA, DEFAULT_MIN_ENTHALPY_DELTA))
        max_dp = float(self.options.get(CONF_MAX_DEW_POINT, DEFAULT_MAX_DEW_POINT))
        open_margin = float(self.options.get(CONF_OPEN_TEMP_MARGIN, DEFAULT_OPEN_TEMP_MARGIN))
        close_margin = float(self.options.get(CONF_CLOSE_TEMP_MARGIN, DEFAULT_CLOSE_TEMP_MARGIN))

        for z_key, z_conf in zones_config.items():
            z_name = z_conf.get(CONF_ZONE_NAME, z_key.capitalize())
            climate_ent = z_conf.get(CONF_CLIMATE_ENTITY)
            temp_ent = z_conf.get(CONF_TEMP_SENSOR)
            hum_ent = z_conf.get(CONF_HUMIDITY_SENSOR)
            win_ent = z_conf.get(CONF_OPEN_WINDOWS_SENSOR)

            c_st = self._get_val(climate_ent, "off")
            c_target = float(self._get_attr(climate_ent, "temperature", 72.0) or 72.0)
            in_temp = float(self._get_val(temp_ent, 72.0) or 72.0)
            in_hum = float(self._get_val(hum_ent, 50.0) or 50.0)

            windows_count = int(self._get_val(win_ent, 0) or 0)
            is_open = windows_count > 0

            # Safe temperature bounds relative to thermostat
            safe_min = max(66.0, c_target - 4.0)
            safe_max = min(78.0, c_target + 4.0)

            # Extract in-memory models for this zone
            z_models = {
                "temp_open": self.models.get(f"{z_key}_temp_profile_win_open") or RateModel("ransac", 0.3, 0.0),
                "temp_closed": self.models.get(f"{z_key}_temp_profile_win_closed") or RateModel("ransac", 0.05, 0.0),
                "humidity_open": self.models.get(f"{z_key}_humidity_profile_win_open"),
                "humidity_closed": self.models.get(f"{z_key}_humidity_profile_win_closed"),
            }

            seasonal_mode, _ = determine_seasonal_mode(
                hvac_mode=c_st,
                forecast=forecast_list,
                comfort_profile=comfort_profile,
                active_heat_sp=68.0,
                active_cool_sp=75.0,
            )

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
                solcast_forecast=solcast_forecast,
                is_currently_open=is_open,
                min_enthalpy_delta=min_enthalpy,
                max_dew_point=max_dp,
                open_temp_margin=open_margin,
                close_temp_margin=close_margin,
            )

            # If an environmental hazard veto is active, override recommendation to Close
            if is_vetoed:
                plan.recommended_state = "Close Windows"
                plan.details_message = f"Keep {z_name} windows closed ({veto_reason})"

            zone_plans[z_key] = plan

            # Sync Eco Mode request helper
            eco_bool = z_conf.get(CONF_WINDOW_ECO_BOOLEAN)
            if eco_bool:
                try:
                    target_svc = "turn_on" if plan.eco_mode_requested else "turn_off"
                    await self.hass.services.async_call("input_boolean", target_svc, {"entity_id": eco_bool})
                except Exception:
                    pass

            # 3. Convective Circulation Management
            if z_conf.get(CONF_CIRCULATION_ENABLED, False):
                source_temp_ent = z_conf.get(CONF_CIRCULATION_SOURCE_TEMP)
                active_bool_ent = z_conf.get(CONF_CIRCULATION_ACTIVE_BOOLEAN)
                eco_bool_ent = z_conf.get(CONF_CIRCULATION_ECO_BOOLEAN)

                source_temp = float(self._get_val(source_temp_ent, in_temp) or in_temp)
                fan_is_active = (self._get_val(active_bool_ent, "off") == "on")

                circ_action, lockout = self.circulation_manager.evaluate_circulation(
                    z_key=z_key,
                    area_temp=in_temp,
                    source_temp=source_temp,
                    target_cool=75.0,
                    target_heat=68.0,
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
                    lockout_hours=lockout or int(self.options.get(CONF_CIRC_LOCKOUT_HOURS, DEFAULT_CIRC_LOCKOUT_HOURS)),
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
                    self.circulation_manager.state_memory[z_key]["start_time"] = None
                    self.circulation_manager.state_memory[z_key]["start_temp"] = None
                    await self._learning_store.async_save(self.circulation_manager.export_learning_data())

        self.zone_results = zone_plans

        # 4. Formulate Combined Whole-House Plan & Format Markdown Details
        any_open = any(p.recommended_state == "Open Windows" for p in zone_plans.values())
        final_state = "Open Windows" if any_open else "Close Windows"
        self.last_recommendation = final_state

        ts_str = now.strftime("%-I:%M %p on %b %-d")
        details = f"**Action Plan as of {ts_str}:**\n\n"
        up_plan = zone_plans.get("upstairs")
        down_plan = zone_plans.get("downstairs")

        if up_plan and down_plan and up_plan.details_message == down_plan.details_message:
            details += f"**Whole House:** {up_plan.details_message}"
        else:
            if up_plan:
                details += f"**Upstairs:** {up_plan.details_message}\n"
            if down_plan:
                details += f"**Downstairs:** {down_plan.details_message}"

        self.last_details = details

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
        self.last_plan_history = avg_hist

        # Sync overview helper directly
        rec_ent = self.options.get(CONF_RECOMMENDATION_ENTITY, self.entry_data.get(CONF_RECOMMENDATION_ENTITY))
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
        }
