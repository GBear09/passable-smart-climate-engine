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
    DEFAULT_PRESENCE_ENTITY,
    DEFAULT_RECOMMENDATION_ENTITY,
    DEFAULT_SOLAR_POWER_ENTITY,
    DEFAULT_SOLCAST_POWER_ENTITY,
    DEFAULT_WEATHER_ENTITY,
    DOMAIN,
)
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
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Presents categorized tuning menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "thermodynamic_settings",
                "veto_settings",
                "circulation_settings",
            ],
        )

    async def async_step_thermodynamic_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjusts enthalpy and temperature deadbands."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

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
            }
        )
        return self.async_show_form(step_id="thermodynamic_settings", data_schema=schema)

    async def async_step_veto_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjusts hazard safety limits and dwell timers."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

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
            }
        )
        return self.async_show_form(step_id="veto_settings", data_schema=schema)

    async def async_step_circulation_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Adjusts convective circulation guardrails."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        schema = vol.Schema(
            {
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
            }
        )
        return self.async_show_form(step_id="circulation_settings", data_schema=schema)
