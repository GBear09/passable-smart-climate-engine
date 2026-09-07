"""Constants for the Passable Smart Climate Engine integration."""

from homeassistant.const import Platform

DOMAIN = "passable_climate"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]

# Configuration Keys - Environment
CONF_WEATHER_ENTITY = "weather_entity"
CONF_SOLAR_POWER_ENTITY = "solar_power_entity"
CONF_SOLCAST_POWER_ENTITY = "solcast_power_entity"
CONF_HOME_MODE_ENTITY = "home_mode_entity"
CONF_PRESENCE_ENTITY = "presence_entity"
CONF_AQI_ENTITY = "aqi_entity"

# Configuration Keys - InfluxDB
CONF_INFLUX_HOST = "influx_host"
CONF_INFLUX_PORT = "influx_port"
CONF_INFLUX_VERSION = "influx_version"
CONF_INFLUX_DATABASE = "influx_database"
CONF_INFLUX_USERNAME = "influx_username"
CONF_INFLUX_PASSWORD = "influx_password"
CONF_INFLUX_TOKEN = "influx_token"
CONF_INFLUX_ORG = "influx_org"
CONF_INFLUX_BUCKET = "influx_bucket"
CONF_INFLUX_RETRAIN_HOURS = "retrain_interval_hours"
CONF_INFLUX_LOOKBACK_DAYS = "training_lookback_days"

# Configuration Keys - Zones
CONF_ZONES = "zones"
CONF_ZONE_NAME = "name"
CONF_CLIMATE_ENTITY = "climate_entity"
CONF_TEMP_SENSOR = "temperature_sensor"
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_OPEN_WINDOWS_SENSOR = "open_windows_sensor"
CONF_WINDOW_ECO_BOOLEAN = "window_eco_boolean"
CONF_CIRCULATION_ENABLED = "circulation_enabled"
CONF_CIRCULATION_SOURCE_TEMP = "circulation_source_temp"
CONF_CIRCULATION_ACTIVE_BOOLEAN = "circulation_active_boolean"
CONF_CIRCULATION_ECO_BOOLEAN = "circulation_eco_boolean"

# Configuration Keys - Schedules & Outputs
CONF_BEDTIME_START = "bedtime_start_entity"
CONF_PARENT_BEDTIME = "parent_bedtime_entity"
CONF_MORNING_START = "morning_start_entity"
CONF_COMFORT_PROFILE = "comfort_profile_entity"
CONF_RECOMMENDATION_ENTITY = "recommendation_entity"
CONF_PREDICT_HEAT_BOOLEAN = "predict_heat_boolean"
CONF_PREDICTION_MESSAGE_TEXT = "prediction_message_text"
CONF_COMFORT_RECOVERY_BOOLEAN = "comfort_recovery_boolean"

# Configuration Keys - Thermodynamic & Veto Thresholds
CONF_MIN_ENTHALPY_DELTA = "min_enthalpy_delta"
CONF_MAX_DEW_POINT = "max_dew_point"
CONF_OPEN_TEMP_MARGIN = "open_temp_margin"
CONF_CLOSE_TEMP_MARGIN = "close_temp_margin"
CONF_OPEN_DWELL_MINUTES = "open_dwell_minutes"
CONF_FORECAST_LOOKAHEAD_MINUTES = "forecast_lookahead_minutes"
CONF_HIGH_WIND_SPEED = "high_wind_speed"
CONF_HIGH_WIND_GUST = "high_wind_gust"
CONF_MAX_PRECIPITATION_PROBABILITY = "max_precipitation_probability"
CONF_AQI_THRESHOLD = "aqi_threshold"

# Configuration Keys - Circulation Guardrails
CONF_CIRC_DELTA_THRESHOLD = "circulation_delta_threshold"
CONF_CIRC_MIN_MINUTES = "circulation_min_minutes"
CONF_CIRC_MAX_MINUTES = "circulation_max_minutes"
CONF_CIRC_STALL_MARGIN = "circulation_stall_margin"
CONF_CIRC_LOCKOUT_HOURS = "circulation_lockout_hours"

# Defaults - Sensors & Entities
DEFAULT_WEATHER_ENTITY = "weather.home"
DEFAULT_SOLAR_POWER_ENTITY = "sensor.my_home_solar_power"
DEFAULT_SOLCAST_POWER_ENTITY = "sensor.solcast_pv_forecast_power_now"
DEFAULT_HOME_MODE_ENTITY = "input_select.home_mode"
DEFAULT_PRESENCE_ENTITY = "binary_sensor.someone_is_home"
DEFAULT_COMFORT_PROFILE_ENTITY = "pyscript.comfort_profile_parameters"

DEFAULT_BEDTIME_START = "input_datetime.max_s_bedtime"
DEFAULT_PARENT_BEDTIME = "input_datetime.parent_bedtime"
DEFAULT_MORNING_START = "input_datetime.morning_start_weekdays"
DEFAULT_RECOMMENDATION_ENTITY = "input_text.comfort_profile_overview"
DEFAULT_PREDICT_HEAT_BOOLEAN = "input_boolean.hvac_advisor_predicts_heat_needed"
DEFAULT_PREDICTION_MESSAGE_TEXT = "input_text.hvac_advisor_prediction_message"
DEFAULT_COMFORT_RECOVERY_BOOLEAN = "input_boolean.hvac_advisor_comfort_recovery_mode"

# Defaults - Thresholds
DEFAULT_MIN_ENTHALPY_DELTA = 1.2
DEFAULT_MAX_DEW_POINT = 58.0
DEFAULT_OPEN_TEMP_MARGIN = 1.0
DEFAULT_CLOSE_TEMP_MARGIN = 0.2
DEFAULT_OPEN_DWELL_MINUTES = 45
DEFAULT_FORECAST_LOOKAHEAD_MINUTES = 60
DEFAULT_HIGH_WIND_SPEED = 18.0
DEFAULT_HIGH_WIND_GUST = 25.0
DEFAULT_MAX_PRECIPITATION_PROBABILITY = 25.0
DEFAULT_AQI_THRESHOLD = 50.0

DEFAULT_CIRC_DELTA_THRESHOLD = 4.0
DEFAULT_CIRC_MIN_MINUTES = 15
DEFAULT_CIRC_MAX_MINUTES = 120
DEFAULT_CIRC_STALL_MARGIN = 0.2
DEFAULT_CIRC_LOCKOUT_HOURS = 1

DEFAULT_INFLUX_HOST = "localhost"
DEFAULT_INFLUX_PORT = 8086
DEFAULT_INFLUX_VERSION = "1"
DEFAULT_INFLUX_DATABASE = "homeassistant"
DEFAULT_INFLUX_RETRAIN_HOURS = 6
DEFAULT_INFLUX_LOOKBACK_DAYS = 365

# Plot Metric Series Definitions (For the 16 Dashboard Plot Entities)
METRIC_TEMP_CLOSED = "temp_profile_win_closed"
METRIC_TEMP_OPEN = "temp_profile_win_open"
METRIC_TEMP_HEAT = "temp_profile_hvac_heat"
METRIC_TEMP_COOL = "temp_profile_hvac_cool"

METRIC_HUMIDITY_CLOSED = "humidity_profile_win_closed"
METRIC_HUMIDITY_OPEN = "humidity_profile_win_open"
METRIC_HUMIDITY_HEAT = "humidity_profile_hvac_heat"
METRIC_HUMIDITY_COOL = "humidity_profile_hvac_cool"

PLOT_METRIC_KEYS = [
    METRIC_TEMP_CLOSED,
    METRIC_TEMP_OPEN,
    METRIC_TEMP_HEAT,
    METRIC_TEMP_COOL,
    METRIC_HUMIDITY_CLOSED,
    METRIC_HUMIDITY_OPEN,
    METRIC_HUMIDITY_HEAT,
    METRIC_HUMIDITY_COOL,
]
