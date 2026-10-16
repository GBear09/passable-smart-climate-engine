"""Unit tests for humidity recovery HVAC mode safeguards in Passable Climate Advisor."""

import datetime
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.core.models import RateModel
from custom_components.passable_climate.engines.advisor import evaluate_zone_plan


class TestAdvisorHumiditySafeguards(unittest.TestCase):
    """Verify that high/low humidity recovery respects temperature and season."""

    def setUp(self):
        self.now = datetime.datetime(2026, 10, 8, 8, 30, tzinfo=datetime.timezone.utc)
        self.comfort_profile = {
            "type": "psychrometric_box",
            "temp_min": 64.0,
            "temp_max": 76.0,
            "humidity_min": 25.0,
            "humidity_max": 60.0,
            "dew_point_max": 58.0,
        }
        # Flat forecast over 12 hours: 56°F, 92% RH (cool, damp outdoor air)
        self.cool_damp_forecast = [
            {
                "datetime": (self.now + datetime.timedelta(hours=i)).isoformat(),
                "temperature": 56.0,
                "humidity": 92.0,
                "condition": "cloudy",
                "wind_speed": 5.0,
            }
            for i in range(16)
        ]
        # Summer forecast: 85°F, 70% RH
        self.hot_humid_forecast = [
            {
                "datetime": (self.now + datetime.timedelta(hours=i)).isoformat(),
                "temperature": 85.0,
                "humidity": 70.0,
                "condition": "sunny",
                "wind_speed": 5.0,
            }
            for i in range(16)
        ]
        # Models where closed humidity barely changes (no passive recovery)
        self.models = {
            "temp_closed": RateModel("ransac", slope=0.01, intercept=0.0),
            "temp_open": RateModel("ransac", slope=0.3, intercept=0.0),
            "humidity_closed": RateModel("ransac", slope=0.0, intercept=0.0),
            "humidity_open": RateModel("ransac", slope=0.2, intercept=0.0),
        }

    def test_fall_transition_cool_house_high_humidity_does_not_recommend_cool(self):
        """In Fall Transition with 65°F indoor temp and 61% RH, AC cool mode must NOT be recommended."""
        plan = evaluate_zone_plan(
            zone_name="Upstairs",
            inside_temp=65.9,
            inside_humidity=61.0,
            forecast=self.cool_damp_forecast,
            current_outside_temp=56.0,
            models=self.models,
            hvac_mode="off",
            seasonal_mode="fall_transition",
            comfort_profile=self.comfort_profile,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            lat=39.0,
            lon=-77.0,
            active_heat_sp=64.0,
            active_cool_sp=75.0,
            now=self.now,
        )

        self.assertEqual(plan.recommended_state, "Close Windows")
        self.assertNotIn("change to 'cool' mode", plan.details_message)
        self.assertNotIn("HVAC is needed", plan.details_message)
        self.assertIn("retain heat", plan.details_message)
        self.assertIn("Dehumidification is needed", plan.details_message)
        self.assertFalse(plan.comfort_recovery_triggered)
        self.assertEqual(plan.scenario, "fall_trap_heat")

    def test_summer_warm_house_high_humidity_recommends_cool(self):
        """In Summer with 78°F indoor temp and 68% RH, AC cool mode should be recommended."""
        plan = evaluate_zone_plan(
            zone_name="Upstairs",
            inside_temp=78.0,
            inside_humidity=68.0,
            forecast=self.hot_humid_forecast,
            current_outside_temp=85.0,
            models=self.models,
            hvac_mode="off",
            seasonal_mode="summer",
            comfort_profile=self.comfort_profile,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            lat=39.0,
            lon=-77.0,
            active_heat_sp=64.0,
            active_cool_sp=75.0,
            now=self.now,
        )

        self.assertEqual(plan.recommended_state, "Close Windows")
        self.assertIn("A change to 'cool' mode is needed", plan.details_message)
        self.assertTrue(plan.comfort_recovery_triggered)
        self.assertEqual(plan.scenario, "comfort_recovery")

    def test_summer_already_in_cool_mode_high_humidity(self):
        """In Summer with 74°F indoor temp already in cool mode, confirms HVAC is needed."""
        plan = evaluate_zone_plan(
            zone_name="Upstairs",
            inside_temp=74.0,
            inside_humidity=68.0,
            forecast=self.hot_humid_forecast,
            current_outside_temp=85.0,
            models=self.models,
            hvac_mode="cool",
            seasonal_mode="summer",
            comfort_profile=self.comfort_profile,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            lat=39.0,
            lon=-77.0,
            active_heat_sp=64.0,
            active_cool_sp=75.0,
            now=self.now,
        )

        self.assertEqual(plan.recommended_state, "Close Windows")
        self.assertIn("HVAC is needed", plan.details_message)
        self.assertNotIn("change to", plan.details_message)
        self.assertTrue(plan.comfort_recovery_triggered)

    def test_heat_mode_active_high_humidity_does_not_switch_to_cool(self):
        """When active in heat mode, high humidity advises dehumidification without switching to cool."""
        plan = evaluate_zone_plan(
            zone_name="Upstairs",
            inside_temp=68.0,
            inside_humidity=65.0,
            forecast=self.cool_damp_forecast,
            current_outside_temp=45.0,
            models=self.models,
            hvac_mode="heat",
            seasonal_mode="winter",
            comfort_profile=self.comfort_profile,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            lat=39.0,
            lon=-77.0,
            active_heat_sp=68.0,
            active_cool_sp=75.0,
            now=self.now,
        )

        self.assertEqual(plan.recommended_state, "Close Windows")
        self.assertNotIn("cool", plan.details_message.lower())
        self.assertIn("Dehumidification is needed", plan.details_message)
        self.assertFalse(plan.comfort_recovery_triggered)

    def test_low_humidity_warm_house_does_not_recommend_heat(self):
        """When house is warm (72°F) but dry (20% RH), do NOT recommend heating mode."""
        plan = evaluate_zone_plan(
            zone_name="Upstairs",
            inside_temp=72.0,
            inside_humidity=20.0,
            forecast=self.cool_damp_forecast,
            current_outside_temp=50.0,
            models=self.models,
            hvac_mode="off",
            seasonal_mode="fall_transition",
            comfort_profile=self.comfort_profile,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            lat=39.0,
            lon=-77.0,
            active_heat_sp=68.0,
            active_cool_sp=75.0,
            now=self.now,
        )

        self.assertEqual(plan.recommended_state, "Close Windows")
        self.assertNotIn("heat mode", plan.details_message.lower())
        self.assertIn("Humidification is needed", plan.details_message)
        self.assertFalse(plan.comfort_recovery_triggered)

    def test_low_humidity_cold_house_recommends_heat_for_temperature(self):
        """When house is cold (61°F) and dry (20% RH), recommend heating for temperature."""
        plan = evaluate_zone_plan(
            zone_name="Upstairs",
            inside_temp=61.0,
            inside_humidity=20.0,
            forecast=self.cool_damp_forecast,
            current_outside_temp=40.0,
            models=self.models,
            hvac_mode="off",
            seasonal_mode="winter",
            comfort_profile=self.comfort_profile,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            lat=39.0,
            lon=-77.0,
            active_heat_sp=68.0,
            active_cool_sp=75.0,
            now=self.now,
        )

        self.assertEqual(plan.recommended_state, "Close Windows")
        self.assertIn("A change to 'heat' mode is needed for temperature", plan.details_message)
        self.assertIn("Humidification is also needed", plan.details_message)
        self.assertTrue(plan.comfort_recovery_triggered)

    def test_dew_point_ceiling_enforced_when_hvac_off(self):
        """Outdoor air exceeding dew point ceiling must be rejected even when hvac_mode is off."""
        from custom_components.passable_climate.engines.advisor import is_outside_air_favorable

        # Indoor: 84°F, 45% RH (h ≈ 33.8 BTU/lb). Outdoor: 71°F, 90% RH (h ≈ 32.8 BTU/lb, DP ≈ 68°F)
        favorable, reason = is_outside_air_favorable(
            outside_temp=71.0,
            outside_rh=90.0,
            inside_temp=84.0,
            inside_rh=45.0,
            hvac_mode="off",
            seasonal_mode="normal",
            inside_lower_bound=25.0,
            inside_upper_bound=60.0,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            max_dew_point=58.0,
        )
        self.assertFalse(favorable)
        self.assertIn("dew point", reason.lower())

    def test_macro_override_does_not_open_into_hotter_outdoor_air(self):
        """macro_override must not allow opening windows in cooling mode when outdoor temp > indoor temp."""
        from custom_components.passable_climate.engines.advisor import is_outside_air_favorable

        favorable, _ = is_outside_air_favorable(
            outside_temp=76.5,
            outside_rh=40.0,
            inside_temp=75.0,
            inside_rh=50.0,
            hvac_mode="cool",
            seasonal_mode="heatwave_prep",
            inside_lower_bound=25.0,
            inside_upper_bound=60.0,
            safe_min_temp=62.0,
            safe_max_temp=80.0,
            macro_override=True,
        )
        self.assertFalse(favorable)


if __name__ == "__main__":
    unittest.main()
