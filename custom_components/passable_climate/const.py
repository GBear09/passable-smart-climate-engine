"""Constants for the Passable Smart Climate Engine integration."""

from homeassistant.const import Platform

DOMAIN = "passable_climate"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER]

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
CONF_HVAC_TRANSITION_OFFSET = "hvac_transition_offset_entity"

# Configuration Keys - Psychrometric Box Comfort Envelope
CONF_COMFORT_TEMP_MIN = "comfort_temp_min"
CONF_COMFORT_TEMP_MAX = "comfort_temp_max"
CONF_COMFORT_HUMIDITY_MIN = "comfort_humidity_min"
CONF_COMFORT_HUMIDITY_MAX = "comfort_humidity_max"
CONF_COMFORT_DEW_POINT_MAX = "comfort_dew_point_max"

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
CONF_PREEMPTIVE_ECO_MODE = "preemptive_eco_mode"

# Configuration Keys - Circulation Guardrails
CONF_UPSTAIRS_CIRC_ENABLED = "upstairs_circ_enabled"
CONF_DOWNSTAIRS_CIRC_ENABLED = "downstairs_circ_enabled"
CONF_CIRC_DELTA_THRESHOLD = "circulation_delta_threshold"
CONF_CIRC_MIN_MINUTES = "circulation_min_minutes"
CONF_CIRC_MAX_MINUTES = "circulation_max_minutes"
CONF_CIRC_STALL_MARGIN = "circulation_stall_margin"
CONF_CIRC_LOCKOUT_HOURS = "circulation_lockout_hours"
CONF_CIRC_COOLDOWN_MINUTES = "circulation_cooldown_minutes"

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
DEFAULT_HVAC_TRANSITION_OFFSET_ENTITY = "input_number.hvac_transition_offset"
DEFAULT_HVAC_TRANSITION_OFFSET = 2.0

# Defaults - Psychrometric Box Comfort Envelope
DEFAULT_COMFORT_TEMP_MIN = 64.5
DEFAULT_COMFORT_TEMP_MAX = 76.0
DEFAULT_COMFORT_HUMIDITY_MIN = 25.0
DEFAULT_COMFORT_HUMIDITY_MAX = 60.0
DEFAULT_COMFORT_DEW_POINT_MAX = 58.0
DEFAULT_COMFORT_ROLLOFF_TEMP = 75.0

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
DEFAULT_PREEMPTIVE_ECO_MODE = False

DEFAULT_CIRC_DELTA_THRESHOLD = 4.0
DEFAULT_CIRC_MIN_MINUTES = 15
DEFAULT_CIRC_MAX_MINUTES = 120
DEFAULT_CIRC_STALL_MARGIN = 0.2
DEFAULT_CIRC_LOCKOUT_HOURS = 1
DEFAULT_CIRC_COOLDOWN_MINUTES = 30

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

# Configuration Keys - Room Telemetry & Window Contacts
CONF_ROOMS = "rooms"
CONF_ROOM_NAME = "name"
CONF_ROOM_ZONE = "zone"
CONF_ROOM_TEMP_SENSOR = "temp_sensor"
CONF_ROOM_WINDOW_SENSORS = "window_sensors"
CONF_ROOM_INCLUDE_IN_AVG = "include_in_zone_average"
CONF_ROOM_ENABLE_TELEMETRY = "enable_room_telemetry"
CONF_UPSTAIRS_WINDOW_SENSORS = "upstairs_window_sensors"
CONF_DOWNSTAIRS_WINDOW_SENSORS = "downstairs_window_sensors"
CONF_FILTER_CAPACITY_UPSTAIRS = "filter_capacity_upstairs"
CONF_FILTER_CAPACITY_DOWNSTAIRS = "filter_capacity_downstairs"

DEFAULT_FILTER_CAPACITY_HOURS = 300.0

DEFAULT_UPSTAIRS_WINDOW_SENSORS = [
    "binary_sensor.max_s_window_driveway_window_door_is_open_2",
    "binary_sensor.bathroom_window_window_door_is_open_2",
    "binary_sensor.master_bed_window_front_right_window_door_is_open_2",
    "binary_sensor.maggie_s_window_side_yard_window_door_is_open_2",
]

DEFAULT_DOWNSTAIRS_WINDOW_SENSORS = [
    "binary_sensor.kitchen_window_sink_window_door_is_open_2",
    "binary_sensor.dining_room_window_left_window_door_is_open_2",
    "binary_sensor.playroom_window_front_right_window_door_is_open_3",
]

DEFAULT_ROOMS: dict[str, dict[str, object]] = {
    "living_room": {
        CONF_ROOM_NAME: "Living Room",
        CONF_ROOM_ZONE: "downstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.living_room_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.dining_room_window_left_window_door_is_open_2"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "dining_room": {
        CONF_ROOM_NAME: "Dining Room",
        CONF_ROOM_ZONE: "downstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.downstairs_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.dining_room_window_left_window_door_is_open_2"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "kitchen": {
        CONF_ROOM_NAME: "Kitchen",
        CONF_ROOM_ZONE: "downstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.kitchen_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.kitchen_window_sink_window_door_is_open_2"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "playroom": {
        CONF_ROOM_NAME: "Playroom",
        CONF_ROOM_ZONE: "downstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.playroom_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.playroom_window_front_right_window_door_is_open_3"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "bathroom": {
        CONF_ROOM_NAME: "Bathroom",
        CONF_ROOM_ZONE: "downstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.bathroom_temperature",
        CONF_ROOM_WINDOW_SENSORS: [],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: False,
    },
    "master_bedroom": {
        CONF_ROOM_NAME: "Master Bedroom",
        CONF_ROOM_ZONE: "upstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.master_bedroom_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.master_bed_window_front_right_window_door_is_open_2"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "maggies_room": {
        CONF_ROOM_NAME: "Maggie's Room",
        CONF_ROOM_ZONE: "upstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.maggies_room_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.maggie_s_window_side_yard_window_door_is_open_2"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "maxs_room": {
        CONF_ROOM_NAME: "Max's Room",
        CONF_ROOM_ZONE: "upstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.maxs_room_temperature",
        CONF_ROOM_WINDOW_SENSORS: ["binary_sensor.max_s_window_driveway_window_door_is_open_2"],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "office": {
        CONF_ROOM_NAME: "Office",
        CONF_ROOM_ZONE: "upstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.office_temperature",
        CONF_ROOM_WINDOW_SENSORS: [],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
    "upstairs": {
        CONF_ROOM_NAME: "Upstairs Hallway",
        CONF_ROOM_ZONE: "upstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.upstairs_temperature",
        CONF_ROOM_WINDOW_SENSORS: [],
        CONF_ROOM_INCLUDE_IN_AVG: True,
        CONF_ROOM_ENABLE_TELEMETRY: False,
    },
    "madeleines_room": {
        CONF_ROOM_NAME: "Madeleine's Room",
        CONF_ROOM_ZONE: "upstairs",
        CONF_ROOM_TEMP_SENSOR: "sensor.madeleines_room_temperature",
        CONF_ROOM_WINDOW_SENSORS: [],
        CONF_ROOM_INCLUDE_IN_AVG: False,
        CONF_ROOM_ENABLE_TELEMETRY: True,
    },
}

# Canonical 8 MLR Rate Sensor Specifications per Zone (16 total across Upstairs & Downstairs)
ZONE_MLR_RATE_SPECS = [
    (
        "{zone}_heat_loss_gain_windows_closed",
        "Heat Loss/Gain (Windows Closed)",
        "°F/h",
        "mdi:home-thermometer-outline",
        "temp_closed",
    ),
    (
        "{zone}_heat_loss_gain_windows_open",
        "Heat Loss/Gain (Windows Open)",
        "°F/h",
        "mdi:window-open-variant",
        "temp_open",
    ),
    (
        "hvac_heating_rate_{zone}",
        "HVAC Heating Performance",
        "°F/h",
        "mdi:fire",
        "temp_heat",
    ),
    (
        "hvac_cooling_rate_{zone}",
        "HVAC Cooling Performance",
        "°F/h",
        "mdi:snowflake",
        "temp_cool",
    ),
    (
        "humidity_rate_windows_closed_{zone}",
        "Humidity Change (Windows Closed)",
        "g/m³/h",
        "mdi:water-percent",
        "hum_closed",
    ),
    (
        "humidity_rate_windows_open_{zone}",
        "Humidity Change (Windows Open)",
        "g/m³/h",
        "mdi:water-outline",
        "hum_open",
    ),
    (
        "humidity_rate_heating_{zone}",
        "Humidity Change (HVAC Heating)",
        "g/m³/h",
        "mdi:water-minus",
        "hum_heat",
    ),
    (
        "humidity_rate_cooling_{zone}",
        "Humidity Change (HVAC Cooling)",
        "g/m³/h",
        "mdi:water-check",
        "hum_cool",
    ),
]

