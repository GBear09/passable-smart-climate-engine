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
    ZONE_MLR_RATE_SPECS,
)
from collections import deque
from .core.models import RateModel, SimulationAction, ZonePlanResult
from .core.psychrometrics import PsychrometricEngine, pressure_from_elevation
from .core.simulator import run_static_simulation
from .engines.advisor import (
    BAD_WEATHER_CONDITIONS,
    determine_seasonal_mode,
    evaluate_environmental_vetoes,
    evaluate_zone_plan,
    get_comfort_bounds,
)
from .engines.circulation import CirculationManager
from .engines.latch import (
    CLOSE,
    OPEN,
    SAFETY_SCENARIOS,
    RecommendationLatch,
)
from .engines.phases import PHASE_DAYTIME, determine_temporal_phase, resolve_zone_setpoints
from .training.influx_client import InfluxClient
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

        self._latch = RecommendationLatch()
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

    def get_zone_open_windows_count(self, zone_id: str) -> int:
        """Count open window contact sensors for a zone, falling back to legacy count sensor if needed."""
        win_sensors = self.get_zone_window_sensors(zone_id)
        found_any = False
        open_count = 0
        for w_ent in win_sensors:
            st = self.hass.states.get(w_ent)
            if st is not None and st.state not in _INVALID_STATES:
                found_any = True
                if st.state == "on":
                    open_count += 1
        if found_any:
            return open_count

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

        # 2. Open Window Counts
        up_win = self.get_zone_open_windows_count("upstairs")
        down_win = self.get_zone_open_windows_count("downstairs")
        total_win = up_win + down_win
        self.open_windows_counts = {
            "upstairs": up_win,
            "downstairs": down_win,
            "total": total_win,
        }

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
                    if (self.hass.states.get(w_e) and self.hass.states.get(w_e).state == "on")
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

            if is_active_now and not was_active:
                zt["active_since_ts"] = now_utc.isoformat()
                cycles = list(zt.get("cycle_timestamps", []))
                cycles.append(now_utc.isoformat())
                zt["cycle_timestamps"] = cycles
            elif not is_active_now:
                zt["active_since_ts"] = None

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

            # Coasting evaluation (90-minute post-HVAC thermal inertia gate with rebound override)
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

            # Physics-Based Time-to-Target & Health Diagnostics
            target_sp = c_attrs.get("temperature")
            if target_sp is None:
                target_sp = c_attrs.get("target_temp_low") if hvac_action == "heating" else c_attrs.get("target_temp_high")
            target_f = float(target_sp) if target_sp is not None else None

            active_run_mins = 0.0
            if zt.get("active_since_ts"):
                act_dt = dt_util.parse_datetime(str(zt["active_since_ts"]))
                if act_dt:
                    active_run_mins = max(0.0, (now_utc - act_dt).total_seconds() / 60.0)

            model_key = f"{z_key}_temp_profile_hvac_heat" if hvac_action == "heating" else f"{z_key}_temp_profile_hvac_cool"
            rate_model = self.models.get(model_key)
            pred_rate = 0.0
            if rate_model and in_temp is not None:
                delta_t = w_temp - in_temp
                pred_rate = float(rate_model.slope * delta_t + rate_model.intercept)
            if active_run_mins >= 10.0 and abs(ft_rate) > 0.1:
                pred_rate = 0.5 * pred_rate + 0.5 * ft_rate if pred_rate != 0.0 else ft_rate

            time_to_temp = 0
            if is_active_now and in_temp is not None and target_f is not None:
                if hvac_action == "heating" and target_f > in_temp:
                    eff_r = max(0.4, pred_rate if pred_rate > 0.2 else 1.5)
                    time_to_temp = max(1, min(240, int(round(((target_f - in_temp) / eff_r) * 60.0))))
                elif hvac_action == "cooling" and in_temp > target_f:
                    eff_r = max(0.4, abs(pred_rate) if pred_rate < -0.2 else 1.5)
                    time_to_temp = max(1, min(240, int(round(((in_temp - target_f) / eff_r) * 60.0))))

            cap_key = CONF_FILTER_CAPACITY_UPSTAIRS if z_key == "upstairs" else CONF_FILTER_CAPACITY_DOWNSTAIRS
            cap_h = (
                self.comfort_settings.get(cap_key)
                or self._read_float(f"number.hvac_filter_life_{z_key}", lo=10.0, hi=2000.0)
                or self._read_float(f"input_number.hvac_filter_life_{z_key}", lo=10.0, hi=2000.0)
                or float(self.options.get(cap_key, DEFAULT_FILTER_CAPACITY_HOURS))
            )
            rem_h = round(float(cap_h) - float(zt.get("filter_runtime_hours", 0.0)), 1)

            cph = len(valid_cycles)
            if cph > 4:
                health_status = "Short Cycling"
            elif is_active_now and active_run_mins >= 20.0 and (
                (hvac_action == "cooling" and ft_rate > -0.1)
                or (hvac_action == "heating" and ft_rate < 0.1)
            ):
                health_status = "Thermal Stall"
            elif rem_h <= 0.0:
                health_status = "Filter Overdue"
            else:
                health_status = "Optimal"

            self.zone_diagnostics[z_key] = {
                "time_to_temp": time_to_temp,
                "time_to_temp_attrs": {
                    "formatted": f"~{time_to_temp}m" if time_to_temp > 0 else "Idle",
                    "hvac_action": hvac_action,
                    "current_temperature": in_temp,
                    "target_temperature": target_f,
                    "predicted_rate_f_hr": round(pred_rate, 2),
                    "observed_rate_f_hr": ft_rate,
                    "active_runtime_minutes": round(active_run_mins, 1),
                },
                "health_status": health_status,
                "health_attrs": {
                    "cycles_per_hour": cph,
                    "active_runtime_minutes": round(active_run_mins, 1),
                    "observed_rate_f_hr": ft_rate,
                    "filter_life_remaining_hours": rem_h,
                    "is_coasting": is_coasting,
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
            if win_ent and win_ent not in entities_to_track:
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
            "cloud_coverage": 50.0,
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

        # 7. Bedtime Overnight Heat Prediction Alert
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
                        await self.hass.services.async_call("input_text", "set_value", {"entity_id": predict_msg_ent, "value": msg})
                        await self._async_command_bool(predict_heat_ent, True)
                    except Exception as e:
                        _LOGGER.error("Failed to set heat prediction entities: %s", e)

                details += "\n\n**Note:** A switch to **Heat Mode** may be required overnight."

        if not self.heat_prediction_alert and predict_heat_ent:
            await self._async_command_bool(predict_heat_ent, False)

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
