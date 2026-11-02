"""Unit tests for determine_seasonal_mode in Passable Climate Advisor."""

import datetime
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.engines.advisor import determine_seasonal_mode


class TestAdvisorSeasonalMode(unittest.TestCase):
    """Test macro seasonal transitions and calendar month gating."""

    def setUp(self):
        self.comfort_profile = {
            "type": "psychrometric_box",
            "temp_min": 68.0,
            "temp_max": 76.0,
        }
        self.active_heat_sp = 68.0
        self.active_cool_sp = 74.0

    def _generate_forecast(self, start_dt: datetime.datetime, temps: list[float]) -> list[dict]:
        forecast = []
        for i, t in enumerate(temps):
            forecast.append({
                "datetime": (start_dt + datetime.timedelta(hours=i)).isoformat(),
                "temperature": float(t),
                "humidity": 50.0,
                "condition": "partlycloudy",
                "wind_speed": 5.0,
            })
        return forecast

    def test_november_heat_mode_mild_forecast_remains_normal(self):
        """In November (Northern Hemisphere), a warm spell in heat mode must NOT trigger Spring Transition."""
        start = datetime.datetime(2026, 11, 2, 8, 0, tzinfo=datetime.timezone.utc)
        # 7-day forecast with daily highs around 70°F and nights in the 50s-60s -> average ~64°F (> min_comfort - 5.0 = 63°F)
        temps = [64.0] * (7 * 24)
        forecast = self._generate_forecast(start, temps)

        mode, details = determine_seasonal_mode(
            hvac_mode="heat",
            forecast=forecast,
            comfort_profile=self.comfort_profile,
            active_heat_sp=self.active_heat_sp,
            active_cool_sp=self.active_cool_sp,
            latitude=38.8,
        )

        self.assertEqual(mode, "normal")
        self.assertIsNone(details, f"Expected no seasonal details in November, got: {details}")

    def test_april_heat_mode_warm_forecast_triggers_spring_transition(self):
        """In April (Northern Hemisphere), a warm spell in heat mode triggers Spring Transition if 48h high >= cool_sp."""
        start = datetime.datetime(2026, 4, 15, 8, 0, tzinfo=datetime.timezone.utc)
        # 7-day forecast averaging 66°F, with 48h peak reaching 75°F (>= active_cool_sp 74.0)
        temps = [66.0] * (7 * 24)
        temps[14] = 75.0  # peak in first 48 hours
        forecast = self._generate_forecast(start, temps)

        mode, details = determine_seasonal_mode(
            hvac_mode="heat",
            forecast=forecast,
            comfort_profile=self.comfort_profile,
            active_heat_sp=self.active_heat_sp,
            active_cool_sp=self.active_cool_sp,
            latitude=38.8,
        )

        self.assertEqual(mode, "spring_transition")
        self.assertIsNotNone(details)
        self.assertIn("Spring Transition", details)

    def test_april_heat_mode_warm_forecast_suspended_if_48h_peak_cool(self):
        """In April, Spring Transition is suspended if 48h peak is below active_cool_sp."""
        start = datetime.datetime(2026, 4, 15, 8, 0, tzinfo=datetime.timezone.utc)
        # 7-day forecast averaging 66°F, but next 48h peak is only 70°F (< active_cool_sp 74.0)
        temps = [66.0] * (7 * 24)
        for i in range(48):
            temps[i] = 62.0
        forecast = self._generate_forecast(start, temps)

        mode, details = determine_seasonal_mode(
            hvac_mode="heat",
            forecast=forecast,
            comfort_profile=self.comfort_profile,
            active_heat_sp=self.active_heat_sp,
            active_cool_sp=self.active_cool_sp,
            latitude=38.8,
        )

        self.assertEqual(mode, "normal")
        self.assertIsNotNone(details)
        self.assertIn("suspended for today", details)

    def test_september_cool_mode_cool_forecast_triggers_fall_transition(self):
        """In September (Northern Hemisphere), cool weather in cool mode triggers Fall Transition."""
        start = datetime.datetime(2026, 9, 20, 8, 0, tzinfo=datetime.timezone.utc)
        # 7-day forecast averaging 60°F (< min_comfort 68.0), upcoming 48h low is 58°F (<= active_heat_sp 68.0)
        temps = [60.0] * (7 * 24)
        temps[10] = 58.0
        forecast = self._generate_forecast(start, temps)

        mode, details = determine_seasonal_mode(
            hvac_mode="cool",
            forecast=forecast,
            comfort_profile=self.comfort_profile,
            active_heat_sp=self.active_heat_sp,
            active_cool_sp=self.active_cool_sp,
            latitude=38.8,
        )

        self.assertEqual(mode, "fall_transition")
        self.assertIsNotNone(details)
        self.assertIn("Fall Transition", details)

    def test_may_cool_mode_cool_spell_does_not_trigger_fall_transition(self):
        """In May (Northern Hemisphere), a cool spell in cool mode must NOT trigger Fall Transition."""
        start = datetime.datetime(2026, 5, 10, 8, 0, tzinfo=datetime.timezone.utc)
        temps = [60.0] * (7 * 24)
        forecast = self._generate_forecast(start, temps)

        mode, details = determine_seasonal_mode(
            hvac_mode="cool",
            forecast=forecast,
            comfort_profile=self.comfort_profile,
            active_heat_sp=self.active_heat_sp,
            active_cool_sp=self.active_cool_sp,
            latitude=38.8,
        )

        self.assertEqual(mode, "normal")
        self.assertIsNone(details)

    def test_southern_hemisphere_latitudes(self):
        """In the Southern Hemisphere (latitude < 0), months are reversed (November is Spring)."""
        start = datetime.datetime(2026, 11, 2, 8, 0, tzinfo=datetime.timezone.utc)
        temps = [66.0] * (7 * 24)
        temps[12] = 75.0  # peak >= active_cool_sp
        forecast = self._generate_forecast(start, temps)

        mode, details = determine_seasonal_mode(
            hvac_mode="heat",
            forecast=forecast,
            comfort_profile=self.comfort_profile,
            active_heat_sp=self.active_heat_sp,
            active_cool_sp=self.active_cool_sp,
            latitude=-33.86,  # Sydney
        )

        self.assertEqual(mode, "spring_transition")
        self.assertIsNotNone(details)


if __name__ == "__main__":
    unittest.main()
