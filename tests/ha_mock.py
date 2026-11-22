"""Consistent Home Assistant mocks for the unit test suite."""

import sys
from unittest import mock


class MockModule(mock.MagicMock):
    __path__ = []


class DummyDataUpdateCoordinator:
    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.logger = logger
        self.last_update_success = True
        self.data = {}

    def __class_getitem__(cls, item):
        return cls


class DummyCoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls


class DummySensorEntity:
    pass


class DummyTempConverter:
    @staticmethod
    def convert(val, from_u, to_u):
        if str(from_u).lower() in ("c", "°c", "celsius"):
            return (float(val) * 9.0 / 5.0) + 32.0
        return float(val)


class DummySpeedConverter:
    @staticmethod
    def convert(val, from_u, to_u):
        if str(from_u).lower() in ("m/s", "mps"):
            return float(val) * 2.23694
        if str(from_u).lower() in ("km/h", "kmh"):
            return float(val) * 0.621371
        return float(val)


def setup_ha_mocks():
    """Initializes standard mocks for homeassistant in sys.modules."""
    coord_module = mock.MagicMock()
    coord_module.DataUpdateCoordinator = DummyDataUpdateCoordinator
    coord_module.CoordinatorEntity = DummyCoordinatorEntity
    coord_module.UpdateFailed = Exception

    conv_module = mock.MagicMock()
    conv_module.TemperatureConverter = DummyTempConverter
    conv_module.SpeedConverter = DummySpeedConverter
    conv_module.PowerConverter = mock.MagicMock()

    ha_root = MockModule()
    helpers_module = MockModule()
    util_module = MockModule()

    import datetime
    dt_module = mock.MagicMock()
    dt_module.parse_datetime = lambda s: datetime.datetime.fromisoformat(str(s))
    dt_module.utcnow = lambda: datetime.datetime.now(datetime.timezone.utc)
    dt_module.now = lambda: datetime.datetime.now(datetime.timezone.utc)
    dt_module.as_utc = lambda dt: dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)
    dt_module.as_local = lambda dt: dt

    event_module = mock.MagicMock()
    start_module = mock.MagicMock()
    storage_module = mock.MagicMock()
    entity_module = mock.MagicMock()
    entity_module.DeviceInfo = mock.MagicMock()
    entity_platform_module = mock.MagicMock()
    entity_registry_module = mock.MagicMock()
    components_module = MockModule()
    sensor_module = mock.MagicMock()
    sensor_module.SensorEntity = DummySensorEntity
    sensor_module.SensorDeviceClass = mock.MagicMock()
    sensor_module.SensorStateClass = mock.MagicMock()
    components_module.sensor = sensor_module

    helpers_module.event = event_module
    helpers_module.start = start_module
    helpers_module.update_coordinator = coord_module
    helpers_module.storage = storage_module
    helpers_module.entity = entity_module
    helpers_module.entity_platform = entity_platform_module
    helpers_module.entity_registry = entity_registry_module

    util_module.dt = dt_module
    util_module.unit_conversion = conv_module

    ha_root.components = components_module
    ha_root.config_entries = mock.MagicMock()
    ha_root.const = mock.MagicMock()
    ha_root.core = mock.MagicMock()
    ha_root.helpers = helpers_module
    ha_root.util = util_module

    sys.modules["homeassistant"] = ha_root
    sys.modules["homeassistant.components"] = components_module
    sys.modules["homeassistant.components.sensor"] = sensor_module
    sys.modules["homeassistant.config_entries"] = ha_root.config_entries
    sys.modules["homeassistant.const"] = ha_root.const
    sys.modules["homeassistant.core"] = ha_root.core
    sys.modules["homeassistant.helpers"] = helpers_module
    sys.modules["homeassistant.helpers.entity"] = entity_module
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform_module
    sys.modules["homeassistant.helpers.entity_registry"] = entity_registry_module
    sys.modules["homeassistant.helpers.event"] = event_module
    sys.modules["homeassistant.helpers.start"] = start_module
    sys.modules["homeassistant.helpers.update_coordinator"] = coord_module
    sys.modules["homeassistant.helpers.storage"] = storage_module
    sys.modules["homeassistant.util"] = util_module
    sys.modules["homeassistant.util.dt"] = dt_module
    sys.modules["homeassistant.util.unit_conversion"] = conv_module


setup_ha_mocks()
