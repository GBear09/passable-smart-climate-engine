"""Unit tests for Bolton-Magnus psychrometrics and ISA barometric altitude adjustment."""

import math
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.core.psychrometrics import (
    ATM_PRESSURE_HPA,
    PsychrometricEngine,
    pressure_from_elevation,
)


class TestPsychrometrics(unittest.TestCase):
    """Test mathematical accuracy of psychrometric formulas."""

    def test_pressure_from_elevation(self):
        """Standard sea level should be 1013.25 hPa; higher elevations should have lower pressure."""
        p_0 = pressure_from_elevation(0.0)
        self.assertAlmostEqual(p_0, ATM_PRESSURE_HPA, places=2)

        # Denver, CO (~1600m) should be ~835-840 hPa
        p_denver = pressure_from_elevation(1600.0)
        self.assertTrue(830.0 < p_denver < 845.0)

    def test_saturation_vapor_pressure_magnus(self):
        """Check Bolton (1980) saturation vapor pressure against known reference values."""
        # At 32°F (0°C), e_s should be 6.112 hPa
        es_32 = PsychrometricEngine.saturation_vapor_pressure(32.0)
        self.assertAlmostEqual(es_32, 6.112, places=3)

        # At 68°F (20°C), e_s is approx 23.37 hPa
        es_68 = PsychrometricEngine.saturation_vapor_pressure(68.0)
        self.assertTrue(23.0 < es_68 < 23.8)

    def test_dew_point_magnus_exact_inversion(self):
        """Dew point of saturated air (100% RH) must equal dry-bulb temperature."""
        for temp_f in [40.0, 55.0, 70.0, 85.0, 100.0]:
            dp = PsychrometricEngine.dew_point(temp_f, 100.0)
            self.assertAlmostEqual(dp, temp_f, delta=0.01)

        # Standard test: 70°F at 50% RH -> Dew point ~50.5°F
        dp_70_50 = PsychrometricEngine.dew_point(70.0, 50.0)
        self.assertTrue(50.0 < dp_70_50 < 51.0)

    def test_projected_rh(self):
        """Warming air without adding moisture must decrease relative humidity."""
        rh_initial = 60.0
        rh_warmed = PsychrometricEngine.projected_rh(source_temp_f=60.0, source_rh=rh_initial, target_temp_f=75.0)
        self.assertLess(rh_warmed, rh_initial)

        # Cooling air must increase relative humidity
        rh_cooled = PsychrometricEngine.projected_rh(source_temp_f=75.0, source_rh=40.0, target_temp_f=60.0)
        self.assertGreater(rh_cooled, 40.0)

    def test_specific_enthalpy(self):
        """Specific enthalpy increases with both temperature and humidity."""
        h_dry = PsychrometricEngine.specific_enthalpy(70.0, 20.0)
        h_humid = PsychrometricEngine.specific_enthalpy(70.0, 80.0)
        self.assertGreater(h_humid, h_dry)

        h_warm = PsychrometricEngine.specific_enthalpy(85.0, 50.0)
        h_cool = PsychrometricEngine.specific_enthalpy(65.0, 50.0)
        self.assertGreater(h_warm, h_cool)


if __name__ == "__main__":
    unittest.main()
