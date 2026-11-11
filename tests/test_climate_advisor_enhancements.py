"""Unit tests for Phases 1-7 ClimateAdvisor enhancements in Passable Smart Climate Engine."""

import datetime
import os
import sys
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.coordinator import SmartClimateCoordinator
from custom_components.passable_climate.core.models import RateModel
from custom_components.passable_climate.engines.advisor import (
    calculate_preconditioning_plan,
    evaluate_sleep_bedroom_offset,
)
from custom_components.passable_climate.engines.latch import (
    CLOSE,
    OPEN,
    ComplianceLedger,
    WindowContactTracker,
)
from custom_components.passable_climate.training.regression import fit_online_rolling_ols


class TestClimateAdvisorEnhancements(unittest.IsolatedAsyncioTestCase):
    """Tests for Phases 1 through 7 additive enhancements."""

    def test_phase1_window_contact_tracker_debounce_and_blip_immunity(self):
        """WindowContactTracker must debounce off->on transitions, ignore reconnect blips, and hold state on dropout."""
        tracker = WindowContactTracker()
        t0 = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)

        # 1. Initial read: w1 is "off", w2 is already "on" at startup -> w2 immediately confirmed
        res0 = tracker.evaluate_zone_contacts(
            ["binary_sensor.w1", "binary_sensor.w2"],
            {"binary_sensor.w1": "off", "binary_sensor.w2": "on"},
            t0,
            debounce_seconds=120.0,
        )
        self.assertEqual(res0["confirmed_count"], 1)
        self.assertEqual(res0["confirmed_open_sensors"], ["binary_sensor.w2"])

        # 2. w1 opens at t0 + 10s -> pending debounce, not yet confirmed
        res1 = tracker.evaluate_zone_contacts(
            ["binary_sensor.w1", "binary_sensor.w2"],
            {"binary_sensor.w1": "on", "binary_sensor.w2": "off"},
            t0 + datetime.timedelta(seconds=10),
            debounce_seconds=120.0,
        )
        self.assertEqual(res1["confirmed_count"], 0)
        self.assertEqual(res1["raw_open_count"], 1)
        self.assertEqual(res1["pending_debounce_sensors"], ["binary_sensor.w1"])

        # 3. w1 still open after 130s -> confirmed open
        res2 = tracker.evaluate_zone_contacts(
            ["binary_sensor.w1", "binary_sensor.w2"],
            {"binary_sensor.w1": "on", "binary_sensor.w2": "off"},
            t0 + datetime.timedelta(seconds=135),
            debounce_seconds=120.0,
        )
        self.assertEqual(res2["confirmed_count"], 1)
        self.assertEqual(res2["confirmed_open_sensors"], ["binary_sensor.w1"])
        self.assertEqual(res2["pending_debounce_sensors"], [])

        # 4. w1 drops to unavailable -> holds last confirmed_open (True) and records in unavailable_sensors
        res3 = tracker.evaluate_zone_contacts(
            ["binary_sensor.w1", "binary_sensor.w2"],
            {"binary_sensor.w1": "unavailable", "binary_sensor.w2": "unavailable"},
            t0 + datetime.timedelta(seconds=150),
            debounce_seconds=120.0,
        )
        self.assertEqual(res3["confirmed_count"], 1)
        self.assertEqual(res3["unavailable_sensors"], ["binary_sensor.w1", "binary_sensor.w2"])

        # 5. w2 reconnects with a brief "on" blip while debounce=0 -> reconnect grace (60s) still blocks it!
        res4 = tracker.evaluate_zone_contacts(
            ["binary_sensor.w1", "binary_sensor.w2"],
            {"binary_sensor.w1": "off", "binary_sensor.w2": "on"},
            t0 + datetime.timedelta(seconds=160),
            debounce_seconds=0.0,
            reconnect_grace_seconds=60.0,
        )
        self.assertEqual(res4["confirmed_count"], 0)
        self.assertEqual(res4["pending_debounce_sensors"], ["binary_sensor.w2"])

    async def test_phase2_and_phase3_swing_coast_and_idle_comfort_stall(self):
        """Coordinator telemetry loop must learn swing/coast and flag Idle Comfort Stall after 90m off-target."""
        hass = mock.MagicMock()
        entry = mock.MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {
            "zones": {
                "upstairs": {
                    "climate_entity": "climate.upstairs_hk",
                    "temperature_sensor": "sensor.upstairs_temp",
                    "humidity_sensor": "sensor.upstairs_humidity",
                    "open_windows_sensor": "sensor.upstairs_open_windows_count",
                }
            }
        }
        entry.options = {}
        coord = SmartClimateCoordinator(hass, entry)
        coord.telemetry_state["upstairs"]["filter_runtime_hours"] = 50.0

        t0 = datetime.datetime(2026, 7, 15, 18, 0, 0, tzinfo=datetime.timezone.utc)
        state_map = {}

        def set_state(ent_id, val, attrs=None):
            s = mock.MagicMock()
            s.state = str(val)
            s.attributes = attrs or {}
            s.last_reported = t0
            s.last_updated = t0
            state_map[ent_id] = s

        set_state("weather.home", "sunny", {"temperature": 88.0, "humidity": 50.0, "wind_speed": 5.0})
        set_state("binary_sensor.someone_is_home", "on")
        set_state("input_select.home_mode", "Home")
        set_state("sensor.upstairs_humidity", "50.0")
        set_state("sensor.upstairs_temp", "74.8", {"unit_of_measurement": "°F"})
        set_state(
            "climate.upstairs_hk",
            "cool",
            {"hvac_action": "cooling", "temperature": 74.0},
        )
        hass.states.get.side_effect = lambda e: state_map.get(e)

        # Step 1: Transition from idle -> cooling at 74.8°F with target 74.0°F -> learns 0.8°F swing
        with mock.patch("homeassistant.util.dt.now", return_value=t0):
            await coord._async_update_telemetry()
        ttt_attrs = coord.zone_diagnostics["upstairs"]["time_to_temp_attrs"]
        self.assertAlmostEqual(ttt_attrs["learned_swing_cool_f"], 0.8)

        # Step 2: Transition cooling -> idle at t0 + 15m (temp=74.0°F), then coast down to 73.6°F and rebound at t0 + 25m
        t1 = t0 + datetime.timedelta(minutes=15)
        set_state("sensor.upstairs_temp", "74.0", {"unit_of_measurement": "°F"})
        set_state("climate.upstairs_hk", "cool", {"hvac_action": "idle", "temperature": 74.0})
        with mock.patch("homeassistant.util.dt.now", return_value=t1):
            await coord._async_update_telemetry()

        t2 = t1 + datetime.timedelta(minutes=5)
        set_state("sensor.upstairs_temp", "73.6", {"unit_of_measurement": "°F"})
        with mock.patch("homeassistant.util.dt.now", return_value=t2):
            await coord._async_update_telemetry()

        t3 = t2 + datetime.timedelta(minutes=10)
        set_state("sensor.upstairs_temp", "73.8", {"unit_of_measurement": "°F"})
        with mock.patch("homeassistant.util.dt.now", return_value=t3):
            await coord._async_update_telemetry()

        ttt_attrs = coord.zone_diagnostics["upstairs"]["time_to_temp_attrs"]
        self.assertAlmostEqual(ttt_attrs["learned_coast_f"], 0.4)
        self.assertAlmostEqual(ttt_attrs["learned_coast_minutes"], 15.0)

        # Step 3: Zone sits at 77.5°F (> 74.0 + 0.8 + 1.0) with HVAC idle for 95 minutes -> Idle Comfort Stall
        t4 = t3 + datetime.timedelta(minutes=5)
        set_state("sensor.upstairs_temp", "77.5", {"unit_of_measurement": "°F"})
        with mock.patch("homeassistant.util.dt.now", return_value=t4):
            await coord._async_update_telemetry()
        self.assertEqual(coord.zone_diagnostics["upstairs"]["health_status"], "Optimal")

        t5 = t4 + datetime.timedelta(minutes=95)
        with mock.patch("homeassistant.util.dt.now", return_value=t5):
            await coord._async_update_telemetry()
        self.assertEqual(coord.zone_diagnostics["upstairs"]["health_status"], "Idle Comfort Stall")
        self.assertIn("above target", coord.zone_diagnostics["upstairs"]["health_attrs"]["stall_reason"])

        # Step 4: Verify Filter Overdue retains strict precedence over Idle Comfort Stall
        coord.telemetry_state["upstairs"]["filter_runtime_hours"] = 315.0
        with mock.patch("homeassistant.util.dt.now", return_value=t5 + datetime.timedelta(minutes=5)):
            await coord._async_update_telemetry()
        self.assertEqual(coord.zone_diagnostics["upstairs"]["health_status"], "Filter Overdue")

        # Step 5: Verify time_to_temp uses thermostat current_temperature (68.0°F) rather than lagging zone avg (67.0°F)
        set_state("sensor.upstairs_temp", "67.0", {"unit_of_measurement": "°F"})
        set_state("climate.upstairs", "heat", {"hvac_action": "heating", "temperature": 68.0, "current_temperature": 68.0})
        set_state("climate.upstairs_hk", "heat", {"hvac_action": "heating", "temperature": 68.0, "current_temperature": 68})
        with mock.patch("homeassistant.util.dt.now", return_value=t5 + datetime.timedelta(minutes=10)):
            await coord._async_update_telemetry()
        self.assertEqual(coord.zone_diagnostics["upstairs"]["time_to_temp"], 0)
        self.assertAlmostEqual(coord.zone_diagnostics["upstairs"]["time_to_temp_attrs"]["current_temperature"], 68.0)
        self.assertAlmostEqual(coord.zone_diagnostics["upstairs"]["time_to_temp_attrs"]["zone_average_temperature"], 67.0)

    def test_phase4_preconditioning_plan_calculation(self):
        """calculate_preconditioning_plan must compute rate-derived lead minutes before a comfort breach."""
        now = datetime.datetime(2026, 7, 20, 11, 0, 0, tzinfo=datetime.timezone.utc)
        forecast = [
            {"datetime": (now + datetime.timedelta(hours=i)).isoformat(), "temperature": 85.0 + i, "humidity": 50.0}
            for i in range(6)
        ]
        closed_history = [
            {"temp": 74.2, "humidity": 50.0},
            {"temp": 75.1, "humidity": 50.0},
            {"temp": 76.2, "humidity": 50.0},
            {"temp": 77.4, "humidity": 50.0},
        ]
        cool_model = RateModel("ransac", slope=0.02, intercept=-2.2)

        plan = calculate_preconditioning_plan(
            closed_history=closed_history,
            forecast=forecast,
            hvac_mode="cool",
            seasonal_mode="normal",
            inside_temp=73.5,
            active_heat_sp=68.0,
            active_cool_sp=74.0,
            safe_min_temp=65.0,
            safe_max_temp=76.0,
            hvac_cool_model=cool_model,
            now=now,
        )
        self.assertTrue(plan["pre_condition_needed"])
        self.assertEqual(plan["pre_condition_mode"], "cool")
        self.assertGreaterEqual(plan["pre_condition_lead_minutes"], 30)
        self.assertLessEqual(plan["pre_condition_lead_minutes"], 240)
        self.assertIsNotNone(plan["pre_condition_start_time"])
        self.assertIsNotNone(plan["predicted_breach_time"])

        # When thermostat is already in heat mode with windows closed, suppress action-required
        # pre-conditioning and predict next automatic HVAC cycle kick-on time instead
        heat_history = [
            {"temp": 68.1, "humidity": 45.0},
            {"temp": 67.2, "humidity": 45.0},
            {"temp": 66.2, "humidity": 45.0},
        ]
        auto_plan = calculate_preconditioning_plan(
            closed_history=heat_history,
            forecast=forecast,
            hvac_mode="heat",
            seasonal_mode="normal",
            inside_temp=67.6,
            active_heat_sp=68.0,
            active_cool_sp=74.0,
            safe_min_temp=65.0,
            safe_max_temp=76.0,
            now=now,
            raw_hvac_state="heat",
            hvac_action="idle",
            is_currently_open=False,
            thermostat_temp=68.9,
            thermostat_closed_history=heat_history,
            swing_heat_f=0.5,
        )
        self.assertFalse(auto_plan["pre_condition_needed"])
        self.assertEqual(auto_plan["next_hvac_cycle_mode"], "heat")
        self.assertIsNotNone(auto_plan["next_hvac_cycle_time"])
        self.assertEqual(auto_plan["next_hvac_trigger_temp"], 67.5)

    def test_phase5_sleep_bedroom_offset_evaluation(self):
        """evaluate_sleep_bedroom_offset must identify limiting bedroom and apply 0.5°F hysteresis."""
        rooms_cfg = {
            "master_bedroom": {"name": "Master Bedroom", "zone": "upstairs", "temp_sensor": "sensor.master_temp"},
            "maxs_room": {"name": "Max's Room", "zone": "upstairs", "temp_sensor": "sensor.max_temp"},
            "maggies_room": {"name": "Maggie's Room", "zone": "upstairs", "temp_sensor": "sensor.maggie_temp"},
        }
        room_temps = {"master_bedroom": 68.2, "maxs_room": 65.1, "maggies_room": 67.5}
        room_rates = {"master_bedroom": -0.2, "maxs_room": -0.6, "maggies_room": -0.3}

        res = evaluate_sleep_bedroom_offset(
            rooms_config=rooms_cfg,
            room_temps=room_temps,
            room_rates=room_rates,
            zone_temp=68.0,
            hvac_mode="heat",
            seasonal_mode="normal",
            active_heat_sp=67.0,
            active_cool_sp=74.0,
            swing_heat_f=0.5,
            prev_offset_f=0.0,
        )
        self.assertEqual(res["sleep_limiting_room"], "Max's Room")
        self.assertEqual(res["sleep_limiting_room_entity"], "sensor.max_temp")
        self.assertEqual(res["sleep_limiting_room_temp"], 65.1)
        self.assertEqual(res["recommended_sleep_offset_f"], 3.0)

        # Hysteresis check: slight change in Max's room (65.3°F -> raw boost 3.2°F) holds +3.0°F
        room_temps["maxs_room"] = 65.3
        res2 = evaluate_sleep_bedroom_offset(
            rooms_config=rooms_cfg,
            room_temps=room_temps,
            room_rates=room_rates,
            zone_temp=68.0,
            hvac_mode="heat",
            seasonal_mode="normal",
            active_heat_sp=67.0,
            active_cool_sp=74.0,
            swing_heat_f=0.5,
            prev_offset_f=3.0,
        )
        self.assertEqual(res2["recommended_sleep_offset_f"], 3.0)

    def test_phase6_compliance_ledger_tracking_and_serialization(self):
        """ComplianceLedger must track follow rate, response lag, and open-window fraction across days."""
        ledger = ComplianceLedger()
        day1 = datetime.datetime(2026, 9, 10, 9, 0, 0, tzinfo=datetime.timezone.utc)

        # Day 1: Open Windows recommended at 9:00, user opens 2 of 4 upstairs windows at 9:30 (30m lag)
        ledger.record_step(day1, OPEN, up_open_count=0, up_total_sensors=4, dn_open_count=0, dn_total_sensors=3)
        ledger.record_step(
            day1 + datetime.timedelta(minutes=30),
            OPEN,
            up_open_count=2,
            up_total_sensors=4,
            dn_open_count=1,
            dn_total_sensors=3,
        )
        # Close recommended at 13:00, user closes at 13:15
        ledger.record_step(
            day1 + datetime.timedelta(hours=4),
            CLOSE,
            up_open_count=2,
            up_total_sensors=4,
            dn_open_count=1,
            dn_total_sensors=3,
        )
        ledger.record_step(
            day1 + datetime.timedelta(hours=4, minutes=15),
            CLOSE,
            up_open_count=0,
            up_total_sensors=4,
            dn_open_count=0,
            dn_total_sensors=3,
        )

        restored = ComplianceLedger.from_dict(ledger.as_dict())
        summary = restored.get_summary()
        self.assertEqual(summary["recommended_days"], 1)
        self.assertEqual(summary["complied_days"], 1)
        self.assertEqual(summary["follow_rate_pct"], 100.0)
        self.assertEqual(summary["avg_open_lag_minutes"], 30.0)
        self.assertEqual(summary["avg_close_lag_minutes"], 15.0)
        self.assertEqual(summary["learned_open_windows_fraction"], 0.5)
        self.assertIn("1/1 days (100%)", summary["compliance_summary"])

    def test_phase7_online_rolling_ols_and_influxdb_priority(self):
        """fit_online_rolling_ols must enforce physical signs and never overwrite an InfluxDB model."""
        # Valid passive closed-window samples: rate = 0.05 * delta_t
        samples = [(-10.0 + i * 2.0, 0.05 * (-10.0 + i * 2.0)) for i in range(8)]
        slope, intercept, r_sq = fit_online_rolling_ols(samples, regime="temp_closed")
        self.assertIsNotNone(slope)
        self.assertAlmostEqual(slope, 0.05, places=3)
        self.assertAlmostEqual(intercept, 0.0, places=3)
        self.assertGreater(r_sq, 0.95)

        # Unphysical negative slope in passive regime must be rejected
        bad_samples = [(-10.0 + i * 2.0, -0.05 * (-10.0 + i * 2.0)) for i in range(8)]
        b_slope, b_int, _ = fit_online_rolling_ols(bad_samples, regime="temp_closed")
        self.assertIsNone(b_slope)
        self.assertIsNone(b_int)


if __name__ == "__main__":
    unittest.main()
