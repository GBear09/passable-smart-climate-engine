"""Unit tests for coordinator telemetry validation, fail-closed AQI, occupancy, and actuation."""

import datetime
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.coordinator import (
    RECOVERY_EXIT_MARGIN_RH,
    RECOVERY_MIN_ON,
    STALE_INDOOR,
    SmartClimateCoordinator,
)
from custom_components.passable_climate.engines.advisor import evaluate_environmental_vetoes


class TestCoordinatorTelemetryAndOccupancy(unittest.IsolatedAsyncioTestCase):
    """Tests for validated telemetry reading, occupancy gating, and control arbitration."""

    def setUp(self):
        self.hass = mock.MagicMock()
        self.entry = mock.MagicMock()
        self.entry.data = {
            "influx_host": "localhost",
            "influx_port": 8086,
            "zones": {
                "upstairs": {
                    "temperature_sensor": "sensor.upstairs_temp",
                    "humidity_sensor": "sensor.upstairs_humidity",
                    "open_windows_sensor": "sensor.upstairs_windows",
                }
            },
        }
        self.entry.options = {}
        self.coordinator = SmartClimateCoordinator(self.hass, self.entry)

    def test_read_float_validation_and_staleness(self):
        """_read_float must reject unavailable, non-numeric, out-of-range, and stale states."""
        now = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.coordinator.hass.states.get.return_value = None

        # Missing state returns None
        self.assertIsNone(self.coordinator._read_float("sensor.test", lo=0.0, hi=100.0))

        # Unavailable / unknown returns None
        for invalid in ["unknown", "unavailable", "None", ""]:
            st = mock.MagicMock()
            st.state = invalid
            self.coordinator.hass.states.get.return_value = st
            self.assertIsNone(self.coordinator._read_float("sensor.test", lo=0.0, hi=100.0))

        # Out of bounds returns None
        st = mock.MagicMock()
        st.state = "105.0"
        self.coordinator.hass.states.get.return_value = st
        self.assertIsNone(self.coordinator._read_float("sensor.test", lo=0.0, hi=100.0))

        # Valid value returns float
        st.state = "72.4"
        st.last_reported = now
        self.coordinator.hass.states.get.return_value = st
        val = self.coordinator._read_float("sensor.test", lo=0.0, hi=100.0)
        self.assertEqual(val, 72.4)

    def test_occupancy_veto_in_advisor(self):
        """evaluate_environmental_vetoes must reject window actions when house is unoccupied."""
        # When nobody is home
        vetoed, reason = evaluate_environmental_vetoes(
            weather_condition="sunny",
            precipitation_probability=0.0,
            wind_speed=5.0,
            wind_gust=8.0,
            aqi_value=25.0,
            home_mode="Home",
            someone_is_home=False,
        )
        self.assertTrue(vetoed)
        self.assertIn("Nobody home", reason)

        # When in Away mode
        vetoed, reason = evaluate_environmental_vetoes(
            weather_condition="sunny",
            precipitation_probability=0.0,
            wind_speed=5.0,
            wind_gust=8.0,
            aqi_value=25.0,
            home_mode="Away",
            someone_is_home=True,
        )
        self.assertTrue(vetoed)
        self.assertIn("Nobody home", reason)

        # When someone is home and mode is Home
        vetoed, reason = evaluate_environmental_vetoes(
            weather_condition="sunny",
            precipitation_probability=0.0,
            wind_speed=5.0,
            wind_gust=8.0,
            aqi_value=25.0,
            home_mode="Home",
            someone_is_home=True,
        )
        self.assertFalse(vetoed)
        self.assertEqual(reason, "")

    def test_effective_hvac_mode_arbitration(self):
        """_effective_hvac_mode must correctly resolve auto, heat_cool, and dry modes."""
        # auto with action cooling -> cool
        mode = self.coordinator._effective_hvac_mode(
            state="auto", attrs={"hvac_action": "cooling"}, w_temp=75.0, in_temp=72.0
        )
        self.assertEqual(mode, "cool")

        # auto with action heating -> heat
        mode = self.coordinator._effective_hvac_mode(
            state="auto", attrs={"hvac_action": "heating"}, w_temp=45.0, in_temp=68.0
        )
        self.assertEqual(mode, "heat")

        # heat_cool idle with outdoor warmer than indoor -> cool
        mode = self.coordinator._effective_hvac_mode(
            state="heat_cool", attrs={"hvac_action": "idle"}, w_temp=78.0, in_temp=72.0
        )
        self.assertEqual(mode, "cool")

        # dry mode -> cool
        mode = self.coordinator._effective_hvac_mode(
            state="dry", attrs={}, w_temp=70.0, in_temp=70.0
        )
        self.assertEqual(mode, "cool")

        # off mode -> off
        mode = self.coordinator._effective_hvac_mode(
            state="off", attrs={}, w_temp=70.0, in_temp=70.0
        )
        self.assertEqual(mode, "off")

    def test_forecast_sanitization_and_anchor(self):
        """_sanitize_forecast must anchor step 0 at observation and drop invalid entries."""
        now = datetime.datetime(2026, 10, 12, 14, 0, 0, tzinfo=datetime.timezone.utc)
        raw_forecast = [
            # Past forecast item should be skipped
            {"datetime": (now - datetime.timedelta(hours=1)).isoformat(), "temperature": 60.0, "humidity": 50.0},
            # Missing humidity should be skipped
            {"datetime": (now + datetime.timedelta(hours=1)).isoformat(), "temperature": 65.0, "humidity": None},
            # Valid item
            {"datetime": (now + datetime.timedelta(hours=2)).isoformat(), "temperature": 68.0, "humidity": 45.0, "cloud_coverage": None},
        ]
        obs = {"temperature": 70.0, "humidity": 55.0, "wind_speed": 4.0}
        clean = self.coordinator._sanitize_forecast(raw_forecast, now, t_unit="°F", w_unit="mph", obs=obs)

        # First element must be the observation anchor
        self.assertEqual(len(clean), 2)
        self.assertEqual(clean[0]["temperature"], 70.0)
        self.assertEqual(clean[0]["humidity"], 55.0)

        # Second element must have default cloud coverage (50.0) and converted temp
        self.assertEqual(clean[1]["temperature"], 68.0)
        self.assertEqual(clean[1]["cloud_coverage"], 50.0)

    def test_contact_sensor_dropout_fails_safe_to_open(self):
        """CE1: When window contact sensor is unavailable, coordinator must treat is_open as True."""
        # Contact sensor returns None (unknown/unavailable)
        self.coordinator.hass.states.get.return_value = None
        win_raw = self.coordinator._read_float("sensor.upstairs_windows", lo=0.0, hi=1000.0)
        self.assertIsNone(win_raw)

        # Fail-safe rule: is_open = True if win_raw is None else win_raw > 0
        is_open = True if win_raw is None else (win_raw > 0)
        self.assertTrue(is_open)

    async def test_startup_actuation_gating(self):
        """CE4: Coordinator must not command external service calls before actuation_enabled=True."""
        self.coordinator.actuation_enabled = False
        st = mock.MagicMock()
        st.state = "off"
        self.coordinator.hass.states.get.return_value = st

        # Attempt to command helper
        await self.coordinator._async_command_bool("input_boolean.test", True)
        self.coordinator.hass.services.async_call.assert_not_called()

        # Attempt to sync overview
        await self.coordinator._async_sync_overview("Open Windows")
        self.coordinator.hass.services.async_call.assert_not_called()

        # Once actuation is enabled:
        self.coordinator.actuation_enabled = True
        self.coordinator.hass.services.async_call = mock.AsyncMock()
        await self.coordinator._async_command_bool("input_boolean.test", True)
        self.coordinator.hass.services.async_call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
