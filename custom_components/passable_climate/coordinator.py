"""DataUpdateCoordinator for Passable Smart Climate Engine.
Coordinates weather ingestion, multi-zone micro-simulations, and circulation control.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfPower,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
import homeassistant.util.dt as dt_util
from homeassistant.util.unit_conversion import PowerConverter, SpeedConverter, TemperatureConverter

from .const import (
    CONF_AQI_ENTITY,
    CONF_AQI_THRESHOLD,
    CONF_BEDTIME_START,
    CONF_CIRC_COOLDOWN_MINUTES,
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
    CONF_COMFORT_DEW_POINT_MAX,
    CONF_COMFORT_HUMIDITY_MAX,
    CONF_COMFORT_HUMIDITY_MIN,
    CONF_COMFORT_PROFILE,
    CONF_COMFORT_RECOVERY_BOOLEAN,
    CONF_COMFORT_TEMP_MAX,
    CONF_COMFORT_TEMP_MIN,
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
    CONF_PREEMPTIVE_ECO_MODE,
    CONF_PRESENCE_ENTITY,
    CONF_RECOMMENDATION_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    CONF_SOLCAST_POWER_ENTITY,
    CONF_TEMP_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_CONTACT_DEBOUNCE_SECONDS,
    CONF_WINDOW_ECO_BOOLEAN,
    CONF_ZONE_NAME,
    CONF_ZONES,
    DEFAULT_AQI_THRESHOLD,
    DEFAULT_BEDTIME_START,
    DEFAULT_CIRC_COOLDOWN_MINUTES,
    DEFAULT_CIRC_DELTA_THRESHOLD,
    DEFAULT_CIRC_LOCKOUT_HOURS,
    DEFAULT_CIRC_MAX_MINUTES,
    DEFAULT_CIRC_MIN_MINUTES,
    DEFAULT_CIRC_STALL_MARGIN,
    DEFAULT_CLOSE_TEMP_MARGIN,
    DEFAULT_COMFORT_DEW_POINT_MAX,
    DEFAULT_COMFORT_HUMIDITY_MAX,
    DEFAULT_COMFORT_HUMIDITY_MIN,
    DEFAULT_COMFORT_PROFILE_ENTITY,
    DEFAULT_COMFORT_RECOVERY_BOOLEAN,
    DEFAULT_COMFORT_ROLLOFF_TEMP,
    DEFAULT_COMFORT_TEMP_MAX,
    DEFAULT_COMFORT_TEMP_MIN,
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
    DEFAULT_PREEMPTIVE_ECO_MODE,
    DEFAULT_PRESENCE_ENTITY,
    DEFAULT_RECOMMENDATION_ENTITY,
    DEFAULT_SOLAR_POWER_ENTITY,
    DEFAULT_SOLCAST_POWER_ENTITY,
    DEFAULT_UPSTAIRS_WINDOW_SENSORS,
    DEFAULT_WEATHER_ENTITY,
    DEFAULT_WINDOW_CONTACT_DEBOUNCE_SECONDS,
    DEFAULT_DOWNSTAIRS_WINDOW_SENSORS,
    DEFAULT_FILTER_CAPACITY_HOURS,
    DEFAULT_ROOMS,
    CONF_DOWNSTAIRS_WINDOW_SENSORS,
    CONF_FILTER_CAPACITY_DOWNSTAIRS,
    CONF_FILTER_CAPACITY_UPSTAIRS,
    CONF_ROOM_ENABLE_TELEMETRY,
    CONF_ROOM_INCLUDE_IN_AVG,
    CONF_ROOM_NAME,
    CONF_ROOM_TEMP_SENSOR,
    CONF_ROOM_WINDOW_SENSORS,
    CONF_ROOM_ZONE,
    CONF_ROOMS,
    CONF_UPSTAIRS_WINDOW_SENSORS,
    DOMAIN,
    METRIC_TEMP_CLOSED,
    METRIC_TEMP_COOL,
    METRIC_TEMP_HEAT,
    METRIC_TEMP_OPEN,
    RECONNECT_BLIP_GRACE_SECONDS,
    STORAGE_KEY_COMPLIANCE,
    ZONE_MLR_RATE_SPECS,
)
from collections import deque
from .core.models import RateModel, SimulationAction, ZonePlanResult
from .core.psychrometrics import PsychrometricEngine, pressure_from_elevation
from .core.simulator import run_static_simulation
from .engines.advisor import (
    BAD_WEATHER_CONDITIONS,
    calculate_preconditioning_plan,
    determine_seasonal_mode,
    evaluate_environmental_vetoes,
    evaluate_sleep_bedroom_offset,
    evaluate_zone_plan,
    get_comfort_bounds,
)
from .engines.circulation import CirculationManager
from .engines.latch import (
    CLOSE,
    OPEN,
    SAFETY_SCENARIOS,
    ComplianceLedger,
    RecommendationLatch,
    WindowContactTracker,
)
from .engines.phases import PHASE_DAYTIME, determine_temporal_phase, resolve_zone_setpoints
from .training.influx_client import InfluxClient
from .training.regression import fit_online_rolling_ols
from .training.trainer import train_all_models_sync

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
_INVALID_STATES = {STATE_UNKNOWN, STATE_UNAVAILABLE, "None", ""}
STALE_INDOOR = datetime.timedelta(hours=24)
RECOVERY_MIN_ON = datetime.timedelta(minutes=30)
RECOVERY_EXIT_MARGIN_RH = 3.0

DEFAULT_SEEDED_TELEMETRY: dict[str, dict[str, Any]] = {
    "downstairs": {
        "filter_runtime_hours": 179.56,
        "filter_last_replaced": "2026-08-01 18:01:08",
        "last_active_ts": "2026-09-18T23:59:14+00:00",
        "last_hvac_mode": "cooling",
        "heating_today": 0.0,
        "cooling_today": 0.0,
        "fan_today": 0.0,
        "today_date": "",
        "cycle_timestamps": [],
        "active_since_ts": None,
        "last_action": "idle",
        "swing_heat_samples": [],
        "swing_cool_samples": [],
        "coast_heat_samples": [],
        "coast_cool_samples": [],
        "coast_duration_samples": [],
        "active_coast_tracker": None,
        "off_target_idle_since": None,
    },
    "upstairs": {
        "filter_runtime_hours": 311.35,
        "filter_last_replaced": "2026-06-13 20:59:03",
        "last_active_ts": "2026-10-02T23:40:30+00:00",
        "last_hvac_mode": "cooling",
        "heating_today": 0.0,
        "cooling_today": 0.0,
        "fan_today": 0.0,
        "today_date": "",
        "cycle_timestamps": [],
        "active_since_ts": None,
        "last_action": "idle",
        "swing_heat_samples": [],
        "swing_cool_samples": [],
        "coast_heat_samples": [],
        "coast_cool_samples": [],
        "coast_duration_samples": [],
        "active_coast_tracker": None,
        "off_target_idle_since": None,
    },
}


class SmartClimateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Central coordinator for Passable Smart Climate Engine."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Passable Smart Climate Engine",
            update_interval=datetime.timedelta(minutes=15),
        )
        self.entry = entry
        self.entry_data = entry.data
        self.options = entry.options

        self.influx_client = InfluxClient(
            host=self.entry_data.get("influx_host", "localhost"),
            port=int(self.entry_data.get("influx_port", 8086)),
            version=str(self.entry_data.get("influx_version", "1")),
            database=self.entry_data.get("influx_database", "homeassistant"),
            username=self.entry_data.get("influx_username"),
            password=self.entry_data.get("influx_password"),
            token=self.entry_data.get("influx_token"),
            org=self.entry_data.get("influx_org"),
            bucket=self.entry_data.get("influx_bucket"),
        )

        self.models: dict[str, RateModel] = {}
        self.circulation_manager = CirculationManager()
        self._models_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.models")
        self._learning_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.learning_data")
        self._latch_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.latch")
        self._telemetry_store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.telemetry")
        self._compliance_store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_COMPLIANCE}.{entry.entry_id}")

        self._latch = RecommendationLatch()
        self.window_tracker = WindowContactTracker()
        self.compliance_ledger = ComplianceLedger()
        self._last_commanded: dict[str, bool] = {}
        self._recovery_since: datetime.datetime | None = None
        self.actuation_enabled: bool = False
        self._circ_lock = asyncio.Lock()
        self._train_lock = asyncio.Lock()
        self.last_update_time: datetime.datetime | None = None

        elevation_m = float(getattr(hass.config, "elevation", 0.0) or 0.0)
        self.pressure_hpa: float = pressure_from_elevation(elevation_m)

        self.last_recommendation: str = CLOSE
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
        self.daily_outlook_summary: str = ""
        self.zone_outlook_summaries: dict[str, str] = {"upstairs": "", "downstairs": ""}

        # Psychrometric Box Comfort Envelope Parameters
        self.comfort_settings: dict[str, float] = {
            CONF_COMFORT_TEMP_MIN: DEFAULT_COMFORT_TEMP_MIN,
            CONF_COMFORT_TEMP_MAX: DEFAULT_COMFORT_TEMP_MAX,
            CONF_COMFORT_HUMIDITY_MIN: DEFAULT_COMFORT_HUMIDITY_MIN,
            CONF_COMFORT_HUMIDITY_MAX: DEFAULT_COMFORT_HUMIDITY_MAX,
            CONF_COMFORT_DEW_POINT_MAX: DEFAULT_COMFORT_DEW_POINT_MAX,
        }

        # Native MLR Telemetry, Filter Life, Daily Runtime & Health Diagnostics State
        self._series_buffers: dict[str, deque[tuple[float, float]]] = {}
        self._online_ols_buffers: dict[str, deque[tuple[float, float]]] = {}
        self._last_telemetry_tick_ts: float | None = None
        self.telemetry_state: dict[str, dict[str, Any]] = {
            z: dict(DEFAULT_SEEDED_TELEMETRY[z]) for z in ("upstairs", "downstairs")
        }
        self.zone_mlr_rates: dict[str, float | None] = {}
        self.zone_mlr_attrs: dict[str, dict[str, Any]] = {}
        self.room_mlr_rates: dict[str, float | None] = {}
        self.room_mlr_attrs: dict[str, dict[str, Any]] = {}
        self.abs_humidity: dict[str, float | None] = {
            "outside": None,
            "upstairs": None,
            "downstairs": None,
        }
        self.open_windows_counts: dict[str, int] = {
            "upstairs": 0,
            "downstairs": 0,
            "total": 0,
        }
        self.open_windows_attrs: dict[str, dict[str, Any]] = {
            "upstairs": {},
            "downstairs": {},
            "total": {},
        }
        self.pre_condition_needed: bool = False
        self.pre_condition_mode: str | None = None
        self.pre_condition_start_time: str | None = None
        self.pre_condition_lead_minutes: int | None = None
        self.predicted_breach_time: str | None = None
        self.sleep_limiting_room: str | None = None
        self.sleep_limiting_room_entity: str | None = None
        self.sleep_limiting_room_temp: float | None = None
        self.recommended_sleep_offset_f: float = 0.0
        self.compliance_summary: str = "No open-window recommendations in the last 14 days"
        self.learned_open_windows_fraction: float = 1.0
        self.zone_fault_tolerant_rates: dict[str, float] = {
            "upstairs": 0.0,
            "downstairs": 0.0,
        }
        self.zone_diagnostics: dict[str, dict[str, Any]] = {
            "upstairs": {
                "time_to_temp": 0,
                "time_to_temp_attrs": {},
                "health_status": "Optimal",
                "health_attrs": {},
                "cycles_per_hour": 0,
                "is_coasting": False,
            },
            "downstairs": {
                "time_to_temp": 0,
                "time_to_temp_attrs": {},
                "health_status": "Optimal",
                "health_attrs": {},
                "cycles_per_hour": 0,
                "is_coasting": False,
            },
        }

    async def async_set_comfort_param(self, param_key: str, value: float) -> None:
        """Updates a psychrometric comfort envelope parameter and triggers an immediate refresh."""
        self.comfort_settings[param_key] = float(value)
        _LOGGER.debug("Updated comfort setting %s = %.1f; requesting coordinator refresh.", param_key, value)
        await self.async_request_refresh()

    async def async_initialize(self) -> None:
        """Loads cached models, circulation learning data, latch, and telemetry from Home Assistant storage."""
        try:
            import dataclasses

            cached_models = await self._models_store.async_load()
            if not cached_models:
                legacy_models_store = Store(self.hass, STORAGE_VERSION, f"{DOMAIN}.models")
                cached_models = await legacy_models_store.async_load()
                if cached_models:
                    _LOGGER.info("Passable Smart Climate: Migrating models from legacy storage key.")
                    self._models_store.async_delay_save(lambda: cached_models, 1)

            if cached_models and isinstance(cached_models, dict):
                valid_fields = {f.name for f in dataclasses.fields(RateModel)}
                for k, d in cached_models.items():
                    if isinstance(d, dict):
                        self.models[k] = RateModel(**{kk: vv for kk, vv in d.items() if kk in valid_fields})
                _LOGGER.info("Passable Smart Climate: Loaded %d cached models from storage.", len(self.models))
        except Exception as err:
            _LOGGER.warning("Could not load cached models: %s", err)

        try:
            cached_learning = await self._learning_store.async_load()
            if not cached_learning:
                legacy_learning_store = Store(self.hass, STORAGE_VERSION, f"{DOMAIN}.learning_data")
                cached_learning = await legacy_learning_store.async_load()
                if cached_learning:
                    _LOGGER.info("Passable Smart Climate: Migrating circulation learning records from legacy storage key.")
                    self._learning_store.async_delay_save(lambda: cached_learning, 1)

            if cached_learning and isinstance(cached_learning, dict):
                self.circulation_manager.load_learning_data(cached_learning)
                _LOGGER.info("Passable Smart Climate: Loaded circulation learning records from storage.")
        except Exception as err:
            _LOGGER.warning("Could not load circulation learning records: %s", err)

        try:
            cached_latch = await self._latch_store.async_load()
            if cached_latch and isinstance(cached_latch, dict):
                self._latch = RecommendationLatch.from_dict(cached_latch)
                self.last_recommendation = self._latch.state
                _LOGGER.info("Passable Smart Climate: Restored recommendation latch (%s since %s).", self._latch.state, self._latch.since)
        except Exception as err:
            _LOGGER.warning("Could not restore recommendation latch: %s", err)

        try:
            cached_telem = await self._telemetry_store.async_load()
            if cached_telem and isinstance(cached_telem, dict):
                for z in ("upstairs", "downstairs"):
                    if isinstance(cached_telem.get(z), dict):
                        self.telemetry_state[z].update(cached_telem[z])
                _LOGGER.info("Passable Smart Climate: Restored HVAC filter & runtime telemetry from storage.")
            else:
                _LOGGER.info("Passable Smart Climate: Seeding initial HVAC filter & runtime telemetry snapshot.")
                self._telemetry_store.async_delay_save(lambda: self.telemetry_state, 2)
        except Exception as err:
            _LOGGER.warning("Could not restore telemetry store: %s", err)

        try:
            cached_comp = await self._compliance_store.async_load()
            if cached_comp and isinstance(cached_comp, dict):
                self.compliance_ledger = ComplianceLedger.from_dict(cached_comp)
                comp_sum = self.compliance_ledger.get_summary()
                self.compliance_summary = comp_sum["compliance_summary"]
                self.learned_open_windows_fraction = comp_sum["learned_open_windows_fraction"]
                _LOGGER.info("Passable Smart Climate: Restored compliance ledger from storage.")
        except Exception as err:
            _LOGGER.warning("Could not restore compliance ledger: %s", err)

    def _weather_units(self, weather_ent: str) -> tuple[str, str]:
        """Reads temperature and wind speed units for a weather entity."""
        st = self.hass.states.get(weather_ent)
        attrs = st.attributes if st else {}
        t_unit = attrs.get("temperature_unit", self.hass.config.units.temperature_unit)
        w_unit = attrs.get("wind_speed_unit", self.hass.config.units.wind_speed_unit)
        return str(t_unit), str(w_unit)

    @staticmethod
    def _to_f(v: float, unit: str) -> float:
        """Converts temperature value from given unit to Fahrenheit."""
        try:
            return float(TemperatureConverter.convert(v, unit, UnitOfTemperature.FAHRENHEIT))
        except Exception:
            return float(v)

    @staticmethod
    def _to_mph(v: float, unit: str) -> float:
        """Converts wind speed value from given unit to miles per hour."""
        try:
            return float(SpeedConverter.convert(v, unit, UnitOfSpeed.MILES_PER_HOUR))
        except Exception:
            return float(v)

    def _power_w(self, entity_id: str | None) -> float | None:
        """Reads a power sensor and converts its measurement to Watts."""
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        val = self._read_float(entity_id, lo=-1e7, hi=1e7)
        if st is None or val is None:
            return None
        unit = st.attributes.get("unit_of_measurement", UnitOfPower.WATT)
        try:
            return float(PowerConverter.convert(val, unit, UnitOfPower.WATT))
        except Exception:
            return float(val)

    def _read_float(
        self,
        entity_id: str | None,
        *,
        lo: float,
        hi: float,
        max_age: datetime.timedelta | None = None,
    ) -> float | None:
        """Return a validated float, or None if missing/non-numeric/out-of-range/stale."""
        if not entity_id:
            return None
        st = self.hass.states.get(entity_id)
        if st is None or st.state in _INVALID_STATES or (isinstance(st.state, str) and st.state.lower() in _INVALID_STATES):
            return None
        try:
            val = float(st.state)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(val) or not (lo <= val <= hi):
            return None
        if max_age is not None:
            seen = getattr(st, "last_reported", None) or getattr(st, "last_updated", None)
            if isinstance(seen, datetime.datetime) and (dt_util.utcnow() - seen) > max_age:
                return None
        return val

    @staticmethod
    def _effective_hvac_mode(state: str, attrs: dict[str, Any], w_temp: float, in_temp: float) -> str:
        """Normalize HVAC modes, handling auto, heat_cool, and dry modes."""
        action = attrs.get("hvac_action")
        if state in ("heat_cool", "auto"):
            if action == "cooling":
                return "cool"
            if action == "heating":
                return "heat"
            return "cool" if w_temp >= in_temp else "heat"
        return {"cool": "cool", "dry": "cool", "heat": "heat"}.get(state, "off")

    def _sanitize_forecast(
        self,
        raw: list[dict[str, Any]],
        now_utc: datetime.datetime,
        t_unit: str,
        w_unit: str,
        obs: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Clean and normalize hourly forecast with observed t0 anchor."""
        out: list[dict[str, Any]] = []
        if obs:
            out.append({"datetime": now_utc.isoformat(), **obs})
        for f in raw:
            t, h, ts = f.get("temperature"), f.get("humidity"), f.get("datetime")
            if t is None or h is None or ts is None:
                continue
            try:
                dt = dt_util.parse_datetime(str(ts))
                if dt is None or dt <= now_utc:
                    continue
                out.append({
                    **f,
                    "datetime": dt.isoformat(),
                    "temperature": self._to_f(float(t), t_unit),
                    "humidity": max(0.0, min(100.0, float(h))),
                    "wind_speed": self._to_mph(float(f.get("wind_speed") or 0.0), w_unit),
                    "cloud_coverage": float(f["cloud_coverage"]) if f.get("cloud_coverage") is not None else 50.0,
                })
            except Exception:
                continue
        return out

    async def _async_command_bool(self, entity_id: str | None, on: bool) -> None:
        """Edge-triggered idempotent boolean actuation reconciled with live HA state."""
        if not entity_id or not self.actuation_enabled:
            return
        st = self.hass.states.get(entity_id)
        if st is None:
            _LOGGER.debug("Skipping missing helper %s", entity_id)
            return
        is_currently_on = (st.state == "on")
        if self._last_commanded.get(entity_id) == on and is_currently_on == on:
            return
        self._last_commanded[entity_id] = on
        if is_currently_on != on:
            await self.hass.services.async_call(
                "input_boolean",
                "turn_on" if on else "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )

    async def _async_sync_overview(self, value: str) -> None:
        """Sync recommendation value to overview helper without overriding foreign state attributes."""
        rec_ent = self.options.get(
            CONF_RECOMMENDATION_ENTITY,
            self.entry_data.get(CONF_RECOMMENDATION_ENTITY, DEFAULT_RECOMMENDATION_ENTITY),
        )
        if not rec_ent or not self.actuation_enabled:
            return
        st = self.hass.states.get(rec_ent)
        if st is None or st.state == value:
            return
        try:
            await self.hass.services.async_call(
                "input_text",
                "set_value",
                {"entity_id": rec_ent, "value": value[:255]},
                blocking=True,
            )
        except Exception as err:
            _LOGGER.warning("Could not set value on helper %s: %s", rec_ent, err)

    async def _async_sync_prediction_message(self, entity_id: str | None, value: str) -> None:
        """Sync daily climate outlook or overnight alert message to input_text helper (<=255 chars)."""
        if not entity_id or not self.actuation_enabled:
            return
        clean_val = (value or "").strip()[:255]
        if not clean_val:
            return
        st = self.hass.states.get(entity_id)
        if st is not None and st.state == clean_val:
            return
        try:
            await self.hass.services.async_call(
                "input_text",
                "set_value",
                {"entity_id": entity_id, "value": clean_val},
                blocking=True,
            )
        except Exception as err:
            _LOGGER.warning("Could not set prediction message on %s: %s", entity_id, err)

    def _build_climate_outlook_summaries(
        self,
        *,
        zone_plans: dict[str, ZonePlanResult],
        avg_hist: list[dict[str, float]],
        sanitized_forecast: list[dict[str, Any]],
        w_temp: float,
        final_state: str,
        zone_climates: dict[str, str],
    ) -> None:
        """Build Pixel Weather-style per-zone narratives and <=255-char whole-house outlook."""
        horizon_fc = sanitized_forecast[:12] if sanitized_forecast else []
        out_temps = [float(f["temperature"]) for f in horizon_fc if f.get("temperature") is not None]
        if out_temps:
            out_min = min(out_temps)
            out_max = max(out_temps)
            out_ctx = (
                f"with {out_min:.0f}°–{out_max:.0f}°F outdoors"
                if abs(out_max - out_min) >= 1.5
                else f"with ~{w_temp:.0f}°F outdoors"
            )
        else:
            out_ctx = f"with ~{w_temp:.0f}°F outdoors"

        zone_labels = {"upstairs": "Upstairs", "downstairs": "Downstairs"}
        short_parts: dict[str, str] = {}
        new_zone_summaries: dict[str, str] = {}

        for z_key in ("upstairs", "downstairs"):
            z_label = zone_labels[z_key]
            diag = self.zone_diagnostics.get(z_key, {})
            ttt_a = diag.get("time_to_temp_attrs", {})
            ttt_mins = int(diag.get("time_to_temp", 0) or 0)
            action = str(ttt_a.get("hvac_action") or zone_climates.get(z_key, "idle") or "idle").lower()
            cur_t = ttt_a.get("current_temperature")
            tgt_t = ttt_a.get("target_temperature")
            pred_rate = ttt_a.get("predicted_rate_f_hr")
            obs_rate = ttt_a.get("observed_rate_f_hr")
            if obs_rate is None:
                obs_rate = self.zone_fault_tolerant_rates.get(z_key, 0.0)
            coast_f = float(ttt_a.get("learned_coast_f") or 0.0)
            next_mode = ttt_a.get("next_hvac_cycle_mode")
            next_time = ttt_a.get("next_hvac_cycle_time")
            next_mins = ttt_a.get("next_hvac_cycle_minutes")
            next_trig = ttt_a.get("next_hvac_trigger_temp")
            pre_needed = bool(ttt_a.get("pre_condition_needed"))
            pre_mode = ttt_a.get("pre_condition_mode")
            pre_start = ttt_a.get("pre_condition_start_time")
            breach_t = ttt_a.get("predicted_breach_time")

            # Sentence 1: Live cycle & next predicted cycle
            if action in ("heating", "cooling"):
                verb = "heating" if action == "heating" else "cooling"
                short_verb = "Heating" if action == "heating" else "Cooling"
                tgt_str = f"{float(tgt_t):.0f}°F" if tgt_t is not None else "setpoint"
                if ttt_mins > 0:
                    rate_bit = (
                        f" ({float(pred_rate):+.1f}°/h)"
                        if pred_rate is not None and abs(float(pred_rate)) >= 0.2
                        else ""
                    )
                    coast_bit = f" Expect ~{coast_f:.1f}°F post-cycle coast." if coast_f >= 0.3 else ""
                    s1 = f"{z_label} is {verb} toward {tgt_str} (~{ttt_mins}m left{rate_bit}).{coast_bit}"
                    short_parts[z_key] = f"{short_verb} (~{ttt_mins}m)"
                else:
                    s1 = f"{z_label} is maintaining {tgt_str}."
                    short_parts[z_key] = f"{short_verb} (near {tgt_str})"
            elif pre_needed and pre_start:
                pre_word = "pre-cooling" if pre_mode == "cool" else "pre-heating"
                t_now_s = f"{float(cur_t):.1f}°F" if cur_t is not None else "comfort"
                breach_bit = f" before {breach_t} limit" if breach_t else ""
                s1 = f"{z_label} is idle at {t_now_s}; {pre_word} recommended around {pre_start}{breach_bit}."
                short_parts[z_key] = f"Pre-{pre_mode or 'cond'} @{pre_start}"
            elif next_mode and next_time:
                t_now_s = f"{float(cur_t):.1f}°F" if cur_t is not None else "steady"
                min_s = f" (~{int(round(float(next_mins)))}m)" if next_mins is not None else ""
                trig_s = f" near {float(next_trig):.1f}°F" if next_trig is not None else ""
                s1 = f"{z_label} is idle at {t_now_s}; next {next_mode} cycle predicted around {next_time}{min_s}{trig_s}."
                short_min = f" ({int(round(float(next_mins)))}m)" if next_mins is not None else ""
                short_parts[z_key] = f"Next {next_mode} {next_time}{short_min}"
            else:
                drift_f = float(obs_rate or 0.0)
                t_now_s = f"{float(cur_t):.1f}°F" if cur_t is not None else ""
                if abs(drift_f) >= 0.2:
                    dir_word = "warming" if drift_f > 0 else "cooling"
                    s1 = f"{z_label} is idle{' at ' + t_now_s if t_now_s else ''}, {dir_word} gently at {drift_f:+.1f}°/h."
                    short_parts[z_key] = f"Idle ({drift_f:+.1f}°/h)"
                else:
                    s1 = f"{z_label} is idle and holding steady{' near ' + t_now_s if t_now_s else ''}."
                    short_parts[z_key] = f"Idle ({t_now_s or 'Steady'})"

            # Sentence 2: 12h passive trajectory + outdoor context
            plan = zone_plans.get(z_key)
            z_hist = (plan.history if plan and plan.history else avg_hist) or []
            if len(z_hist) >= 2:
                t0, t1 = float(z_hist[0]["temp"]), float(z_hist[-1]["temp"])
                h0, h1 = float(z_hist[0]["humidity"]), float(z_hist[-1]["humidity"])
                dt = t1 - t0
                if abs(dt) < 0.8:
                    trend_desc = f"holds steady ({t0:.1f}°→{t1:.1f}°F)"
                elif dt < 0:
                    trend_desc = f"drifts {t0:.1f}°→{t1:.1f}°F ({dt:+.1f}°F)"
                else:
                    trend_desc = f"warms {t0:.1f}°→{t1:.1f}°F ({dt:+.1f}°F)"
                hrs = len(z_hist)
                s2 = f"Over the next {hrs}h unconditioned, indoor temp {trend_desc} as RH moves {h0:.0f}%→{h1:.0f}% {out_ctx}."
            else:
                s2 = f"Monitoring indoor drift {out_ctx}."

            # Sentence 3: Window & bedroom microclimate advisory
            adv_note = plan.details_message.strip() if (plan and plan.details_message) else f"Recommendation: {final_state}."
            if z_key == "upstairs" and (self.is_bedtime or self.is_evening) and self.sleep_limiting_room and self.recommended_sleep_offset_f > 0:
                lim_temp = f" ({float(self.sleep_limiting_room_temp):.1f}°F)" if self.sleep_limiting_room_temp is not None else ""
                adv_note = f"{adv_note} Sleep offset tracks {self.sleep_limiting_room}{lim_temp}."

            new_zone_summaries[z_key] = f"{s1} {s2} {adv_note}".strip()

        self.zone_outlook_summaries = new_zone_summaries

        # Concise whole-house summary for input_text.hvac_advisor_prediction_message (<=255 chars)
        segments: list[str] = []
        if "upstairs" in short_parts:
            segments.append(f"Up: {short_parts['upstairs']}")
        if "downstairs" in short_parts:
            segments.append(f"Dn: {short_parts['downstairs']}")
        head = " | ".join(segments)

        traj_bit = ""
        if len(avg_hist) >= 2:
            at0, at1 = float(avg_hist[0]["temp"]), float(avg_hist[-1]["temp"])
            ah0, ah1 = float(avg_hist[0]["humidity"]), float(avg_hist[-1]["humidity"])
            adt = at1 - at0
            traj_bit = f" 12h passive: {at0:.1f}->{at1:.1f}°F ({adt:+.1f}°), RH {ah0:.0f}->{ah1:.0f}%."

        win_tail = "Open windows." if final_state == OPEN else "Keep windows closed."
        candidate = f"{head}.{traj_bit} {win_tail}".strip() if head else f"{traj_bit.strip()} {win_tail}".strip()
        if len(candidate) > 255:
            candidate = candidate[:252].rstrip(" ,;:-") + "..."
        self.daily_outlook_summary = candidate

    def get_configured_rooms(self) -> dict[str, dict[str, Any]]:
        """Return configured rooms dictionary merged with DEFAULT_ROOMS."""
        raw = self.options.get(CONF_ROOMS, self.entry_data.get(CONF_ROOMS, DEFAULT_ROOMS))
        if not isinstance(raw, dict) or not raw:
            return dict(DEFAULT_ROOMS)
        return raw

    def get_zone_window_sensors(self, zone_id: str) -> list[str]:
        """Return all binary_sensor window entities for a given zone."""
        if zone_id == "upstairs":
            configured = list(
                self.options.get(
                    CONF_UPSTAIRS_WINDOW_SENSORS,
                    self.entry_data.get(CONF_UPSTAIRS_WINDOW_SENSORS, DEFAULT_UPSTAIRS_WINDOW_SENSORS),
                )
            )
        else:
            configured = list(
                self.options.get(
                    CONF_DOWNSTAIRS_WINDOW_SENSORS,
                    self.entry_data.get(CONF_DOWNSTAIRS_WINDOW_SENSORS, DEFAULT_DOWNSTAIRS_WINDOW_SENSORS),
                )
            )
        for r_conf in self.get_configured_rooms().values():
            if isinstance(r_conf, dict) and r_conf.get(CONF_ROOM_ZONE) == zone_id:
                for w_ent in r_conf.get(CONF_ROOM_WINDOW_SENSORS, []):
                    if w_ent and w_ent not in configured:
                        configured.append(w_ent)
        return configured

    def get_zone_open_windows_count(self, zone_id: str, now_utc: datetime.datetime | None = None) -> int:
        """Count confirmed-open window contact sensors for a zone with per-sensor debounce
        and reconnect-blip immunity, falling back to legacy count sensor if needed.
        """
        win_sensors = self.get_zone_window_sensors(zone_id)
        raw_states: dict[str, str | None] = {}
        found_any = any(w_ent in self.window_tracker._contacts for w_ent in win_sensors)
        for w_ent in win_sensors:
            st = self.hass.states.get(w_ent)
            if st is not None:
                raw_states[w_ent] = st.state
                if st.state not in _INVALID_STATES:
                    found_any = True
        if found_any:
            debounce_sec = float(
                self.options.get(
                    CONF_WINDOW_CONTACT_DEBOUNCE_SECONDS,
                    self.entry_data.get(
                        CONF_WINDOW_CONTACT_DEBOUNCE_SECONDS,
                        DEFAULT_WINDOW_CONTACT_DEBOUNCE_SECONDS,
                    ),
                )
            )
            res = self.window_tracker.evaluate_zone_contacts(
                win_sensors,
                raw_states,
                now_utc or dt_util.utcnow(),
                debounce_seconds=debounce_sec,
                reconnect_grace_seconds=float(RECONNECT_BLIP_GRACE_SECONDS),
            )
            self.open_windows_attrs[zone_id] = {
                "confirmed_open_sensors": res["confirmed_open_sensors"],
                "pending_debounce_sensors": res["pending_debounce_sensors"],
                "unavailable_sensors": res["unavailable_sensors"],
                "raw_open_count": res["raw_open_count"],
                "debounce_seconds": int(debounce_sec),
            }
            return int(res["confirmed_count"])

        zones_config = self.entry_data.get(CONF_ZONES, {})
        win_ent = zones_config.get(zone_id, {}).get(CONF_OPEN_WINDOWS_SENSOR)
        if win_ent and not win_ent.endswith("_open_windows_count"):
            raw = self._read_float(win_ent, lo=0.0, hi=100.0)
            if raw is not None:
                return int(round(raw))
        return 0

    @staticmethod
    def _calc_absolute_humidity(temp_f: float | None, rh: float | None) -> float | None:
        """Calculate Magnus-formula Absolute Humidity in g/m³ matching legacy YAML template."""
        if temp_f is None or rh is None or rh <= 0.0:
            return None
        try:
            temp_c = (temp_f - 32.0) * 5.0 / 9.0
            es = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
            e = (rh / 100.0) * es
            ah = (e * 2.1674) / (temp_c + 273.15) * 100.0
            return round(ah, 2)
        except Exception:
            return None

    def _record_and_calc_rate(self, series_key: str, val: float | None, now_ts: float) -> float:
        """Record a sample in a 10-minute rolling buffer and return the derivative in units/hour."""
        if val is None or not math.isfinite(val):
            return 0.0
        buf = self._series_buffers.setdefault(series_key, deque(maxlen=30))
        buf.append((now_ts, float(val)))
        cutoff = now_ts - 720.0
        while len(buf) > 2 and buf[0][0] < cutoff:
            buf.popleft()

        if len(buf) < 2:
            return 0.0

        # Select sample closest to 10 minutes (600s) ago, requiring at least 45s elapsed
        target_ts = now_ts - 600.0
        ref_ts, ref_val = buf[0]
        for s_ts, s_val in buf:
            if s_ts <= target_ts:
                ref_ts, ref_val = s_ts, s_val
            else:
                break

        dt_sec = now_ts - ref_ts
        if dt_sec < 45.0:
            return 0.0
        return round((float(val) - ref_val) / (dt_sec / 3600.0), 3)

    async def async_reset_filter(self, zone: str) -> None:
        """Reset filter runtime hours and update replacement timestamp for the specified zone."""
        z_key = zone.lower().strip()
        zones_to_reset = ["upstairs", "downstairs"] if z_key == "all" else [z_key]
        now_local_str = dt_util.now().strftime("%Y-%m-%d %H:%M:%S")
        for zk in zones_to_reset:
            if zk in self.telemetry_state:
                self.telemetry_state[zk]["filter_runtime_hours"] = 0.0
                self.telemetry_state[zk]["filter_last_replaced"] = now_local_str
                _LOGGER.info("Passable Smart Climate: Reset HVAC filter runtime for %s at %s.", zk, now_local_str)
        await self._telemetry_store.async_save(self.telemetry_state)
        await self._async_update_telemetry()
        self.async_update_listeners()

    def _read_thermostat_temp_f(
        self, z_key: str, climate_ent: str | None, fallback_temp: float | None = None
    ) -> float | None:
        """Read the thermostat's unsmoothed 0.1°F active-profile temperature.
        Prefers the native non-_hk climate entity (e.g. climate.downstairs) when
        climate_ent is a HomeKit integer-rounded entity (e.g. climate.downstairs_hk).
        """
        candidates: list[str] = []
        if climate_ent:
            stripped = str(climate_ent).removesuffix("_hk")
            if stripped != climate_ent:
                candidates.append(stripped)
            candidates.append(str(climate_ent))
        default_base = f"climate.{z_key}"
        if default_base not in candidates:
            candidates.insert(0, default_base)

        for cid in candidates:
            st_obj = self.hass.states.get(cid)
            if not st_obj:
                continue
            raw_t = st_obj.attributes.get("current_temperature")
            if raw_t is not None:
                try:
                    unit = str(st_obj.attributes.get("temperature_unit") or "°F")
                    val_f = self._to_f(float(raw_t), unit)
                    if 30.0 <= val_f <= 110.0:
                        return round(val_f, 2)
                except (ValueError, TypeError):
                    continue
        return fallback_temp

    async def _async_update_telemetry(self, _now: Any = None) -> None:
        """60-second lightweight telemetry loop for MLR derivatives, filter life, and diagnostics."""
        now_local = dt_util.now()
        now_utc = dt_util.as_utc(now_local)
        now_ts = now_utc.timestamp()

        dt_hours = 0.0
        if self._last_telemetry_tick_ts is not None:
            elapsed = now_ts - self._last_telemetry_tick_ts
            if 0.0 < elapsed <= 600.0:
                dt_hours = elapsed / 3600.0
        self._last_telemetry_tick_ts = now_ts

        today_str = now_local.strftime("%Y-%m-%d")
        zones_config = self.entry_data.get(CONF_ZONES, {})
        rooms_config = self.get_configured_rooms()

        # 1. Outdoor Weather & Solar Context for MLR Attributes
        weather_ent = self.options.get(
            CONF_WEATHER_ENTITY,
            self.entry_data.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY),
        )
        t_unit, w_unit = self._weather_units(weather_ent)
        w_temp_raw = self._get_attr(weather_ent, "temperature")
        w_temp = self._to_f(float(w_temp_raw), t_unit) if w_temp_raw is not None else 70.0
        w_rh_raw = self._get_attr(weather_ent, "humidity")
        w_rh = float(w_rh_raw) if w_rh_raw is not None else 50.0
        w_wind_raw = self._get_attr(weather_ent, "wind_speed", 0.0)
        w_wind = self._to_mph(float(w_wind_raw or 0.0), w_unit)
        cloud_cov_raw = self._get_attr(weather_ent, "cloud_coverage")
        if cloud_cov_raw is None:
            cloud_cov_raw = self._read_float("sensor.openweathermap_cloud_coverage", lo=0.0, hi=100.0)
        cloud_cov = float(cloud_cov_raw) if cloud_cov_raw is not None else 50.0

        sun_elev = float(self._get_attr("sun.sun", "elevation", 0.0) or 0.0)
        sun_azim = float(self._get_attr("sun.sun", "azimuth", 180.0) or 180.0)

        solar_power_ent = self.options.get(
            CONF_SOLAR_POWER_ENTITY,
            self.entry_data.get(CONF_SOLAR_POWER_ENTITY, DEFAULT_SOLAR_POWER_ENTITY),
        )
        solcast_power_ent = self.options.get(
            CONF_SOLCAST_POWER_ENTITY,
            self.entry_data.get(CONF_SOLCAST_POWER_ENTITY, DEFAULT_SOLCAST_POWER_ENTITY),
        )
        actual_solar_w = self._power_w(solar_power_ent) or 0.0
        solcast_w = self._power_w(solcast_power_ent) or 0.0

        out_ah = self._calc_absolute_humidity(w_temp, w_rh)
        self.abs_humidity["outside"] = out_ah

        # 2. Open Window Counts (Debounced & Blip-Immune)
        up_win = self.get_zone_open_windows_count("upstairs", now_utc=now_utc)
        down_win = self.get_zone_open_windows_count("downstairs", now_utc=now_utc)
        total_win = up_win + down_win
        self.open_windows_counts = {
            "upstairs": up_win,
            "downstairs": down_win,
            "total": total_win,
        }
        up_w_attrs = self.open_windows_attrs.get("upstairs", {})
        dn_w_attrs = self.open_windows_attrs.get("downstairs", {})
        confirmed_open_all = list(up_w_attrs.get("confirmed_open_sensors", [])) + list(
            dn_w_attrs.get("confirmed_open_sensors", [])
        )
        self.open_windows_attrs["total"] = {
            "confirmed_open_sensors": confirmed_open_all,
            "pending_debounce_sensors": list(up_w_attrs.get("pending_debounce_sensors", []))
            + list(dn_w_attrs.get("pending_debounce_sensors", [])),
            "unavailable_sensors": list(up_w_attrs.get("unavailable_sensors", []))
            + list(dn_w_attrs.get("unavailable_sensors", [])),
            "raw_open_count": int(up_w_attrs.get("raw_open_count", 0))
            + int(dn_w_attrs.get("raw_open_count", 0)),
        }
        confirmed_open_set = set(confirmed_open_all)

        # 3. Per-Room Temperatures & 10-Minute Rolling Derivatives
        room_temps: dict[str, float | None] = {}
        room_rates: dict[str, float] = {}
        room_open_win: dict[str, int] = {}

        for r_slug, r_conf in rooms_config.items():
            if not isinstance(r_conf, dict):
                continue
            r_temp_ent = r_conf.get(CONF_ROOM_TEMP_SENSOR)
            r_unit = self._get_attr(r_temp_ent, "unit_of_measurement", "°F")
            r_raw = self._read_float(r_temp_ent, lo=-10.0, hi=120.0)
            r_temp = self._to_f(r_raw, r_unit) if r_raw is not None else None
            room_temps[r_slug] = r_temp
            room_rates[r_slug] = self._record_and_calc_rate(f"room_{r_slug}", r_temp, now_ts)

            r_win_list = r_conf.get(CONF_ROOM_WINDOW_SENSORS, [])
            if r_win_list:
                r_open = sum(
                    1
                    for w_e in r_win_list
                    if (
                        w_e in confirmed_open_set
                        or (
                            w_e not in self.window_tracker._contacts
                            and self.hass.states.get(w_e)
                            and self.hass.states.get(w_e).state == "on"
                        )
                    )
                )
            else:
                r_zone = str(r_conf.get(CONF_ROOM_ZONE, "downstairs"))
                r_open = up_win if r_zone == "upstairs" else down_win
            room_open_win[r_slug] = r_open

        # 4. Per-Zone Runtime, Coasting, Fault-Tolerant Rates & Diagnostics
        zone_actions: dict[str, str] = {}
        zone_in_temps: dict[str, float | None] = {}
        zone_ah_rates: dict[str, float] = {}

        for z_key in ("upstairs", "downstairs"):
            z_conf = zones_config.get(z_key, {})
            climate_ent = z_conf.get(CONF_CLIMATE_ENTITY, f"climate.{z_key}_hk")
            temp_ent = z_conf.get(CONF_TEMP_SENSOR, f"sensor.{z_key}_average_temperature_smoothed")
            hum_ent = z_conf.get(CONF_HUMIDITY_SENSOR, f"sensor.{z_key}_average_humidity_smoothed")

            t_unit_z = self._get_attr(temp_ent, "unit_of_measurement", "°F")
            in_temp_raw = self._read_float(temp_ent, lo=-10.0, hi=120.0)
            in_temp = self._to_f(in_temp_raw, t_unit_z) if in_temp_raw is not None else None
            in_rh = self._read_float(hum_ent, lo=1.0, hi=100.0)
            zone_in_temps[z_key] = in_temp

            z_ah = self._calc_absolute_humidity(in_temp, in_rh)
            self.abs_humidity[z_key] = z_ah
            zone_ah_rates[z_key] = self._record_and_calc_rate(f"zone_ah_{z_key}", z_ah, now_ts)
            fallback_z_rate = self._record_and_calc_rate(f"zone_temp_{z_key}", in_temp, now_ts)

            # Fault-Tolerant Zone Temperature Rate across active room sensors
            candidate_rates = [
                room_rates[slug]
                for slug, rc in rooms_config.items()
                if isinstance(rc, dict)
                and rc.get(CONF_ROOM_ZONE) == z_key
                and rc.get(CONF_ROOM_INCLUDE_IN_AVG, True)
                and room_temps.get(slug) is not None
                and abs(room_rates.get(slug, 0.0)) < 15.0
            ]
            non_zero_rates = [r for r in candidate_rates if abs(r) > 0.001]
            if non_zero_rates:
                ft_rate = round(sum(non_zero_rates) / len(non_zero_rates), 2)
            elif candidate_rates:
                ft_rate = round(sum(candidate_rates) / len(candidate_rates), 2)
            else:
                ft_rate = round(fallback_z_rate, 2)
            self.zone_fault_tolerant_rates[z_key] = ft_rate

            # HVAC Action & Runtime Accumulation
            c_st_obj = self.hass.states.get(climate_ent)
            c_attrs = c_st_obj.attributes if c_st_obj else {}
            hvac_action = str(c_attrs.get("hvac_action", "idle") or "idle").lower()
            fan_mode = str(c_attrs.get("fan_mode", "auto") or "auto").lower()
            zone_actions[z_key] = hvac_action

            # Read thermostat's own active-profile unsmoothed temperature & 10m rate
            tstat_temp = self._read_thermostat_temp_f(z_key, climate_ent, in_temp)
            tstat_rate = self._record_and_calc_rate(f"tstat_temp_{z_key}", tstat_temp, now_ts)
            eval_temp = tstat_temp if tstat_temp is not None else in_temp
            eval_rate = round(tstat_rate, 2) if abs(tstat_rate) > 0.05 else ft_rate

            zt = self.telemetry_state[z_key]
            if zt.get("today_date") != today_str:
                if zt.get("today_date"):
                    zt["heating_today"] = 0.0
                    zt["cooling_today"] = 0.0
                    zt["fan_today"] = 0.0
                zt["today_date"] = today_str

            prev_action = str(zt.get("last_action", "idle"))
            is_active_now = hvac_action in ("heating", "cooling")
            was_active = prev_action in ("heating", "cooling")

            target_sp = c_attrs.get("temperature")
            if target_sp is None:
                target_sp = c_attrs.get("target_temp_low") if hvac_action == "heating" else c_attrs.get("target_temp_high")
            target_f = float(target_sp) if target_sp is not None else None

            if is_active_now and not was_active:
                zt["active_since_ts"] = now_utc.isoformat()
                cycles = list(zt.get("cycle_timestamps", []))
                cycles.append(now_utc.isoformat())
                zt["cycle_timestamps"] = cycles
                zt["active_coast_tracker"] = None
                if eval_temp is not None and target_f is not None:
                    raw_swing = (target_f - eval_temp) if hvac_action == "heating" else (eval_temp - target_f)
                    if 0.1 <= raw_swing <= 1.2:
                        s_key = "swing_heat_samples" if hvac_action == "heating" else "swing_cool_samples"
                        zt[s_key] = list(zt.get(s_key, []))[-19:] + [round(raw_swing, 2)]
            elif not is_active_now:
                zt["active_since_ts"] = None
                if was_active and eval_temp is not None:
                    zt["active_coast_tracker"] = {
                        "mode": prev_action,
                        "stop_ts": now_utc.isoformat(),
                        "stop_temp": round(eval_temp, 2),
                        "peak_temp": round(eval_temp, 2),
                    }

            if is_active_now or was_active:
                zt["last_active_ts"] = now_utc.isoformat()
            if is_active_now:
                zt["last_hvac_mode"] = hvac_action
            zt["last_action"] = hvac_action

            # Prune cycle_timestamps to rolling 60-minute window
            valid_cycles: list[str] = []
            for ts_s in zt.get("cycle_timestamps", []):
                dt_c = dt_util.parse_datetime(str(ts_s))
                if dt_c and (now_utc - dt_c).total_seconds() <= 3600.0:
                    valid_cycles.append(ts_s)
            zt["cycle_timestamps"] = valid_cycles

            if dt_hours > 0.0:
                if hvac_action == "heating":
                    zt["heating_today"] = round(float(zt.get("heating_today", 0.0)) + dt_hours, 4)
                    zt["filter_runtime_hours"] = round(float(zt.get("filter_runtime_hours", 0.0)) + dt_hours, 4)
                elif hvac_action == "cooling":
                    zt["cooling_today"] = round(float(zt.get("cooling_today", 0.0)) + dt_hours, 4)
                    zt["filter_runtime_hours"] = round(float(zt.get("filter_runtime_hours", 0.0)) + dt_hours, 4)
                elif hvac_action == "fan" or fan_mode == "on":
                    zt["fan_today"] = round(float(zt.get("fan_today", 0.0)) + dt_hours, 4)
                    zt["filter_runtime_hours"] = round(float(zt.get("filter_runtime_hours", 0.0)) + dt_hours, 4)

            # Coasting evaluation (90-minute post-HVAC thermal inertia gate with rebound override & learned coast)
            last_act_dt = dt_util.parse_datetime(str(zt.get("last_active_ts", "")))
            last_mode = str(zt.get("last_hvac_mode", "idle"))
            is_coasting = False
            if not is_active_now and last_act_dt is not None:
                since_sec = (now_utc - last_act_dt).total_seconds()
                if 0.0 <= since_sec < 5400.0:
                    if last_mode == "cooling" and ft_rate >= 0.0:
                        is_coasting = False
                    elif last_mode == "heating" and ft_rate <= 0.0:
                        is_coasting = False
                    else:
                        is_coasting = True

            # Update active post-shutoff coast tracker
            coast_tracker = zt.get("active_coast_tracker")
            if not is_active_now and isinstance(coast_tracker, dict):
                t_mode = str(coast_tracker.get("mode", "cooling"))
                stop_dt = dt_util.parse_datetime(str(coast_tracker.get("stop_ts", "")))
                elapsed_coast_s = (now_utc - stop_dt).total_seconds() if stop_dt else 9999.0
                if eval_temp is not None:
                    prev_peak = float(coast_tracker.get("peak_temp", eval_temp))
                    if t_mode == "heating" and eval_temp > prev_peak:
                        coast_tracker["peak_temp"] = round(eval_temp, 2)
                    elif t_mode == "cooling" and eval_temp < prev_peak:
                        coast_tracker["peak_temp"] = round(eval_temp, 2)
                rebounded = (t_mode == "cooling" and eval_rate >= 0.0) or (
                    t_mode == "heating" and eval_rate <= 0.0
                )
                if rebounded or elapsed_coast_s >= 5400.0:
                    if elapsed_coast_s >= 120.0:
                        coast_mag = abs(
                            float(coast_tracker.get("peak_temp", 0.0))
                            - float(coast_tracker.get("stop_temp", 0.0))
                        )
                        coast_mins = min(90.0, max(2.0, elapsed_coast_s / 60.0))
                        c_key = "coast_heat_samples" if t_mode == "heating" else "coast_cool_samples"
                        if 0.0 <= coast_mag <= 4.0:
                            zt[c_key] = list(zt.get(c_key, []))[-19:] + [round(coast_mag, 2)]
                        zt["coast_duration_samples"] = list(
                            zt.get("coast_duration_samples", [])
                        )[-19:] + [round(coast_mins, 1)]
                    zt["active_coast_tracker"] = None

            sh_samples = [float(s) for s in zt.get("swing_heat_samples", []) if 0.1 <= float(s) <= 1.2]
            sc_samples = [float(s) for s in zt.get("swing_cool_samples", []) if 0.1 <= float(s) <= 1.2]
            zt["swing_heat_samples"] = sh_samples
            zt["swing_cool_samples"] = sc_samples
            ch_samples = zt.get("coast_heat_samples", [])
            cc_samples = zt.get("coast_cool_samples", [])
            cd_samples = zt.get("coast_duration_samples", [])
            learned_swing_heat_f = round(sum(sh_samples) / len(sh_samples), 2) if sh_samples else 0.5
            learned_swing_cool_f = round(sum(sc_samples) / len(sc_samples), 2) if sc_samples else 0.5
            active_coast_samples = (
                ch_samples if (hvac_action == "heating" or last_mode == "heating") else cc_samples
            )
            learned_coast_f = (
                round(sum(active_coast_samples) / len(active_coast_samples), 2)
                if active_coast_samples
                else 0.3
            )
            learned_coast_minutes = (
                round(sum(cd_samples) / len(cd_samples), 1) if cd_samples else 20.0
            )

            # Physics-Based Time-to-Target & Health Diagnostics (uses thermostat active-profile temp)
            active_run_mins = 0.0
            if zt.get("active_since_ts"):
                act_dt = dt_util.parse_datetime(str(zt["active_since_ts"]))
                if act_dt:
                    active_run_mins = max(0.0, (now_utc - act_dt).total_seconds() / 60.0)

            c_mode_str = str(c_st_obj.state if c_st_obj else "off").lower()
            use_heat_model = hvac_action == "heating" or (hvac_action != "cooling" and c_mode_str == "heat")
            model_key = f"{z_key}_temp_profile_hvac_heat" if use_heat_model else f"{z_key}_temp_profile_hvac_cool"
            rate_model = self.models.get(model_key)
            pred_rate = 0.0
            if rate_model and eval_temp is not None and w_temp is not None:
                delta_t = w_temp - eval_temp
                pred_rate = float(rate_model.slope * delta_t + rate_model.intercept)
            if is_active_now and active_run_mins >= 8.0 and abs(eval_rate) > 0.1:
                if (hvac_action == "heating" and eval_rate > 0.1) or (hvac_action == "cooling" and eval_rate < -0.1):
                    pred_rate = 0.5 * pred_rate + 0.5 * eval_rate if pred_rate != 0.0 else eval_rate

            time_to_temp = 0
            if is_active_now and eval_temp is not None and target_f is not None:
                if hvac_action == "heating" and (target_f - eval_temp) > 0.05:
                    eff_r = max(0.4, pred_rate if pred_rate > 0.2 else 2.5)
                    time_to_temp = max(1, min(240, int(round(((target_f - eval_temp) / eff_r) * 60.0))))
                elif hvac_action == "cooling" and (eval_temp - target_f) > 0.05:
                    eff_r = max(0.4, abs(pred_rate) if pred_rate < -0.2 else 1.8)
                    time_to_temp = max(1, min(240, int(round(((eval_temp - target_f) / eff_r) * 60.0))))

            cap_key = CONF_FILTER_CAPACITY_UPSTAIRS if z_key == "upstairs" else CONF_FILTER_CAPACITY_DOWNSTAIRS
            cap_h = (
                self.comfort_settings.get(cap_key)
                or self._read_float(f"number.hvac_filter_life_{z_key}", lo=10.0, hi=2000.0)
                or self._read_float(f"input_number.hvac_filter_life_{z_key}", lo=10.0, hi=2000.0)
                or float(self.options.get(cap_key, DEFAULT_FILTER_CAPACITY_HOURS))
            )
            rem_h = round(float(cap_h) - float(zt.get("filter_runtime_hours", 0.0)), 1)

            cph = len(valid_cycles)
            stall_reason: str | None = None
            if cph > 4:
                health_status = "Short Cycling"
                stall_reason = f"Compressor started {cph} times in the last 60 minutes (>4 CPH limit)."
            elif is_active_now and active_run_mins >= 20.0 and (
                (hvac_action == "cooling" and ft_rate > -0.1)
                or (hvac_action == "heating" and ft_rate < 0.1)
            ):
                health_status = "Thermal Stall"
                stall_reason = f"Active {hvac_action} for {int(round(active_run_mins))}m with insufficient thermal rate ({ft_rate:+.2f}°F/h)."
            elif rem_h <= 0.0:
                health_status = "Filter Overdue"
                stall_reason = f"HVAC filter runtime exceeded capacity ({abs(rem_h):.1f}h overdue)."
            else:
                health_status = "Optimal"

            # Off-Target Idle Comfort Stall Check (only when otherwise Optimal)
            if is_active_now:
                zt["off_target_idle_since"] = None
            else:
                z_open_now = up_win if z_key == "upstairs" else down_win
                window_eco_now = (
                    self._get_val(
                        z_conf.get(
                            CONF_WINDOW_ECO_BOOLEAN,
                            f"input_boolean.eco_mode_request_{z_key}_hvac_advisor",
                        ),
                        "off",
                    )
                    == "on"
                )
                circ_eco_now = (
                    self._get_val(z_conf.get(CONF_CIRCULATION_ECO_BOOLEAN), "off") == "on"
                )
                floor_eco_now = self._get_val(f"input_boolean.eco_mode_{z_key}", "off") == "on"
                eco_active_now = window_eco_now or circ_eco_now or floor_eco_now
                presence_ent_now = self.options.get(
                    CONF_PRESENCE_ENTITY,
                    self.entry_data.get(CONF_PRESENCE_ENTITY, DEFAULT_PRESENCE_ENTITY),
                )
                home_mode_ent_now = self.options.get(
                    CONF_HOME_MODE_ENTITY,
                    self.entry_data.get(CONF_HOME_MODE_ENTITY, DEFAULT_HOME_MODE_ENTITY),
                )
                someone_home_now = (self._get_val(presence_ent_now, "on") == "on") and (
                    str(self._get_val(home_mode_ent_now, "Home") or "Home").lower()
                    not in {"away", "vacation"}
                )
                c_mode_now = str(c_st_obj.state if c_st_obj else "off").lower()
                comfort_min_now = float(
                    self.comfort_settings.get(CONF_COMFORT_TEMP_MIN, DEFAULT_COMFORT_TEMP_MIN)
                )
                comfort_max_now = float(
                    self.comfort_settings.get(CONF_COMFORT_TEMP_MAX, DEFAULT_COMFORT_TEMP_MAX)
                )
                heat_ref = (
                    target_f
                    if (target_f is not None and c_mode_now in ("heat", "heat_cool", "auto"))
                    else comfort_min_now
                )
                cool_ref = (
                    target_f
                    if (target_f is not None and c_mode_now in ("cool", "heat_cool", "auto"))
                    else comfort_max_now
                )
                off_target_msg: str | None = None
                if eval_temp is not None and z_open_now == 0 and not eco_active_now and someone_home_now:
                    if eval_temp < (heat_ref - learned_swing_heat_f - 1.0):
                        off_target_msg = f"Zone temperature ({eval_temp:.1f}°F) has remained below target ({heat_ref:.1f}°F) with HVAC idle"
                    elif eval_temp > (cool_ref + learned_swing_cool_f + 1.0):
                        off_target_msg = f"Zone temperature ({eval_temp:.1f}°F) has remained above target ({cool_ref:.1f}°F) with HVAC idle"

                if off_target_msg:
                    if not zt.get("off_target_idle_since"):
                        zt["off_target_idle_since"] = now_utc.isoformat()
                    idle_dt = dt_util.parse_datetime(str(zt["off_target_idle_since"]))
                    idle_mins = (now_utc - idle_dt).total_seconds() / 60.0 if idle_dt else 0.0
                    if idle_mins >= 90.0 and health_status == "Optimal":
                        health_status = "Idle Comfort Stall"
                        stall_reason = f"{off_target_msg} for {int(round(idle_mins))}m."
                else:
                    zt["off_target_idle_since"] = None

            prev_ttt_attrs = self.zone_diagnostics.get(z_key, {}).get("time_to_temp_attrs", {})
            self.zone_diagnostics[z_key] = {
                "time_to_temp": time_to_temp,
                "time_to_temp_attrs": {
                    "formatted": f"~{time_to_temp}m" if time_to_temp > 0 else "Idle",
                    "hvac_action": hvac_action,
                    "current_temperature": eval_temp,
                    "zone_average_temperature": in_temp,
                    "target_temperature": target_f,
                    "predicted_rate_f_hr": round(pred_rate, 2),
                    "observed_rate_f_hr": eval_rate,
                    "active_runtime_minutes": round(active_run_mins, 1),
                    "learned_swing_heat_f": learned_swing_heat_f,
                    "learned_swing_cool_f": learned_swing_cool_f,
                    "learned_coast_f": learned_coast_f,
                    "learned_coast_minutes": learned_coast_minutes,
                    "pre_condition_needed": prev_ttt_attrs.get("pre_condition_needed", False),
                    "pre_condition_mode": prev_ttt_attrs.get("pre_condition_mode"),
                    "pre_condition_start_time": prev_ttt_attrs.get("pre_condition_start_time"),
                    "pre_condition_lead_minutes": prev_ttt_attrs.get("pre_condition_lead_minutes"),
                    "predicted_breach_time": prev_ttt_attrs.get("predicted_breach_time"),
                    "next_hvac_cycle_mode": None if hvac_action in ("heating", "cooling") else prev_ttt_attrs.get("next_hvac_cycle_mode"),
                    "next_hvac_cycle_time": None if hvac_action in ("heating", "cooling") else prev_ttt_attrs.get("next_hvac_cycle_time"),
                    "next_hvac_cycle_minutes": None if hvac_action in ("heating", "cooling") else prev_ttt_attrs.get("next_hvac_cycle_minutes"),
                    "next_hvac_trigger_temp": None if hvac_action in ("heating", "cooling") else prev_ttt_attrs.get("next_hvac_trigger_temp"),
                },
                "health_status": health_status,
                "health_attrs": {
                    "cycles_per_hour": cph,
                    "active_runtime_minutes": round(active_run_mins, 1),
                    "observed_rate_f_hr": ft_rate,
                    "filter_life_remaining_hours": rem_h,
                    "is_coasting": is_coasting,
                    "stall_reason": stall_reason,
                },
                "cycles_per_hour": cph,
                "is_coasting": is_coasting,
                "filter_capacity_hours": cap_h,
                "filter_remaining_hours": rem_h,
            }

        # 5. Populate the 16 Canonical Zone MLR Rate Sensors & Per-Room Passive Rate Sensors
        both_idle = (
            zone_actions.get("upstairs") not in ("heating", "cooling")
            and zone_actions.get("downstairs") not in ("heating", "cooling")
        )

        for z_key in ("upstairs", "downstairs"):
            ft_rate = self.zone_fault_tolerant_rates[z_key]
            ah_rate = zone_ah_rates.get(z_key, 0.0)
            in_temp = zone_in_temps.get(z_key)
            in_ah = self.abs_humidity.get(z_key)
            z_open = up_win if z_key == "upstairs" else down_win
            z_action = zone_actions.get(z_key, "idle")
            z_coasting = bool(self.zone_diagnostics[z_key].get("is_coasting", False))

            common_attrs = {
                "outside_temperature": round(w_temp, 2) if w_temp is not None else None,
                "inside_temperature": round(in_temp, 2) if in_temp is not None else None,
                "outside_ah": out_ah,
                "inside_ah": in_ah,
                "wind_speed": round(w_wind, 2),
                "cloud_coverage": round(cloud_cov, 1),
                "sun_elevation": round(sun_elev, 2),
                "sun_azimuth": round(sun_azim, 2),
                "solcast_power": round(solcast_w, 1),
                "actual_solar_power": round(actual_solar_w, 1),
            }

            for obj_id_tpl, _friendly, _unit, _icon, mode_type in ZONE_MLR_RATE_SPECS:
                full_key = obj_id_tpl.format(zone=z_key)
                self.zone_mlr_attrs[full_key] = dict(common_attrs)
                val_out: float | None = None
                if mode_type == "temp_closed":
                    if both_idle and not z_coasting and total_win == 0 and abs(ft_rate) < 10.0:
                        val_out = round(ft_rate, 2)
                elif mode_type == "temp_open":
                    if both_idle and not z_coasting and z_open > 0 and abs(ft_rate) < 10.0:
                        val_out = round(ft_rate, 2)
                elif mode_type == "temp_heat":
                    if z_action == "heating" and total_win == 0 and abs(ft_rate) < 15.0:
                        val_out = round(ft_rate, 2)
                elif mode_type == "temp_cool":
                    if z_action == "cooling" and total_win == 0 and abs(ft_rate) < 15.0:
                        val_out = round(ft_rate, 2)
                elif mode_type == "hum_closed":
                    if both_idle and not z_coasting and total_win == 0 and abs(ah_rate) < 5.0:
                        val_out = round(ah_rate, 3)
                elif mode_type == "hum_open":
                    if both_idle and not z_coasting and z_open > 0 and abs(ah_rate) < 5.0:
                        val_out = round(ah_rate, 3)
                elif mode_type == "hum_heat":
                    if z_action == "heating" and total_win == 0 and abs(ah_rate) < 5.0:
                        val_out = round(ah_rate, 3)
                elif mode_type == "hum_cool":
                    if z_action == "cooling" and total_win == 0 and abs(ah_rate) < 5.0:
                        val_out = round(ah_rate, 3)
                self.zone_mlr_rates[full_key] = val_out

                # Phase 7: Zero-InfluxDB Online Rolling OLS + EWMA Fallback Learner
                # Only updates when InfluxDB has not yet provided a trained model for this regime
                if (
                    val_out is not None
                    and in_temp is not None
                    and w_temp is not None
                    and mode_type in ("temp_closed", "temp_open", "temp_heat", "temp_cool")
                ):
                    metric_map = {
                        "temp_closed": METRIC_TEMP_CLOSED,
                        "temp_open": METRIC_TEMP_OPEN,
                        "temp_heat": METRIC_TEMP_HEAT,
                        "temp_cool": METRIC_TEMP_COOL,
                    }
                    m_key = f"{z_key}_{metric_map[mode_type]}"
                    ols_buf = self._online_ols_buffers.setdefault(m_key, deque(maxlen=30))
                    ols_buf.append((float(w_temp - in_temp), float(val_out)))
                    existing_m = self.models.get(m_key)
                    has_influx_model = existing_m is not None and (
                        existing_m.mlr_data_points > 0 or len(existing_m.x_data) > 0
                    )
                    if not has_influx_model and len(ols_buf) >= 6:
                        slope_o, intercept_o, r_sq = fit_online_rolling_ols(
                            list(ols_buf), regime=mode_type
                        )
                        if slope_o is not None and intercept_o is not None:
                            if existing_m is not None and existing_m.model_type == "online_ewma":
                                alpha = 0.25 if r_sq >= 0.7 else (0.15 if r_sq >= 0.3 else 0.08)
                                new_s = round((1.0 - alpha) * existing_m.slope + alpha * slope_o, 4)
                                new_b = round(
                                    (1.0 - alpha) * existing_m.intercept + alpha * intercept_o, 4
                                )
                            else:
                                new_s, new_b = slope_o, intercept_o
                            self.models[m_key] = RateModel(
                                model_type="online_ewma",
                                slope=new_s,
                                intercept=new_b,
                                equation=f"y = {new_s:.4f}x + {new_b:.2f} (Online EWMA, R²={r_sq:.2f})",
                                last_updated=now_local.strftime("%Y-%m-%d %H:%M:%S"),
                            )

        # Per-Room Passive Heat Loss/Gain Rates
        for r_slug, r_conf in rooms_config.items():
            if not isinstance(r_conf, dict) or not r_conf.get(CONF_ROOM_ENABLE_TELEMETRY, True):
                continue
            r_zone = str(r_conf.get(CONF_ROOM_ZONE, "downstairs"))
            r_temp = room_temps.get(r_slug)
            r_rate = room_rates.get(r_slug, 0.0)
            r_open = room_open_win.get(r_slug, 0)
            r_coasting = bool(self.zone_diagnostics.get(r_zone, {}).get("is_coasting", False))

            r_attrs = {
                "outside_temperature": round(w_temp, 2) if w_temp is not None else None,
                "inside_temperature": round(r_temp, 2) if r_temp is not None else None,
                "wind_speed": round(w_wind, 2),
                "cloud_coverage": round(cloud_cov, 1),
                "sun_elevation": round(sun_elev, 2),
                "sun_azimuth": round(sun_azim, 2),
                "solcast_power": round(solcast_w, 1),
                "actual_solar_power": round(actual_solar_w, 1),
            }
            closed_key = f"{r_slug}_heat_loss_gain_windows_closed"
            open_key = f"{r_slug}_heat_loss_gain_windows_open"
            self.room_mlr_attrs[closed_key] = dict(r_attrs)
            self.room_mlr_attrs[open_key] = dict(r_attrs)

            if both_idle and not r_coasting and total_win == 0 and r_temp is not None and abs(r_rate) < 10.0:
                self.room_mlr_rates[closed_key] = round(r_rate, 2)
            else:
                self.room_mlr_rates[closed_key] = None

            if both_idle and not r_coasting and r_open > 0 and r_temp is not None and abs(r_rate) < 10.0:
                self.room_mlr_rates[open_key] = round(r_rate, 2)
            else:
                self.room_mlr_rates[open_key] = None

        self._telemetry_store.async_delay_save(lambda: self.telemetry_state, 30)
        if _now is not None:
            self.async_update_listeners()

    def async_setup_listeners(self) -> None:
        """Sets up reactive state listeners on window contact sensors, occupancy, helpers, and 60s telemetry tick."""
        from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

        entities_to_track: list[str] = []
        zones_config = self.entry_data.get(CONF_ZONES, {})
        for z_key, z_conf in zones_config.items():
            win_ent = z_conf.get(CONF_OPEN_WINDOWS_SENSOR)
            if (
                win_ent
                and win_ent not in entities_to_track
                and win_ent
                not in (
                    "sensor.upstairs_open_windows_count",
                    "sensor.downstairs_open_windows_count",
                )
            ):
                entities_to_track.append(win_ent)
            for bin_win in self.get_zone_window_sensors(z_key):
                if bin_win and bin_win not in entities_to_track:
                    entities_to_track.append(bin_win)

        # Track presence and home mode for instantaneous updates upon arriving home
        home_mode_ent = self.options.get(
            CONF_HOME_MODE_ENTITY,
            self.entry_data.get(CONF_HOME_MODE_ENTITY, DEFAULT_HOME_MODE_ENTITY),
        )
        presence_ent = self.options.get(
            CONF_PRESENCE_ENTITY,
            self.entry_data.get(CONF_PRESENCE_ENTITY, DEFAULT_PRESENCE_ENTITY),
        )
        for ent in [home_mode_ent, presence_ent]:
            if ent and isinstance(ent, str) and "." in ent and ent not in entities_to_track:
                entities_to_track.append(ent)

        # Track home state and profile helpers for instant reactive phase updates
        for phase_helper in ["input_select.home_state", "input_text.hvac_active_profile"]:
            if phase_helper not in entities_to_track:
                entities_to_track.append(phase_helper)

        # Track transition offset entity for reactive updates
        offset_ent = self.options.get(
            CONF_HVAC_TRANSITION_OFFSET,
            self.entry_data.get(CONF_HVAC_TRANSITION_OFFSET, DEFAULT_HVAC_TRANSITION_OFFSET_ENTITY),
        )
        if offset_ent and isinstance(offset_ent, str) and "." in offset_ent and offset_ent not in entities_to_track:
            entities_to_track.append(offset_ent)

        if entities_to_track:
            async def _handle_tracked_state_change(event: Any) -> None:
                old_state = event.data.get("old_state")
                new_state = event.data.get("new_state")
                if not new_state or (old_state and old_state.state == new_state.state):
                    return
                _LOGGER.debug(
                    "Reactive trigger from tracked entity %s; refreshing climate plan.",
                    event.data.get("entity_id"),
                )
                await self._async_update_telemetry()
                await self.async_request_refresh()

            self._unsub_track = async_track_state_change_event(
                self.hass, entities_to_track, _handle_tracked_state_change
            )

        # 60-second lightweight telemetry tick for rolling derivatives, filter runtime, and diagnostics
        self._unsub_telemetry = async_track_time_interval(
            self.hass,
            self._async_update_telemetry,
            datetime.timedelta(seconds=60),
        )

        # Reactive listener for manual circulation toggle switches
        circ_bools: list[str] = []
        for z_key, z_conf in zones_config.items():
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
                async with self._circ_lock:
                    for z_k, z_c in zones_config.items():
                        if z_c.get(CONF_CIRCULATION_ACTIVE_BOOLEAN) == ent_id:
                            climate_e = z_c.get(CONF_CLIMATE_ENTITY)
                            eco_e = z_c.get(CONF_CIRCULATION_ECO_BOOLEAN)
                            temp_e = z_c.get(CONF_TEMP_SENSOR)
                            now_u = dt_util.utcnow()
                            mem = self.circulation_manager.get_zone_memory(z_k)
                            is_running = mem.get("start_time") is not None

                            if new_state.state == "on":
                                if not is_running:
                                    _LOGGER.info("Passable Smart Climate: Manual toggle ON for %s circulation.", z_k)
                                    if climate_e:
                                        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_e, "fan_mode": "on"})
                                    if eco_e:
                                        await self.hass.services.async_call("input_boolean", "turn_on", {"entity_id": eco_e})
                                    t_unit_c = self._get_attr(temp_e, "unit_of_measurement", "°F")
                                    in_t_raw = self._read_float(temp_e, lo=-10.0, hi=110.0)
                                    in_t = self._to_f(in_t_raw, t_unit_c) if in_t_raw is not None else 70.0
                                    c_st_m = self._get_val(climate_e, "cool")
                                    c_attrs_m = self.hass.states.get(climate_e).attributes if (climate_e and self.hass.states.get(climate_e)) else {}
                                    eff_m = self._effective_hvac_mode(c_st_m, c_attrs_m, 70.0, in_t)
                                    mem["lockout_until"] = None
                                    mem["start_time"] = now_u
                                    mem["start_temp"] = in_t
                                    mem["mode"] = "heat" if eff_m == "heat" else "cool"
                                    self._learning_store.async_delay_save(self.circulation_manager.export_learning_data, 5)
                                    await self.async_request_refresh()
                            elif new_state.state == "off":
                                if is_running:
                                    _LOGGER.info("Passable Smart Climate: Manual toggle OFF for %s circulation.", z_k)
                                    if climate_e:
                                        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_e, "fan_mode": "auto"})
                                    if eco_e:
                                        await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": eco_e})
                                    lockout_h = float(self.options.get(CONF_CIRC_LOCKOUT_HOURS, DEFAULT_CIRC_LOCKOUT_HOURS))
                                    mem["lockout_until"] = now_u + datetime.timedelta(hours=lockout_h)
                                    mem["start_time"] = None
                                    mem["start_temp"] = None
                                    mem["mode"] = None
                                    self._learning_store.async_delay_save(self.circulation_manager.export_learning_data, 5)
                                    await self.async_request_refresh()
                                else:
                                    if climate_e:
                                        await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_e, "fan_mode": "auto"})
                                    if eco_e:
                                        await self.hass.services.async_call("input_boolean", "turn_off", {"entity_id": eco_e})

            self._unsub_circ = async_track_state_change_event(
                self.hass, circ_bools, _handle_circ_toggle
            )

    def async_unload(self) -> None:
        """Unsubscribe all listeners."""
        if hasattr(self, "_unsub_track") and self._unsub_track:
            self._unsub_track()
            self._unsub_track = None
        if hasattr(self, "_unsub_telemetry") and self._unsub_telemetry:
            self._unsub_telemetry()
            self._unsub_telemetry = None
        if hasattr(self, "_unsub_circ") and self._unsub_circ:
            self._unsub_circ()
            self._unsub_circ = None

    async def async_retrain_models(self) -> None:
        """Executes full historical regression training in a background executor thread."""
        if self._train_lock.locked():
            _LOGGER.info("Model retraining already in progress; skipping duplicate request.")
            return
        async with self._train_lock:
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
        if st is None or st.state in _INVALID_STATES:
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
        """Runs the complete environmental arbitration, forward simulation, and circulation manager with fail-safe error handling."""
        try:
            await self._async_update_telemetry()
            return await self._async_evaluate()
        except Exception as err:
            _LOGGER.exception("Evaluation failed; publishing fail-safe 'Close Windows'")
            self.last_recommendation = CLOSE
            self.last_details = f"Advisor degraded: {err}"
            await self._async_sync_overview(CLOSE)
            raise UpdateFailed(str(err)) from err

    async def _async_evaluate(self) -> dict[str, Any]:
        """Core pure calculation and arbitration routine."""
        now = dt_util.now()
        now_utc = dt_util.as_utc(now)

        weather_ent = self.options.get(
            CONF_WEATHER_ENTITY,
            self.entry_data.get(CONF_WEATHER_ENTITY, DEFAULT_WEATHER_ENTITY),
        )
        home_mode_ent = self.options.get(
            CONF_HOME_MODE_ENTITY,
            self.entry_data.get(CONF_HOME_MODE_ENTITY, DEFAULT_HOME_MODE_ENTITY),
        )
        presence_ent = self.options.get(
            CONF_PRESENCE_ENTITY,
            self.entry_data.get(CONF_PRESENCE_ENTITY, DEFAULT_PRESENCE_ENTITY),
        )

        # 1. Weather Telemetry & Normalized Units
        t_unit, w_unit = self._weather_units(weather_ent)
        w_state = self._get_val(weather_ent)
        w_temp_raw = self._get_attr(weather_ent, "temperature")
        w_temp = self._to_f(float(w_temp_raw), t_unit) if w_temp_raw is not None else 70.0
        w_wind_raw = self._get_attr(weather_ent, "wind_speed", 0.0)
        w_wind = self._to_mph(float(w_wind_raw or 0.0), w_unit)
        w_gust_raw = self._get_attr(weather_ent, "wind_gust", 0.0)
        w_gust = self._to_mph(float(w_gust_raw or 0.0), w_unit)
        w_rh_raw = self._get_attr(weather_ent, "humidity")
        w_rh = float(w_rh_raw) if w_rh_raw is not None else None
        w_clouds_raw = self._get_attr(weather_ent, "cloud_coverage")
        if w_clouds_raw is None:
            w_clouds_raw = self._read_float("sensor.openweathermap_cloud_coverage", lo=0.0, hi=100.0)
        w_clouds = max(0.0, min(100.0, float(w_clouds_raw))) if w_clouds_raw is not None else 50.0
        weather_telemetry_degraded = (w_state is None or w_temp_raw is None or w_rh_raw is None)

        # Track wet ground / storm quiet period
        if w_state and str(w_state).lower() in BAD_WEATHER_CONDITIONS:
            self._last_bad_weather_time = now_utc.isoformat()

        # Forecast Extraction
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

        obs_anchor = {
            "temperature": w_temp,
            "humidity": w_rh if w_rh is not None else 50.0,
            "wind_speed": w_wind,
            "condition": w_state or "cloudy",
            "cloud_coverage": w_clouds,
        }
        sanitized_forecast = self._sanitize_forecast(
            forecast_list,
            now_utc,
            t_unit,
            w_unit,
            obs=obs_anchor if (forecast_list and not weather_telemetry_degraded) else None,
        )
        if len(sanitized_forecast) <= 1:
            sanitized_forecast = []

        # Solar Coordinates
        lat = float(getattr(self.hass.config, "latitude", 0.0) or 0.0)
        lon = float(getattr(self.hass.config, "longitude", 0.0) or 0.0)

        # Environmental & Occupancy Veto Check
        home_mode = self._get_val(home_mode_ent, "Home")
        someone_is_home = (self._get_val(presence_ent, "on") == "on")
        is_unoccupied = (someone_is_home is False) or ((home_mode or "").lower() in {"away", "vacation"})

        lookahead_mins = float(self.options.get(CONF_FORECAST_LOOKAHEAD_MINUTES, DEFAULT_FORECAST_LOOKAHEAD_MINUTES))
        lookahead_hours = max(1, int(round(lookahead_mins / 60.0)))
        scan_forecast = sanitized_forecast[1 : lookahead_hours + 1] if len(sanitized_forecast) > 1 else []
        precip_prob = max([float(f.get("precipitation_probability", 0.0) or 0.0) for f in scan_forecast]) if scan_forecast else 0.0

        aqi_ent = self.options.get(CONF_AQI_ENTITY, self.entry_data.get(CONF_AQI_ENTITY))
        aqi_val = self._read_float(aqi_ent, lo=0.0, hi=1000.0, max_age=datetime.timedelta(hours=3)) if aqi_ent else None
        aqi_unknown = bool(aqi_ent) and aqi_val is None

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

        if not is_vetoed and weather_telemetry_degraded:
            is_vetoed = True
            veto_reason = "Outdoor weather telemetry unavailable (fail-closed)"

        if not is_vetoed and aqi_unknown:
            is_vetoed = True
            veto_reason = "AQI telemetry unavailable (fail-closed)"

        if not is_vetoed and scan_forecast:
            for f_item in scan_forecast:
                f_cond = str(f_item.get("condition", "")).lower()
                if f_cond in BAD_WEATHER_CONDITIONS:
                    is_vetoed = True
                    veto_reason = f"Impending bad weather ({f_cond}) within {int(lookahead_mins)} min forecast"
                    break

        self.is_hazard_vetoed = is_vetoed
        self.hazard_veto_reason = veto_reason

        # Solcast PV Forecast & Real-Time Solar Cloud Factor Scaling
        solcast_forecast: list[dict[str, Any]] = []
        try:
            today_attrs = self.hass.states.get("sensor.solcast_pv_forecast_forecast_today")
            tomorrow_attrs = self.hass.states.get("sensor.solcast_pv_forecast_forecast_tomorrow")
            for state_obj in (today_attrs, tomorrow_attrs):
                if state_obj and "detailedForecast" in state_obj.attributes:
                    for item in state_obj.attributes["detailedForecast"]:
                        raw_p = item.get("period_start")
                        if isinstance(raw_p, datetime.datetime):
                            dt_p = raw_p
                        elif raw_p is not None:
                            dt_p = dt_util.parse_datetime(str(raw_p)) or datetime.datetime.fromisoformat(str(raw_p))
                        else:
                            continue
                        if dt_p.tzinfo is None:
                            dt_p = dt_p.replace(tzinfo=datetime.timezone.utc)
                        solcast_forecast.append({"dt": dt_p, "power": float(item["pv_estimate"]) * 1000.0})
            solcast_forecast.sort(key=lambda x: x["dt"])
        except Exception as err:
            _LOGGER.debug("Could not parse Solcast detailedForecast: %s", err)

        solar_trans_ratio = 1.0
        try:
            solar_power_ent = self.options.get(
                CONF_SOLAR_POWER_ENTITY,
                self.entry_data.get(CONF_SOLAR_POWER_ENTITY, DEFAULT_SOLAR_POWER_ENTITY),
            )
            solcast_power_ent = self.options.get(
                CONF_SOLCAST_POWER_ENTITY,
                self.entry_data.get(CONF_SOLCAST_POWER_ENTITY, DEFAULT_SOLCAST_POWER_ENTITY),
            )
            actual_solar_w = self._power_w(solar_power_ent) or 0.0
            solcast_pred_w = self._power_w(solcast_power_ent) or 0.0
            if solcast_pred_w > 100.0:
                solar_trans_ratio = min(1.2, max(0.0, actual_solar_w / solcast_pred_w))
        except Exception:
            solar_trans_ratio = 1.0

        if solcast_forecast and solar_trans_ratio != 1.0:
            for item in solcast_forecast:
                item["power"] = item["power"] * solar_trans_ratio

        # Comfort Profile Resolution
        comfort_profile: dict[str, Any] = {
            "type": "psychrometric_box",
            "temp_min": self.comfort_settings[CONF_COMFORT_TEMP_MIN],
            "temp_max": self.comfort_settings[CONF_COMFORT_TEMP_MAX],
            "humidity_min": self.comfort_settings[CONF_COMFORT_HUMIDITY_MIN],
            "humidity_max": self.comfort_settings[CONF_COMFORT_HUMIDITY_MAX],
            "dew_point_max": self.comfort_settings[CONF_COMFORT_DEW_POINT_MAX],
            "roll_off_temp": DEFAULT_COMFORT_ROLLOFF_TEMP,
            "lower_profile": {
                "temperature_data_points": [self.comfort_settings[CONF_COMFORT_TEMP_MIN]],
                "humidity_data_points": [self.comfort_settings[CONF_COMFORT_HUMIDITY_MIN]],
            },
            "upper_profile": {
                "temperature_data_points": [self.comfort_settings[CONF_COMFORT_TEMP_MAX]],
                "humidity_data_points": [self.comfort_settings[CONF_COMFORT_HUMIDITY_MAX]],
            },
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

        # 3. Zone Setpoint Resolution
        zones_config = self.entry_data.get(CONF_ZONES, {})
        zone_setpoints: dict[str, tuple[float, float]] = {}
        zone_climates: dict[str, str] = {}
        zone_attrs_map: dict[str, dict[str, Any]] = {}
        overall_hvac_mode = "off"

        protect_cool = self._read_float("input_number.hvac_preset_protect_cool", lo=40.0, hi=100.0) or 78.0
        protect_heat = self._read_float("input_number.hvac_preset_protect_heat", lo=40.0, hi=100.0) or 65.0

        for z_key, z_conf in zones_config.items():
            climate_ent = z_conf.get(CONF_CLIMATE_ENTITY)
            c_state = self._get_val(climate_ent, "off")
            c_attrs = self.hass.states.get(climate_ent).attributes if (climate_ent and self.hass.states.get(climate_ent)) else {}
            zone_climates[z_key] = c_state
            zone_attrs_map[z_key] = c_attrs
            c_eff_init = self._effective_hvac_mode(c_state, c_attrs, w_temp, 72.0)
            if c_eff_init in ("cool", "heat"):
                overall_hvac_mode = c_eff_init

            window_eco = (self._get_val(z_conf.get(CONF_WINDOW_ECO_BOOLEAN, f"input_boolean.eco_mode_request_{z_key}_hvac_advisor"), "off") == "on")
            circ_eco = (self._get_val(z_conf.get(CONF_CIRCULATION_ECO_BOOLEAN), "off") == "on")
            circ_act = (self._get_val(z_conf.get(CONF_CIRCULATION_ACTIVE_BOOLEAN), "off") == "on")
            floor_eco = (self._get_val(f"input_boolean.eco_mode_{z_key}", "off") == "on")
            eco_active = window_eco or circ_eco or circ_act or floor_eco

            home_cool = self._read_float(f"input_number.hvac_preset_{z_key}_home_cool", lo=40.0, hi=100.0) or 74.0
            home_heat = self._read_float(f"input_number.hvac_preset_{z_key}_home_heat", lo=40.0, hi=100.0) or 68.0
            sleep_cool = self._read_float(f"input_number.hvac_preset_{z_key}_sleep_cool", lo=40.0, hi=100.0)
            sleep_heat = self._read_float(f"input_number.hvac_preset_{z_key}_sleep_heat", lo=40.0, hi=100.0)

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
                outdoor_temp=w_temp,
                home_mode=home_mode,
            )
            zone_setpoints[z_key] = (act_heat, act_cool)

        # Macro Seasonal Mode
        up_sp = zone_setpoints.get("upstairs", (68.0, 74.0))
        down_sp = zone_setpoints.get("downstairs", (68.0, 74.0))
        global_active_heat = (up_sp[0] + down_sp[0]) / 2.0
        global_active_cool = (up_sp[1] + down_sp[1]) / 2.0

        if len(sanitized_forecast) >= 24:
            seasonal_mode, seasonal_details = determine_seasonal_mode(
                hvac_mode=overall_hvac_mode,
                forecast=sanitized_forecast,
                comfort_profile=comfort_profile,
                active_heat_sp=global_active_heat,
                active_cool_sp=global_active_cool,
                latitude=lat,
            )
            self.seasonal_mode = seasonal_mode
            self.seasonal_details = seasonal_details
        else:
            seasonal_mode = self.seasonal_mode
            seasonal_details = self.seasonal_details

        # 4. Dual-Zone Micro-Simulation & Evaluation
        recovery_bool_ent = self.options.get(
            CONF_COMFORT_RECOVERY_BOOLEAN,
            self.entry_data.get(CONF_COMFORT_RECOVERY_BOOLEAN, DEFAULT_COMFORT_RECOVERY_BOOLEAN),
        )
        is_recovery_on = (self._get_val(recovery_bool_ent, "off") == "on")

        zone_plans: dict[str, ZonePlanResult] = {}
        min_enthalpy = float(self.options.get(CONF_MIN_ENTHALPY_DELTA, DEFAULT_MIN_ENTHALPY_DELTA))
        max_dp = float(self.comfort_settings.get(CONF_COMFORT_DEW_POINT_MAX, self.options.get(CONF_MAX_DEW_POINT, DEFAULT_MAX_DEW_POINT)))
        open_margin = float(self.options.get(CONF_OPEN_TEMP_MARGIN, DEFAULT_OPEN_TEMP_MARGIN))
        close_margin = float(self.options.get(CONF_CLOSE_TEMP_MARGIN, DEFAULT_CLOSE_TEMP_MARGIN))
        open_dwell = float(self.options.get(CONF_OPEN_DWELL_MINUTES, self.entry_data.get(CONF_OPEN_DWELL_MINUTES, DEFAULT_OPEN_DWELL_MINUTES)))

        any_recovery_triggered = False
        recovery_exit_blocked = False
        desired_eco: dict[str, bool] = {}
        zone_precond_plans: dict[str, dict[str, Any]] = {}
        zone_in_comfort: dict[str, bool] = {"upstairs": True, "downstairs": True}
        upstairs_eval_temp: float | None = None
        upstairs_eval_mode: str = "off"

        for z_key, z_conf in zones_config.items():
            z_name = z_conf.get(CONF_ZONE_NAME, z_key.capitalize())
            climate_ent = z_conf.get(CONF_CLIMATE_ENTITY)
            temp_ent = z_conf.get(CONF_TEMP_SENSOR)
            hum_ent = z_conf.get(CONF_HUMIDITY_SENSOR)
            win_ent = z_conf.get(CONF_OPEN_WINDOWS_SENSOR)

            c_st = zone_climates.get(z_key, "off")
            c_attrs = zone_attrs_map.get(z_key, {})
            act_heat, act_cool = zone_setpoints.get(z_key, (68.0, 74.0))

            t_sensor_unit = self._get_attr(temp_ent, "unit_of_measurement", "°F")
            in_temp_raw = self._read_float(temp_ent, lo=-10.0, hi=110.0, max_age=STALE_INDOOR)
            in_temp = self._to_f(in_temp_raw, t_sensor_unit) if in_temp_raw is not None else None
            if in_temp is not None and not (30.0 <= in_temp <= 110.0):
                in_temp = None
            in_hum = self._read_float(hum_ent, lo=1.0, hi=100.0, max_age=STALE_INDOOR)
            win_raw = self._read_float(win_ent, lo=0.0, hi=1000.0)
            if win_raw is None and z_key in self.open_windows_counts:
                win_raw = float(self.open_windows_counts[z_key])

            # Fail-safe: unknown contact state is treated as OPEN for every interlock
            is_open = True if win_raw is None else (win_raw > 0)

            if in_temp is None or in_hum is None:
                _LOGGER.warning("Telemetry unavailable for zone %s; defaulting to safe Close Windows", z_name)
                zone_plans[z_key] = ZonePlanResult(
                    recommended_state=CLOSE,
                    details_message=f"Keep {z_name} windows closed (indoor telemetry unavailable).",
                    history=[],
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    scenario="telemetry_degraded",
                )
                continue

            c_eff = self._effective_hvac_mode(c_st, c_attrs, w_temp, in_temp)
            if c_eff in ("cool", "heat"):
                overall_hvac_mode = c_eff
            if z_key == "upstairs":
                upstairs_eval_temp = in_temp
                upstairs_eval_mode = c_eff

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
                forecast=sanitized_forecast,
                current_outside_temp=w_temp,
                models=z_models,
                hvac_mode=c_eff,
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
                open_dwell_minutes=open_dwell,
                pressure_hpa=self.pressure_hpa,
            )

            # Compressor running interlock
            compressor_active = c_attrs.get("hvac_action") in ("cooling", "heating")
            if plan.recommended_state == OPEN and compressor_active:
                plan.details_message += " Switch HVAC to eco/off before opening (compressor is running)."
                plan.eco_mode_requested = True

            if plan.comfort_recovery_triggered:
                any_recovery_triggered = True

            if is_vetoed:
                plan.recommended_state = CLOSE
                plan.details_message = f"Keep {z_name} windows closed ({veto_reason})."
                plan.scenario = "unoccupied_veto" if is_unoccupied else "hazard_vetoed"

            zone_plans[z_key] = plan

            # Phase 4: Dynamic Rate-Derived Pre-Conditioning Lead Time & Next Automatic Cycle
            closed_pre_hist = (
                run_static_simulation(
                    initial_temp=in_temp,
                    initial_humidity=in_hum,
                    forecast=sanitized_forecast[:12],
                    model_temp=z_models.get("temp_closed"),
                    model_hum=z_models.get("humidity_closed"),
                    strategy="closed",
                    lat=lat,
                    lon=lon,
                    solcast_forecast=solcast_forecast,
                )
                if sanitized_forecast
                else []
            )
            tstat_temp_f = self._read_thermostat_temp_f(z_key, climate_ent, in_temp)
            tstat_pre_hist = (
                run_static_simulation(
                    initial_temp=tstat_temp_f,
                    initial_humidity=in_hum,
                    forecast=sanitized_forecast[:12],
                    model_temp=z_models.get("temp_closed"),
                    model_hum=z_models.get("humidity_closed"),
                    strategy="closed",
                    lat=lat,
                    lon=lon,
                    solcast_forecast=solcast_forecast,
                )
                if (sanitized_forecast and tstat_temp_f is not None)
                else None
            )
            cur_ttt_a = self.zone_diagnostics.get(z_key, {}).get("time_to_temp_attrs", {})
            precond_plan = calculate_preconditioning_plan(
                closed_history=closed_pre_hist,
                forecast=sanitized_forecast,
                hvac_mode=c_eff,
                seasonal_mode=seasonal_mode,
                inside_temp=in_temp,
                active_heat_sp=act_heat,
                active_cool_sp=act_cool,
                safe_min_temp=safe_min,
                safe_max_temp=safe_max,
                hvac_heat_model=self.models.get(f"{z_key}_temp_profile_hvac_heat"),
                hvac_cool_model=self.models.get(f"{z_key}_temp_profile_hvac_cool"),
                now=now,
                raw_hvac_state=c_st,
                hvac_action=str(c_attrs.get("hvac_action", "idle")),
                is_currently_open=is_open,
                thermostat_temp=tstat_temp_f,
                thermostat_closed_history=tstat_pre_hist,
                swing_heat_f=float(cur_ttt_a.get("learned_swing_heat_f", 0.5)),
                swing_cool_f=float(cur_ttt_a.get("learned_swing_cool_f", 0.5)),
            )
            zone_precond_plans[z_key] = precond_plan
            if z_key in self.zone_diagnostics:
                ttt_attrs = self.zone_diagnostics[z_key].setdefault("time_to_temp_attrs", {})
                ttt_attrs["pre_condition_needed"] = precond_plan["pre_condition_needed"]
                ttt_attrs["pre_condition_mode"] = precond_plan["pre_condition_mode"]
                ttt_attrs["pre_condition_start_time"] = precond_plan["pre_condition_start_time"]
                ttt_attrs["pre_condition_lead_minutes"] = precond_plan["pre_condition_lead_minutes"]
                ttt_attrs["predicted_breach_time"] = precond_plan["predicted_breach_time"]
                ttt_attrs["next_hvac_cycle_mode"] = precond_plan["next_hvac_cycle_mode"]
                ttt_attrs["next_hvac_cycle_time"] = precond_plan["next_hvac_cycle_time"]
                ttt_attrs["next_hvac_cycle_minutes"] = precond_plan["next_hvac_cycle_minutes"]
                ttt_attrs["next_hvac_trigger_temp"] = precond_plan["next_hvac_trigger_temp"]

            lb_c, ub_c = get_comfort_bounds(in_temp, comfort_profile)
            zone_in_comfort[z_key] = (
                safe_min <= in_temp <= safe_max
                and (ub_c is None or in_hum <= ub_c)
                and (lb_c is None or is_bedtime or in_hum >= lb_c)
            )

            # Queue eco mode request
            eco_bool = z_conf.get(CONF_WINDOW_ECO_BOOLEAN, f"input_boolean.eco_mode_request_{z_key}_hvac_advisor")
            if eco_bool:
                preemptive_eco = bool(self.options.get(CONF_PREEMPTIVE_ECO_MODE, DEFAULT_PREEMPTIVE_ECO_MODE))
                desired_eco[eco_bool] = bool(
                    plan.eco_mode_requested and (preemptive_eco or compressor_active or is_open)
                )

            # Exit deadband check: block recovery exit if within 3% RH of upper bound
            _, ub = get_comfort_bounds(in_temp, comfort_profile)
            if ub is not None and in_hum > (ub - RECOVERY_EXIT_MARGIN_RH):
                recovery_exit_blocked = True

            # Convective Circulation Management
            circ_enabled = self.options.get(
                f"{z_key}_circ_enabled",
                z_conf.get(CONF_CIRCULATION_ENABLED, False),
            )
            if circ_enabled:
                async with self._circ_lock:
                    source_temp_ent = z_conf.get(CONF_CIRCULATION_SOURCE_TEMP)
                    active_bool_ent = z_conf.get(CONF_CIRCULATION_ACTIVE_BOOLEAN)
                    eco_bool_ent = z_conf.get(CONF_CIRCULATION_ECO_BOOLEAN)

                    src_unit = self._get_attr(source_temp_ent, "unit_of_measurement", "°F")
                    source_temp_raw = self._read_float(source_temp_ent, lo=-10.0, hi=120.0, max_age=STALE_INDOOR) if source_temp_ent else None
                    source_temp = self._to_f(source_temp_raw, src_unit) if source_temp_raw is not None else None
                    if source_temp is not None and not (30.0 <= source_temp <= 120.0):
                        source_temp = None
                    fan_is_active = (self._get_val(active_bool_ent, "off") == "on")

                    if source_temp is not None:
                        cooldown_mins = float(self.options.get(CONF_CIRC_COOLDOWN_MINUTES, DEFAULT_CIRC_COOLDOWN_MINUTES))
                        circ_action, lockout = self.circulation_manager.evaluate_circulation(
                            z_key=z_key,
                            area_temp=in_temp,
                            source_temp=source_temp,
                            target_cool=act_cool,
                            target_heat=act_heat,
                            hvac_mode=c_eff,
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
                            lockout_hours=float(self.options.get(CONF_CIRC_LOCKOUT_HOURS, DEFAULT_CIRC_LOCKOUT_HOURS)),
                            cooldown_hours=cooldown_mins / 60.0,
                        )

                        mem = self.circulation_manager.get_zone_memory(z_key)
                        if circ_action == "turn_on" and not fan_is_active:
                            _LOGGER.info("Passable Smart Climate: Engaging circulation fan for %s.", z_name)
                            mem["start_time"] = now_utc
                            mem["start_temp"] = in_temp
                            mem["mode"] = "cool" if (c_eff == "cool" or in_temp > act_cool) else "heat"
                            self._learning_store.async_delay_save(self.circulation_manager.export_learning_data, 5)
                            if active_bool_ent:
                                await self._async_command_bool(active_bool_ent, True)
                            if eco_bool_ent:
                                await self._async_command_bool(eco_bool_ent, True)
                            if climate_ent and self.actuation_enabled:
                                await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_ent, "fan_mode": "on"}, blocking=True)

                        elif circ_action == "turn_off" and fan_is_active:
                            _LOGGER.info("Passable Smart Climate: Shutting off circulation fan for %s.", z_name)
                            if lockout > 0:
                                mem["lockout_until"] = now_utc + datetime.timedelta(hours=lockout)
                            mem["start_time"] = None
                            mem["start_temp"] = None
                            mem["mode"] = None
                            self._learning_store.async_delay_save(self.circulation_manager.export_learning_data, 5)
                            if active_bool_ent:
                                await self._async_command_bool(active_bool_ent, False)
                            if eco_bool_ent:
                                await self._async_command_bool(eco_bool_ent, False)
                            if climate_ent and self.actuation_enabled:
                                await self.hass.services.async_call("climate", "set_fan_mode", {"entity_id": climate_ent, "fan_mode": "auto"}, blocking=True)

        # 5. Comfort Recovery Latch & Actuation Arbitration
        if any_recovery_triggered:
            want_recovery = True
        elif is_recovery_on:
            held = bool(self._recovery_since and (now_utc - self._recovery_since) < RECOVERY_MIN_ON)
            want_recovery = bool(held or recovery_exit_blocked)
        else:
            want_recovery = False

        if want_recovery and not is_recovery_on:
            self._recovery_since = now_utc
            _LOGGER.info("Passable Smart Climate: Comfort recovery triggered across zones. Engaging recovery latch.")
        elif not want_recovery and is_recovery_on:
            self._recovery_since = None
            _LOGGER.info("Passable Smart Climate: Comfort recovered across all zones. Disengaging recovery latch.")

        self.comfort_recovery_active = want_recovery
        if recovery_bool_ent:
            await self._async_command_bool(recovery_bool_ent, want_recovery)

        # During active comfort recovery, force eco booleans off
        if want_recovery:
            desired_eco = dict.fromkeys(desired_eco, False)

        for ent, want in desired_eco.items():
            await self._async_command_bool(ent, want)

        # Phase 3: Helper Invariant Verification Watchdog
        for z_k_inv, z_c_inv in zones_config.items():
            mem_inv = self.circulation_manager.get_zone_memory(z_k_inv)
            if mem_inv.get("start_time") is None:
                c_eco_ent = z_c_inv.get(CONF_CIRCULATION_ECO_BOOLEAN)
                if c_eco_ent and self._get_val(c_eco_ent, "off") == "on":
                    await self._async_command_bool(c_eco_ent, False)
            if z_k_inv in self.zone_diagnostics:
                self.zone_diagnostics[z_k_inv].setdefault("health_attrs", {})[
                    "last_invariant_check"
                ] = now_utc.isoformat()

        # Phase 4: Aggregate Pre-Conditioning Plan Diagnostics
        active_precond = (
            zone_precond_plans.get("upstairs")
            if zone_precond_plans.get("upstairs", {}).get("pre_condition_needed")
            else (
                zone_precond_plans.get("downstairs")
                if zone_precond_plans.get("downstairs", {}).get("pre_condition_needed")
                else {}
            )
        )
        self.pre_condition_needed = bool(active_precond.get("pre_condition_needed", False))
        self.pre_condition_mode = active_precond.get("pre_condition_mode")
        self.pre_condition_start_time = active_precond.get("pre_condition_start_time")
        self.pre_condition_lead_minutes = active_precond.get("pre_condition_lead_minutes")
        self.predicted_breach_time = active_precond.get("predicted_breach_time")

        # Phase 5: Sleep-Window Worst-Case Bedroom Comfort & Offset Advisory
        if is_bedtime or is_evening:
            rooms_cfg = self.get_configured_rooms()
            r_temps_eval: dict[str, float | None] = {}
            r_rates_eval: dict[str, float | None] = {}
            for r_s, r_c in rooms_cfg.items():
                if isinstance(r_c, dict):
                    rt_ent = r_c.get(CONF_ROOM_TEMP_SENSOR)
                    rt_unit = self._get_attr(rt_ent, "unit_of_measurement", "°F")
                    rt_raw = self._read_float(rt_ent, lo=-10.0, hi=120.0)
                    r_temps_eval[r_s] = self._to_f(rt_raw, rt_unit) if rt_raw is not None else None
                    r_rates_eval[r_s] = self.room_mlr_rates.get(
                        f"{r_s}_heat_loss_gain_windows_closed"
                    )
            up_ttt_a = self.zone_diagnostics.get("upstairs", {}).get("time_to_temp_attrs", {})
            sleep_adv = evaluate_sleep_bedroom_offset(
                rooms_config=rooms_cfg,
                room_temps=r_temps_eval,
                room_rates=r_rates_eval,
                zone_temp=upstairs_eval_temp,
                hvac_mode=upstairs_eval_mode or overall_hvac_mode,
                seasonal_mode=seasonal_mode,
                active_heat_sp=up_sp[0],
                active_cool_sp=up_sp[1],
                comfort_temp_min=float(
                    self.comfort_settings.get(CONF_COMFORT_TEMP_MIN, DEFAULT_COMFORT_TEMP_MIN)
                ),
                comfort_temp_max=float(
                    self.comfort_settings.get(CONF_COMFORT_TEMP_MAX, DEFAULT_COMFORT_TEMP_MAX)
                ),
                swing_heat_f=float(up_ttt_a.get("learned_swing_heat_f", 0.5)),
                swing_cool_f=float(up_ttt_a.get("learned_swing_cool_f", 0.5)),
                prev_offset_f=self.recommended_sleep_offset_f,
            )
            self.sleep_limiting_room = sleep_adv["sleep_limiting_room"]
            self.sleep_limiting_room_entity = sleep_adv["sleep_limiting_room_entity"]
            self.sleep_limiting_room_temp = sleep_adv["sleep_limiting_room_temp"]
            self.recommended_sleep_offset_f = sleep_adv["recommended_sleep_offset_f"]
        else:
            self.sleep_limiting_room = None
            self.sleep_limiting_room_entity = None
            self.sleep_limiting_room_temp = None
            self.recommended_sleep_offset_f = 0.0

        self.zone_results = zone_plans

        # 6. Combined Recommendation & State Machine Latch
        any_open = any(p.recommended_state == OPEN for p in zone_plans.values())
        raw_state = OPEN if (any_open and not is_vetoed) else CLOSE

        out_dp = PsychrometricEngine.dew_point(w_temp, w_rh) if w_rh is not None else None
        latent_breach = out_dp is not None and out_dp > (max_dp + 3.0)

        if is_vetoed:
            active_scenario = "unoccupied_veto" if is_unoccupied else "hazard_vetoed"
        elif any_open:
            active_scenario = next((p.scenario for p in zone_plans.values() if p.recommended_state == OPEN), "standard")
        else:
            active_scenario = next((p.scenario for p in zone_plans.values()), "standard")

        safety_close = is_vetoed or latent_breach or any(
            p.recommended_state == CLOSE and p.scenario in SAFETY_SCENARIOS
            for p in zone_plans.values()
        )

        final_state, dwell_held = self._latch.step(
            raw_state,
            now_utc,
            min_open=datetime.timedelta(minutes=open_dwell),
            min_closed=datetime.timedelta(minutes=max(20.0, open_dwell / 2.0)),
            safety_close=safety_close,
            scenario=active_scenario,
        )

        self.last_recommendation = final_state
        self._latch_store.async_delay_save(self._latch.as_dict, 5)

        # Phase 6: Behavioral Window-Compliance & Recommendation Audit Ledger
        up_sensors_total = len(self.get_zone_window_sensors("upstairs"))
        dn_sensors_total = len(self.get_zone_window_sensors("downstairs"))
        self.compliance_ledger.record_step(
            now=now,
            recommendation=final_state,
            up_open_count=int(self.open_windows_counts.get("upstairs", 0)),
            up_total_sensors=up_sensors_total,
            dn_open_count=int(self.open_windows_counts.get("downstairs", 0)),
            dn_total_sensors=dn_sensors_total,
            up_in_comfort=zone_in_comfort.get("upstairs", True),
            dn_in_comfort=zone_in_comfort.get("downstairs", True),
            dt_minutes=15.0,
        )
        comp_summary = self.compliance_ledger.get_summary()
        self.compliance_summary = comp_summary["compliance_summary"]
        self.learned_open_windows_fraction = comp_summary["learned_open_windows_fraction"]
        self._compliance_store.async_delay_save(self.compliance_ledger.as_dict, 30)

        # Markdown details formulation
        ts_str = f"{now.hour % 12 or 12}:{now.strftime('%M %p on %b')} {now.day}"
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

        if self.pre_condition_needed and self.pre_condition_start_time:
            pre_verb = "pre-cooling" if self.pre_condition_mode == "cool" else "pre-heating"
            details += (
                f"\n\n**Pre-Conditioning Advisory:** Start {pre_verb} around **{self.pre_condition_start_time}** "
                f"(~{self.pre_condition_lead_minutes}m lead time before {self.predicted_breach_time} threshold)."
            )

        if self.comfort_recovery_active and is_vetoed:
            details += "\n\n*(Comfort recovery active: HVAC running to restore indoor comfort)*"

        if dwell_held and self._latch.since is not None:
            dwell_mins = open_dwell if self._latch.state == OPEN else max(20.0, open_dwell / 2.0)
            elapsed_sec = (now_utc - self._latch.since).total_seconds()
            rem_m = max(1, int(round((dwell_mins * 60 - elapsed_sec) / 60.0)))
            details += f"\n\n*({self._latch.state} recommendation held active by {int(dwell_mins)}-minute anti-flapping latch; ~{rem_m} min remaining)*"

        # Trajectory history averaging
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
        self._build_climate_outlook_summaries(
            zone_plans=zone_plans,
            avg_hist=avg_hist,
            sanitized_forecast=sanitized_forecast,
            w_temp=w_temp,
            final_state=final_state,
            zone_climates=zone_climates,
        )

        # 7. Bedtime Overnight Heat Prediction Alert & Daily Outlook Sync
        self.heat_prediction_alert = False
        predict_heat_ent = self.options.get(
            CONF_PREDICT_HEAT_BOOLEAN,
            self.entry_data.get(CONF_PREDICT_HEAT_BOOLEAN, DEFAULT_PREDICT_HEAT_BOOLEAN),
        )
        predict_msg_ent = self.options.get(
            CONF_PREDICTION_MESSAGE_TEXT,
            self.entry_data.get(CONF_PREDICTION_MESSAGE_TEXT, DEFAULT_PREDICTION_MESSAGE_TEXT),
        )
        is_alt_sleep = (self._get_val("input_boolean.sleeping_in_living_room", "off") == "on")
        eval_hist = avg_hist
        if not is_alt_sleep and up_plan and up_plan.history:
            eval_hist = up_plan.history

        if is_bedtime and (overall_hvac_mode in ["cool", "off"]) and eval_hist:
            min_comfort_temp = float(self.comfort_settings.get(CONF_COMFORT_TEMP_MIN, 64.5))
            if not is_alt_sleep:
                up_sleep_heat = self._read_float("input_number.hvac_preset_upstairs_sleep_heat", lo=50.0, hi=80.0)
                if up_sleep_heat is not None:
                    min_comfort_temp = float(up_sleep_heat)
            elif comfort_profile:
                if "temp_min" in comfort_profile:
                    min_comfort_temp = float(comfort_profile["temp_min"])

            offset_cfg = self.options.get(
                CONF_HVAC_TRANSITION_OFFSET,
                self.entry_data.get(CONF_HVAC_TRANSITION_OFFSET, DEFAULT_HVAC_TRANSITION_OFFSET_ENTITY),
            )
            try:
                offset_val = float(offset_cfg)
            except (ValueError, TypeError):
                val_from_ent = self._read_float(offset_cfg, lo=-20.0, hi=20.0)
                offset_val = float(val_from_ent) if val_from_ent is not None else float(DEFAULT_HVAC_TRANSITION_OFFSET)

            is_prediction_on = (self._get_val(predict_heat_ent, "off") == "on")
            transition_threshold = (min_comfort_temp - offset_val) if overall_hvac_mode == "cool" else min_comfort_temp

            first_breach_time: str | None = None
            first_breach_temp: float | None = None
            for idx, pt in enumerate(eval_hist):
                if pt["temp"] < transition_threshold and idx < len(sanitized_forecast):
                    dt_raw = sanitized_forecast[idx].get("datetime")
                    if dt_raw:
                        local_dt = dt_util.as_local(datetime.datetime.fromisoformat(str(dt_raw)))
                        h = local_dt.hour % 12 or 12
                        first_breach_time = f"{h}:{local_dt.strftime('%M %p')}"
                        first_breach_temp = pt["temp"]
                        break

            if first_breach_time:
                self.heat_prediction_alert = True
                area_str = "the bedrooms" if not is_alt_sleep else "the house"
                msg = (
                    f"The advisor predicts {area_str} will get cold enough to require heat overnight "
                    f"(dropping to {first_breach_temp:.1f}°F around {first_breach_time}). "
                    f"Would you like to turn the heat on now?"
                )
                if not is_prediction_on and self.actuation_enabled:
                    try:
                        await self._async_sync_prediction_message(predict_msg_ent, msg)
                        await self._async_command_bool(predict_heat_ent, True)
                    except Exception as e:
                        _LOGGER.error("Failed to set heat prediction entities: %s", e)

                details += "\n\n**Note:** A switch to **Heat Mode** may be required overnight."

        if not self.heat_prediction_alert:
            if predict_heat_ent:
                await self._async_command_bool(predict_heat_ent, False)
            if predict_msg_ent and self.daily_outlook_summary:
                await self._async_sync_prediction_message(predict_msg_ent, self.daily_outlook_summary)

        self.last_details = details
        self.last_update_time = now_utc

        # 8. Sync Overview Helper
        await self._async_sync_overview(final_state)

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
