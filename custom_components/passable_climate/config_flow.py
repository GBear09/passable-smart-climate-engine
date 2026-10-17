"""Config Flow and Options Flow for Passable Smart Climate Engine.
Provides interactive UI setup with entity selectors, masked password inputs, and connection pre-testing.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

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
    CONF_COMFORT_PROFILE,
    CONF_COMFORT_RECOVERY_BOOLEAN,
    CONF_DOWNSTAIRS_CIRC_ENABLED,
    CONF_FORECAST_LOOKAHEAD_MINUTES,
    CONF_HIGH_WIND_GUST,
    CONF_HIGH_WIND_SPEED,
    CONF_HOME_MODE_ENTITY,
    CONF_HUMIDITY_SENSOR,
    CONF_HVAC_TRANSITION_OFFSET,
    CONF_INFLUX_BUCKET,
    CONF_INFLUX_DATABASE,
    CONF_INFLUX_HOST,
    CONF_INFLUX_LOOKBACK_DAYS,
    CONF_INFLUX_ORG,
    CONF_INFLUX_PASSWORD,
    CONF_INFLUX_PORT,
    CONF_INFLUX_RETRAIN_HOURS,
    CONF_INFLUX_TOKEN,
    CONF_INFLUX_USERNAME,
    CONF_INFLUX_VERSION,
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
    CONF_UPSTAIRS_CIRC_ENABLED,
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
    DEFAULT_COMFORT_PROFILE_ENTITY,
    DEFAULT_COMFORT_RECOVERY_BOOLEAN,
    DEFAULT_FORECAST_LOOKAHEAD_MINUTES,
    DEFAULT_HIGH_WIND_GUST,
    DEFAULT_HIGH_WIND_SPEED,
    DEFAULT_HOME_MODE_ENTITY,
    DEFAULT_HVAC_TRANSITION_OFFSET_ENTITY,
    DEFAULT_INFLUX_DATABASE,
    DEFAULT_INFLUX_HOST,
    DEFAULT_INFLUX_LOOKBACK_DAYS,
    DEFAULT_INFLUX_PORT,
    DEFAULT_INFLUX_RETRAIN_HOURS,
    DEFAULT_INFLUX_VERSION,
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
)
from homeassistant.util import slugify
from .training.influx_client import InfluxClient

_LOGGER = logging.getLogger(__name__)

class SmartClimateConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles configuration flow for Passable Smart Climate Engine."""

    VERSION = 1

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1: Environmental & Atmospheric Entity Mapping."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_influxdb()

        schema = vol.Schema(
            {
                vol.Required(CONF_WEATHER_ENTITY, default=DEFAULT_WEATHER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
                vol.Required(CONF_SOLAR_POWER_ENTITY, default=DEFAULT_SOLAR_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_SOLCAST_POWER_ENTITY, default=DEFAULT_SOLCAST_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_HOME_MODE_ENTITY, default=DEFAULT_HOME_MODE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_select")
                ),
                vol.Required(CONF_PRESENCE_ENTITY, default=DEFAULT_PRESENCE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(CONF_AQI_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_influxdb(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 2: InfluxDB Connection Setup with Pre-Flight Connection Test."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = InfluxClient(
                host=user_input.get(CONF_INFLUX_HOST, DEFAULT_INFLUX_HOST),
                port=int(user_input.get(CONF_INFLUX_PORT, DEFAULT_INFLUX_PORT)),
                version=str(user_input.get(CONF_INFLUX_VERSION, DEFAULT_INFLUX_VERSION)),
                database=user_input.get(CONF_INFLUX_DATABASE, DEFAULT_INFLUX_DATABASE),
                username=user_input.get(CONF_INFLUX_USERNAME),
                password=user_input.get(CONF_INFLUX_PASSWORD),
                token=user_input.get(CONF_INFLUX_TOKEN),
                org=user_input.get(CONF_INFLUX_ORG),
                bucket=user_input.get(CONF_INFLUX_BUCKET),
            )

            success, msg = await self.hass.async_add_executor_job(client.test_connection)
            if success:
                self.data.update(user_input)
                return await self.async_step_zones()
            else:
                errors["base"] = "cannot_connect"
                _LOGGER.warning("InfluxDB connection validation failed: %s", msg)

        schema = vol.Schema(
            {
                vol.Required(CONF_INFLUX_HOST, default=DEFAULT_INFLUX_HOST): str,
                vol.Required(CONF_INFLUX_PORT, default=DEFAULT_INFLUX_PORT): int,
                vol.Required(
                    CONF_INFLUX_VERSION, default=DEFAULT_INFLUX_VERSION
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=["1", "2"],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(CONF_INFLUX_DATABASE, default=DEFAULT_INFLUX_DATABASE): str,
                vol.Optional(CONF_INFLUX_USERNAME, default="ha_user"): str,
                vol.Optional(CONF_INFLUX_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_INFLUX_TOKEN): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_INFLUX_ORG): str,
                vol.Optional(CONF_INFLUX_BUCKET, default="homeassistant/autogen"): str,
            }
        )

        return self.async_show_form(step_id="influxdb", data_schema=schema, errors=errors)

    async def async_step_zones(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 3: Upstairs and Downstairs Dual-Zone Mapping."""
        if user_input is not None:
            zones_dict = {
                "upstairs": {
                    CONF_ZONE_NAME: "Upstairs",
                    CONF_CLIMATE_ENTITY: user_input["upstairs_climate"],
                    CONF_TEMP_SENSOR: user_input["upstairs_temp"],
                    CONF_HUMIDITY_SENSOR: user_input["upstairs_humidity"],
                    CONF_OPEN_WINDOWS_SENSOR: user_input["upstairs_windows"],
                    CONF_WINDOW_ECO_BOOLEAN: user_input["upstairs_eco"],
                    CONF_CIRCULATION_ENABLED: user_input.get("upstairs_circ_enabled", True),
                    CONF_CIRCULATION_SOURCE_TEMP: user_input.get("upstairs_attic_temp"),
                    CONF_CIRCULATION_ACTIVE_BOOLEAN: user_input.get("upstairs_circ_active"),
                    CONF_CIRCULATION_ECO_BOOLEAN: user_input.get("upstairs_circ_eco"),
                },
                "downstairs": {
                    CONF_ZONE_NAME: "Downstairs",
                    CONF_CLIMATE_ENTITY: user_input["downstairs_climate"],
                    CONF_TEMP_SENSOR: user_input["downstairs_temp"],
                    CONF_HUMIDITY_SENSOR: user_input["downstairs_humidity"],
                    CONF_OPEN_WINDOWS_SENSOR: user_input["downstairs_windows"],
                    CONF_WINDOW_ECO_BOOLEAN: user_input["downstairs_eco"],
                    CONF_CIRCULATION_ENABLED: user_input.get("downstairs_circ_enabled", True),
                    CONF_CIRCULATION_SOURCE_TEMP: user_input.get("downstairs_basement_temp"),
                    CONF_CIRCULATION_ACTIVE_BOOLEAN: user_input.get("downstairs_circ_active"),
                    CONF_CIRCULATION_ECO_BOOLEAN: user_input.get("downstairs_circ_eco"),
                },
            }
            self.data[CONF_ZONES] = zones_dict
            return await self.async_step_schedules_outputs()

        schema = vol.Schema(
            {
                # Upstairs
                vol.Required("upstairs_climate", default="climate.upstairs_hk"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required("upstairs_temp", default="sensor.upstairs_average_temperature_smoothed"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required("upstairs_humidity", default="sensor.upstairs_average_humidity_smoothed"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required("upstairs_windows", default="sensor.upstairs_open_windows_count"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required("upstairs_eco", default="input_boolean.eco_mode_request_upstairs_hvac_advisor"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
                vol.Optional("upstairs_circ_enabled", default=True): bool,
                vol.Optional("upstairs_attic_temp", default="sensor.attic_average_temperature_smoothed"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional("upstairs_circ_active", default="input_boolean.hvac_fan_circulation_active_upstairs"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
                vol.Optional("upstairs_circ_eco", default="input_boolean.eco_mode_request_upstairs_circulation"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),

                # Downstairs
                vol.Required("downstairs_climate", default="climate.downstairs_hk"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required("downstairs_temp", default="sensor.downstairs_average_temperature_smoothed"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required("downstairs_humidity", default="sensor.downstairs_average_humidity_smoothed"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required("downstairs_windows", default="sensor.downstairs_open_windows_count"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required("downstairs_eco", default="input_boolean.eco_mode_request_downstairs_hvac_advisor"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
                vol.Optional("downstairs_circ_enabled", default=True): bool,
                vol.Optional("downstairs_basement_temp", default="sensor.basement_average_temperature_smoothed"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional("downstairs_circ_active", default="input_boolean.hvac_fan_circulation_active_downstairs"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
                vol.Optional("downstairs_circ_eco", default="input_boolean.eco_mode_request_downstairs_circulation"): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
            }
        )

        return self.async_show_form(step_id="zones", data_schema=schema)

    async def async_step_schedules_outputs(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 4: Schedules & Target Output Helpers."""
        if user_input is not None:
            self.data.update(user_input)
            return self.async_create_entry(
                title="Passable Smart Climate Engine",
                data=self.data,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_BEDTIME_START, default=DEFAULT_BEDTIME_START): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_datetime")
                ),
                vol.Required(CONF_PARENT_BEDTIME, default=DEFAULT_PARENT_BEDTIME): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_datetime")
                ),
                vol.Required(CONF_MORNING_START, default=DEFAULT_MORNING_START): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_datetime")
                ),
                vol.Required(CONF_COMFORT_PROFILE, default=DEFAULT_COMFORT_PROFILE_ENTITY): str,
                vol.Required(CONF_RECOMMENDATION_ENTITY, default=DEFAULT_RECOMMENDATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_text")
                ),
                vol.Required(CONF_PREDICT_HEAT_BOOLEAN, default=DEFAULT_PREDICT_HEAT_BOOLEAN): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
                vol.Required(CONF_PREDICTION_MESSAGE_TEXT, default=DEFAULT_PREDICTION_MESSAGE_TEXT): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_text")
                ),
                vol.Required(CONF_HVAC_TRANSITION_OFFSET, default=DEFAULT_HVAC_TRANSITION_OFFSET_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["input_number", "number", "sensor"])
                ),
                vol.Required(CONF_COMFORT_RECOVERY_BOOLEAN, default=DEFAULT_COMFORT_RECOVERY_BOOLEAN): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="input_boolean")
                ),
            }
        )

        return self.async_show_form(step_id="schedules_outputs", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return SmartClimateOptionsFlowHandler(config_entry)

class SmartClimateOptionsFlowHandler(config_entries.OptionsFlow):
    """Handles categorized Options Flow for live threshold and deadband adjustment."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        super().__init__()
        self._config_entry = config_entry
        self._editing_room_slug: str | None = None
        self._pending_options: dict[str, Any] = {}

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        return self._config_entry

    @config_entry.setter
    def config_entry(self, value: config_entries.ConfigEntry) -> None:
        self._config_entry = value

    def _get_rooms(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of configured rooms merged with defaults."""
        raw_rooms = self.config_entry.options.get(
            CONF_ROOMS,
            self.config_entry.data.get(CONF_ROOMS, DEFAULT_ROOMS),
        )
        out: dict[str, dict[str, Any]] = {}
        for slug, r_conf in raw_rooms.items():
            if isinstance(r_conf, dict):
                out[slug] = {
                    CONF_ROOM_NAME: r_conf.get(CONF_ROOM_NAME, slug.replace("_", " ").title()),
                    CONF_ROOM_ZONE: r_conf.get(CONF_ROOM_ZONE, "downstairs"),
                    CONF_ROOM_TEMP_SENSOR: r_conf.get(CONF_ROOM_TEMP_SENSOR, ""),
                    CONF_ROOM_WINDOW_SENSORS: list(r_conf.get(CONF_ROOM_WINDOW_SENSORS, [])),
                    CONF_ROOM_INCLUDE_IN_AVG: bool(r_conf.get(CONF_ROOM_INCLUDE_IN_AVG, True)),
                    CONF_ROOM_ENABLE_TELEMETRY: bool(r_conf.get(CONF_ROOM_ENABLE_TELEMETRY, True)),
                }
        return out

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Presents categorized tuning menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "manage_rooms",
                "thermodynamic_settings",
                "veto_settings",
                "circulation_settings",
            ],
        )

    async def async_step_manage_rooms(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage per-room telemetry sensors, zone window contacts, and filter capacities."""
        opts = self.config_entry.options
        rooms = self._get_rooms()

        if user_input is not None:
            selected_room = user_input.get("selected_room", "__save_zone_windows__")
            self._pending_options = {
                CONF_UPSTAIRS_WINDOW_SENSORS: user_input.get(
                    CONF_UPSTAIRS_WINDOW_SENSORS, DEFAULT_UPSTAIRS_WINDOW_SENSORS
                ),
                CONF_DOWNSTAIRS_WINDOW_SENSORS: user_input.get(
                    CONF_DOWNSTAIRS_WINDOW_SENSORS, DEFAULT_DOWNSTAIRS_WINDOW_SENSORS
                ),
                CONF_FILTER_CAPACITY_UPSTAIRS: float(
                    user_input.get(CONF_FILTER_CAPACITY_UPSTAIRS, DEFAULT_FILTER_CAPACITY_HOURS)
                ),
                CONF_FILTER_CAPACITY_DOWNSTAIRS: float(
                    user_input.get(CONF_FILTER_CAPACITY_DOWNSTAIRS, DEFAULT_FILTER_CAPACITY_HOURS)
                ),
            }
            if selected_room == "__save_zone_windows__":
                new_options = {**opts, **self._pending_options, CONF_ROOMS: rooms}
                return self.async_create_entry(title="", data=new_options)

            self._editing_room_slug = str(selected_room)
            return await self.async_step_edit_room()

        room_options: list[selector.SelectOptionDict] = [
            selector.SelectOptionDict(
                value="__save_zone_windows__",
                label="Save Zone Window Contacts & Filter Settings Only",
            ),
            selector.SelectOptionDict(
                value="__add_new__",
                label="+ Add New Room / Area",
            ),
        ]
        for r_slug, r_conf in rooms.items():
            zone_label = str(r_conf.get(CONF_ROOM_ZONE, "downstairs")).capitalize()
            room_options.append(
                selector.SelectOptionDict(
                    value=r_slug,
                    label=f"Edit: {r_conf.get(CONF_ROOM_NAME, r_slug)} ({zone_label})",
                )
            )

        schema = vol.Schema(
            {
                vol.Required("selected_room", default="__save_zone_windows__"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=room_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_UPSTAIRS_WINDOW_SENSORS,
                    default=opts.get(CONF_UPSTAIRS_WINDOW_SENSORS, DEFAULT_UPSTAIRS_WINDOW_SENSORS),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
                ),
                vol.Required(
                    CONF_DOWNSTAIRS_WINDOW_SENSORS,
                    default=opts.get(CONF_DOWNSTAIRS_WINDOW_SENSORS, DEFAULT_DOWNSTAIRS_WINDOW_SENSORS),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
                ),
                vol.Required(
                    CONF_FILTER_CAPACITY_UPSTAIRS,
                    default=opts.get(CONF_FILTER_CAPACITY_UPSTAIRS, DEFAULT_FILTER_CAPACITY_HOURS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50.0, max=1000.0, step=10.0, unit_of_measurement="h")
                ),
                vol.Required(
                    CONF_FILTER_CAPACITY_DOWNSTAIRS,
                    default=opts.get(CONF_FILTER_CAPACITY_DOWNSTAIRS, DEFAULT_FILTER_CAPACITY_HOURS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50.0, max=1000.0, step=10.0, unit_of_measurement="h")
                ),
            }
        )
        return self.async_show_form(step_id="manage_rooms", data_schema=schema)

    async def async_step_edit_room(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit an existing room or configure a new room's sensors and zone assignment."""
        rooms = self._get_rooms()
        slug = self._editing_room_slug or "__add_new__"
        is_new = slug == "__add_new__"
        existing = rooms.get(slug, {})

        if user_input is not None:
            if not is_new and user_input.get("remove_room", False):
                rooms.pop(slug, None)
            else:
                room_name = str(user_input.get(CONF_ROOM_NAME, "")).strip() or "New Room"
                target_slug = slugify(room_name) if is_new else slug
                rooms[target_slug] = {
                    CONF_ROOM_NAME: room_name,
                    CONF_ROOM_ZONE: user_input.get(CONF_ROOM_ZONE, "downstairs"),
                    CONF_ROOM_TEMP_SENSOR: user_input.get(CONF_ROOM_TEMP_SENSOR, ""),
                    CONF_ROOM_WINDOW_SENSORS: list(user_input.get(CONF_ROOM_WINDOW_SENSORS, [])),
                    CONF_ROOM_INCLUDE_IN_AVG: bool(user_input.get(CONF_ROOM_INCLUDE_IN_AVG, True)),
                    CONF_ROOM_ENABLE_TELEMETRY: bool(user_input.get(CONF_ROOM_ENABLE_TELEMETRY, True)),
                }

            new_options = {
                **self.config_entry.options,
                **self._pending_options,
                CONF_ROOMS: rooms,
            }
            return self.async_create_entry(title="", data=new_options)

        default_name = existing.get(CONF_ROOM_NAME, "")
        default_zone = existing.get(CONF_ROOM_ZONE, "upstairs")
        default_temp = existing.get(CONF_ROOM_TEMP_SENSOR)
        default_windows = existing.get(CONF_ROOM_WINDOW_SENSORS, [])
        default_avg = existing.get(CONF_ROOM_INCLUDE_IN_AVG, True)
        default_telem = existing.get(CONF_ROOM_ENABLE_TELEMETRY, True)

        fields: dict[Any, Any] = {
            vol.Required(CONF_ROOM_NAME, default=default_name): str,
            vol.Required(CONF_ROOM_ZONE, default=default_zone): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="upstairs", label="Upstairs"),
                        selector.SelectOptionDict(value="downstairs", label="Downstairs"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_ROOM_TEMP_SENSOR,
                default=default_temp if default_temp else vol.UNDEFINED,
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
            ),
            vol.Optional(CONF_ROOM_WINDOW_SENSORS, default=default_windows): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
            vol.Required(CONF_ROOM_INCLUDE_IN_AVG, default=default_avg): selector.BooleanSelector(),
            vol.Required(CONF_ROOM_ENABLE_TELEMETRY, default=default_telem): selector.BooleanSelector(),
        }
        if not is_new:
            fields[vol.Optional("remove_room", default=False)] = selector.BooleanSelector()

        return self.async_show_form(step_id="edit_room", data_schema=vol.Schema(fields))

    async def async_step_thermodynamic_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjusts enthalpy, temperature deadbands, and MLR retraining parameters."""
        if user_input is not None:
            new_options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_MIN_ENTHALPY_DELTA, default=opts.get(CONF_MIN_ENTHALPY_DELTA, DEFAULT_MIN_ENTHALPY_DELTA)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=3.0, step=0.1, unit_of_measurement="BTU/lb")
                ),
                vol.Required(CONF_MAX_DEW_POINT, default=opts.get(CONF_MAX_DEW_POINT, DEFAULT_MAX_DEW_POINT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=45.0, max=65.0, step=0.5, unit_of_measurement="°F")
                ),
                vol.Required(CONF_OPEN_TEMP_MARGIN, default=opts.get(CONF_OPEN_TEMP_MARGIN, DEFAULT_OPEN_TEMP_MARGIN)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.5, max=3.0, step=0.1, unit_of_measurement="°F")
                ),
                vol.Required(CONF_CLOSE_TEMP_MARGIN, default=opts.get(CONF_CLOSE_TEMP_MARGIN, DEFAULT_CLOSE_TEMP_MARGIN)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.0, max=1.0, step=0.1, unit_of_measurement="°F")
                ),
                vol.Required(CONF_INFLUX_RETRAIN_HOURS, default=opts.get(CONF_INFLUX_RETRAIN_HOURS, DEFAULT_INFLUX_RETRAIN_HOURS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=6, max=168, step=6, unit_of_measurement="hours")
                ),
                vol.Required(CONF_INFLUX_LOOKBACK_DAYS, default=opts.get(CONF_INFLUX_LOOKBACK_DAYS, DEFAULT_INFLUX_LOOKBACK_DAYS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=30, max=730, step=30, unit_of_measurement="days")
                ),
            }
        )
        return self.async_show_form(step_id="thermodynamic_settings", data_schema=schema)

    async def async_step_veto_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjusts hazard safety limits and dwell timers."""
        if user_input is not None:
            new_options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_OPEN_DWELL_MINUTES, default=opts.get(CONF_OPEN_DWELL_MINUTES, DEFAULT_OPEN_DWELL_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=15, max=120, step=5, unit_of_measurement="min")
                ),
                vol.Required(CONF_FORECAST_LOOKAHEAD_MINUTES, default=opts.get(CONF_FORECAST_LOOKAHEAD_MINUTES, DEFAULT_FORECAST_LOOKAHEAD_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=30, max=180, step=15, unit_of_measurement="min")
                ),
                vol.Required(CONF_HIGH_WIND_SPEED, default=opts.get(CONF_HIGH_WIND_SPEED, DEFAULT_HIGH_WIND_SPEED)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10.0, max=30.0, step=1.0, unit_of_measurement="mph")
                ),
                vol.Required(CONF_HIGH_WIND_GUST, default=opts.get(CONF_HIGH_WIND_GUST, DEFAULT_HIGH_WIND_GUST)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=15.0, max=45.0, step=1.0, unit_of_measurement="mph")
                ),
                vol.Required(CONF_MAX_PRECIPITATION_PROBABILITY, default=opts.get(CONF_MAX_PRECIPITATION_PROBABILITY, DEFAULT_MAX_PRECIPITATION_PROBABILITY)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10.0, max=50.0, step=5.0, unit_of_measurement="%")
                ),
                vol.Required(CONF_AQI_THRESHOLD, default=opts.get(CONF_AQI_THRESHOLD, DEFAULT_AQI_THRESHOLD)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=50, max=300, step=5, unit_of_measurement="AQI")
                ),
                vol.Required(CONF_PREEMPTIVE_ECO_MODE, default=opts.get(CONF_PREEMPTIVE_ECO_MODE, DEFAULT_PREEMPTIVE_ECO_MODE)): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="veto_settings", data_schema=schema)

    async def async_step_circulation_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjusts convective circulation guardrails."""
        if user_input is not None:
            new_options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=new_options)

        opts = self.config_entry.options
        data = self.config_entry.data
        zones = data.get(CONF_ZONES, {})

        upstairs_default = opts.get(
            CONF_UPSTAIRS_CIRC_ENABLED,
            zones.get("upstairs", {}).get(CONF_CIRCULATION_ENABLED, False),
        )
        downstairs_default = opts.get(
            CONF_DOWNSTAIRS_CIRC_ENABLED,
            zones.get("downstairs", {}).get(CONF_CIRCULATION_ENABLED, False),
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_UPSTAIRS_CIRC_ENABLED, default=upstairs_default): selector.BooleanSelector(),
                vol.Required(CONF_DOWNSTAIRS_CIRC_ENABLED, default=downstairs_default): selector.BooleanSelector(),
                vol.Required(CONF_CIRC_DELTA_THRESHOLD, default=opts.get(CONF_CIRC_DELTA_THRESHOLD, DEFAULT_CIRC_DELTA_THRESHOLD)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=2.0, max=8.0, step=0.5, unit_of_measurement="°F")
                ),
                vol.Required(CONF_CIRC_MIN_MINUTES, default=opts.get(CONF_CIRC_MIN_MINUTES, DEFAULT_CIRC_MIN_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=10, max=30, step=5, unit_of_measurement="min")
                ),
                vol.Required(CONF_CIRC_MAX_MINUTES, default=opts.get(CONF_CIRC_MAX_MINUTES, DEFAULT_CIRC_MAX_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=60, max=240, step=15, unit_of_measurement="min")
                ),
                vol.Required(CONF_CIRC_STALL_MARGIN, default=opts.get(CONF_CIRC_STALL_MARGIN, DEFAULT_CIRC_STALL_MARGIN)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0.1, max=0.5, step=0.05, unit_of_measurement="°F")
                ),
                vol.Required(CONF_CIRC_LOCKOUT_HOURS, default=opts.get(CONF_CIRC_LOCKOUT_HOURS, DEFAULT_CIRC_LOCKOUT_HOURS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=6, step=1, unit_of_measurement="hours")
                ),
                vol.Required(CONF_CIRC_COOLDOWN_MINUTES, default=opts.get(CONF_CIRC_COOLDOWN_MINUTES, DEFAULT_CIRC_COOLDOWN_MINUTES)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=5, max=120, step=5, unit_of_measurement="min")
                ),
            }
        )
        return self.async_show_form(step_id="circulation_settings", data_schema=schema)

