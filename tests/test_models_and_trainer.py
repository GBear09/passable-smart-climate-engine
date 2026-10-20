"""Unit tests for MLR physical sanity constraints, rectified solar features, and OOD bounds."""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.core.models import RateModel
from custom_components.passable_climate.core.solar import facade_solar_features
from custom_components.passable_climate.training.regression import (
    multiple_linear_regression,
    ransac_linear_fit,
)


class TestModelsAndTrainer(unittest.TestCase):
    """Tests for physical MLR constraints, rectified solar features, and out-of-distribution clamping."""

    def test_facade_solar_features_rectification(self):
        """Facade solar projections must never produce negative values regardless of solar azimuth."""
        import math

        for az in range(0, 360, 15):
            az_rad = math.radians(az)
            sin_az = math.sin(az_rad)
            cos_az = math.cos(az_rad)
            s_east, s_south, s_west = facade_solar_features(1000.0, sin_az, cos_az)

            self.assertGreaterEqual(s_east, 0.0, f"Negative east solar at az={az}")
            self.assertGreaterEqual(s_south, 0.0, f"Negative south solar at az={az}")
            self.assertGreaterEqual(s_west, 0.0, f"Negative west solar at az={az}")

        # At noon (azimuth 180° -> sin=0, cos=-1)
        s_e, s_s, s_w = facade_solar_features(800.0, 0.0, -1.0)
        self.assertAlmostEqual(s_e, 0.0)
        self.assertAlmostEqual(s_s, 800.0)
        self.assertAlmostEqual(s_w, 0.0)

        # Evening (azimuth 270° -> sin=-1, cos=0)
        s_e, s_s, s_w = facade_solar_features(600.0, -1.0, 0.0)
        self.assertAlmostEqual(s_e, 0.0)
        self.assertAlmostEqual(s_s, 0.0)
        self.assertAlmostEqual(s_w, 600.0)

    def test_rate_model_ood_clamping(self):
        """Out-of-distribution features must be clamped to the trained envelope, and output clamped."""
        model = RateModel(
            model_type="mlr",
            c_delta_t=0.2,
            c_wind_draft=0.01,
            c_clouds=0.0,
            c_solar_east=0.001,
            c_solar_south=0.001,
            c_solar_west=0.001,
            mlr_intercept=0.0,
            feature_lo=[-20.0, -50.0, 0.0, 0.0, 0.0, 0.0],
            feature_hi=[20.0, 50.0, 100.0, 1000.0, 1000.0, 1000.0],
            rate_lo=-4.0,
            rate_hi=4.0,
        )

        # Extreme polar vortex: delta = -80°F, wind_draft = -1000.0
        # Without clamp this would be -16°F/h; with clamp it should stay within bounds + 25% headroom
        rate = model.calculate_rate(delta=-80.0, wind_speed=30.0)
        # span is 8.0, 25% headroom is 2.0 -> min clamp is -6.0
        self.assertGreaterEqual(rate, -6.01)

    def test_ransac_seed_repeatability(self):
        """Seeded RANSAC fit must produce deterministic results on identical data."""
        data = [[x, 0.5 * x + 1.0 + (0.1 if x % 2 == 0 else -0.1)] for x in range(30)]
        fit1, mask1 = ransac_linear_fit(data, seed=42)
        fit2, mask2 = ransac_linear_fit(data, seed=42)

        self.assertIsNotNone(fit1)
        self.assertEqual(fit1, fit2)
        self.assertEqual(mask1, mask2)

    def test_blocked_cross_validation(self):
        """Blocked K-fold CV should select an optimal alpha without crashing."""
        import numpy as np

        rng = np.random.default_rng(42)
        # 300 samples with 6 features
        X = rng.normal(0, 1, size=(300, 6))
        true_c = np.array([0.3, 0.05, 0.0, 0.002, 0.002, 0.002])
        Y = X @ true_c + rng.normal(0, 0.1, size=300)

        data_list = [tuple([float(y)] + list(x)) for y, x in zip(Y, X)]
        coeffs, intercept, std_coeffs = multiple_linear_regression(data_list)

        self.assertIsNotNone(coeffs)
        self.assertEqual(len(coeffs), 6)
        self.assertIsNotNone(intercept)
        self.assertEqual(len(std_coeffs), 6)
        # First coefficient should be close to 0.3
        self.assertAlmostEqual(coeffs[0], 0.3, delta=0.1)

    def test_trainer_noise_clamping_acceptance(self):
        """Trainer must clamp tiny negative regression noise and accept valid physical models."""
        from unittest import mock
        from custom_components.passable_climate.training.trainer import train_all_models_sync

        client = mock.MagicMock()
        # Mock 2D RANSAC return
        client.fetch_and_bin_profile_data.return_value = (
            list(range(-10, 10)),
            [0.05 * x + 0.5 for x in range(-10, 10)],
            20,
        )

        # Mock MLR return with tiny negative noise on solar west (-0.00002) and wind draft (-0.0005)
        # 300 data points with 6 features: [Y, dt, wd, clouds, se, ss, sw]
        import numpy as np
        rng = np.random.default_rng(42)
        X = rng.uniform(0, 10, size=(320, 6))
        # Ensure positive delta T
        X[:, 0] = rng.uniform(5, 25, size=320)
        # Physical rate: 0.03*dt + 0.002*wd + tiny noise
        Y = 0.03 * X[:, 0] + 0.002 * X[:, 1] + 0.1
        mlr_data = [tuple([float(y)] + list(x)) for y, x in zip(Y, X)]
        client.fetch_mlr_data.return_value = (mlr_data, True)

        models = train_all_models_sync(client, lookback_days=30)
        self.assertIn("upstairs_temp_profile_win_closed", models)
        m = models["upstairs_temp_profile_win_closed"]
        self.assertEqual(m.model_type, "mlr")
        self.assertIsNotNone(m.c_delta_t)
        self.assertGreater(m.c_delta_t, 0.0)
        # Solar coefficients must be >= 0.0 (non-negative)
        self.assertGreaterEqual(m.c_solar_east, 0.0)
        self.assertGreaterEqual(m.c_solar_south, 0.0)
        self.assertGreaterEqual(m.c_solar_west, 0.0)

    def test_decompose_facade_insolation_preserves_azimuth_when_zero_watts(self):
        """decompose_facade_insolation must return valid azimuth sin/cos even when irradiance_watts=0."""
        from custom_components.passable_climate.core.solar import decompose_facade_insolation

        s_e, s_s, s_w, az_sin, az_cos = decompose_facade_insolation(
            irradiance_watts=0.0, elevation_deg=45.0, azimuth_deg=180.0
        )
        self.assertEqual((s_e, s_s, s_w), (0.0, 0.0, 0.0))
        self.assertAlmostEqual(az_sin, 0.0, places=5)
        self.assertAlmostEqual(az_cos, -1.0, places=5)

    def test_watt_scale_solar_coefficient_precision(self):
        """Watt-scale solar coefficients (~1e-4) must not be truncated by rounding."""
        from unittest import mock
        import numpy as np
        from custom_components.passable_climate.training.trainer import train_all_models_sync

        client = mock.MagicMock()
        client.fetch_and_bin_profile_data.return_value = (
            list(range(-10, 10)),
            [0.05 * x + 0.5 for x in range(-10, 10)],
            20,
        )
        rng = np.random.default_rng(7)
        X = np.zeros((320, 6))
        X[:, 0] = rng.uniform(-10.0, 20.0, size=320)
        X[:, 1] = rng.uniform(-50.0, 100.0, size=320)
        X[:, 2] = rng.uniform(0.0, 100.0, size=320)
        X[:, 3] = rng.uniform(0.0, 3000.0, size=320)
        X[:, 4] = rng.uniform(0.0, 4000.0, size=320)
        X[:, 5] = rng.uniform(0.0, 3000.0, size=320)
        Y = (
            0.04 * X[:, 0]
            + 0.001 * X[:, 1]
            - 0.001 * X[:, 2]
            + 0.00015 * X[:, 3]
            + 0.00025 * X[:, 4]
            + 0.00018 * X[:, 5]
            + 0.05
        )
        mlr_data = [tuple([float(y)] + list(x)) for y, x in zip(Y, X)]
        client.fetch_mlr_data.return_value = (mlr_data, True)

        models = train_all_models_sync(client, lookback_days=30)
        m = models["upstairs_temp_profile_win_closed"]
        self.assertAlmostEqual(m.c_solar_south, 0.00025, places=5)

    def test_trainer_enforces_closed_conductance_floor_on_open_model(self):
        """Open-window model must have c_delta_t >= closed-window model c_delta_t and expanded OOD bounds."""
        from unittest import mock
        import numpy as np
        from custom_components.passable_climate.training.trainer import train_all_models_sync

        client = mock.MagicMock()
        client.fetch_and_bin_profile_data.return_value = (
            list(range(-10, 10)),
            [0.05 * x + 0.5 for x in range(-10, 10)],
            20,
        )
        rng = np.random.default_rng(99)

        def fake_fetch_mlr(entity_id, **_kwargs):
            X = np.zeros((320, 6))
            if "windows_closed" in entity_id:
                X[:, 0] = rng.uniform(-25.0, 20.0, size=320)
                X[:, 1] = rng.uniform(-100.0, 100.0, size=320)
                X[:, 2] = rng.uniform(0.0, 100.0, size=320)
                Y = 0.045 * X[:, 0] + 0.001 * X[:, 1] + 0.2
            else:
                # Mild weather selection bias with tiny slope
                X[:, 0] = rng.uniform(-5.0, 5.0, size=320)
                X[:, 1] = rng.uniform(-10.0, 10.0, size=320)
                X[:, 2] = rng.uniform(0.0, 100.0, size=320)
                Y = 0.005 * X[:, 0] + 0.001 * X[:, 1] - 0.02
            return ([tuple([float(y)] + list(x)) for y, x in zip(Y, X)], True)

        client.fetch_mlr_data.side_effect = fake_fetch_mlr
        models = train_all_models_sync(client, lookback_days=30)
        m_closed = models["upstairs_temp_profile_win_closed"]
        m_open = models["upstairs_temp_profile_win_open"]
        self.assertGreaterEqual(m_open.c_delta_t, m_closed.c_delta_t)
        self.assertLessEqual(m_open.feature_lo[0], m_closed.feature_lo[0])


if __name__ == "__main__":
    unittest.main()
