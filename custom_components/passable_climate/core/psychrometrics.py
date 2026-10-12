"""High-precision, pure-Python psychrometric calculations for moist air.
Formulations derived from ASHRAE Fundamentals and the Arden Buck equations.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import math

ATM_PRESSURE_HPA: float = 1013.25  # Standard sea-level barometric pressure (hPa)

# Bolton (1980) Magnus constants — identical to climate_sensors.yaml so runtime AH/dew point
# match the exact distribution the MLR models were trained on.
_MAG_A: float = 6.112
_MAG_B: float = 17.67
_MAG_C: float = 243.5


def pressure_from_elevation(elev_m: float) -> float:
    """ISA barometric pressure (hPa) for a site elevation in metres."""
    return ATM_PRESSURE_HPA * (1.0 - 2.25577e-5 * float(elev_m)) ** 5.25588


class PsychrometricEngine:
    """High-precision psychrometric calculations for moist air."""

    ATM_PRESSURE_HPA = ATM_PRESSURE_HPA

    @staticmethod
    def f_to_c(temp_f: float) -> float:
        """Convert Fahrenheit to Celsius."""
        return (temp_f - 32.0) * 5.0 / 9.0

    @staticmethod
    def c_to_f(temp_c: float) -> float:
        """Convert Celsius to Fahrenheit."""
        return (temp_c * 9.0 / 5.0) + 32.0

    @classmethod
    def saturation_vapor_pressure(cls, temp_f: float) -> float:
        """Bolton (1980) Magnus equation for saturation vapor pressure over water (hPa).
        Valid with high accuracy (<0.3% error) across 0°C to 40°C.
        """
        t_c = cls.f_to_c(temp_f)
        return _MAG_A * math.exp((_MAG_B * t_c) / (t_c + _MAG_C))

    @classmethod
    def actual_vapor_pressure(cls, temp_f: float, rh: float) -> float:
        """Actual water vapor pressure (hPa)."""
        rh_clamped = max(0.0, min(100.0, float(rh)))
        return (rh_clamped / 100.0) * cls.saturation_vapor_pressure(temp_f)

    @classmethod
    def dew_point(cls, temp_f: float, rh: float) -> float:
        """Calculates dew point temperature in Fahrenheit using exact Magnus inversion."""
        vp = cls.actual_vapor_pressure(temp_f, rh)
        if vp <= 0.001:
            return -40.0
        alpha = math.log(vp / _MAG_A)
        denom = _MAG_B - alpha
        if abs(denom) < 1e-6:
            return temp_f
        dp_c = (_MAG_C * alpha) / denom
        return cls.c_to_f(dp_c)

    @classmethod
    def humidity_ratio(cls, temp_f: float, rh: float, pressure_hpa: float = ATM_PRESSURE_HPA) -> float:
        """Calculates humidity ratio W (lb water / lb dry air).
        ASHRAE: W = 0.62198 * (P_w / (P_atm - P_w))
        """
        vp = cls.actual_vapor_pressure(temp_f, rh)
        clamped_vp = min(vp, pressure_hpa - 0.5)
        return 0.62198 * (clamped_vp / (pressure_hpa - clamped_vp))

    @classmethod
    def specific_enthalpy(cls, temp_f: float, rh: float, pressure_hpa: float = ATM_PRESSURE_HPA) -> float:
        """Calculates specific enthalpy of moist air h in BTU / lb of dry air.
        ASHRAE moist air formulation:
        h = 0.240 * T_db + W * (1061.2 + 0.444 * T_db)
        """
        w = cls.humidity_ratio(temp_f, rh, pressure_hpa)
        return (0.240 * temp_f) + (w * (1061.2 + (0.444 * temp_f)))

    @classmethod
    def absolute_humidity(cls, temp_f: float, rh: float) -> float:
        """Returns Absolute Humidity in g/m³."""
        t_c = cls.f_to_c(temp_f)
        e = cls.actual_vapor_pressure(temp_f, rh)
        return (2.16679 * e * 100.0) / (273.15 + t_c)

    @classmethod
    def rh_from_absolute_humidity(cls, temp_f: float, ah: float) -> float:
        """Converts Absolute Humidity (g/m³) back to Relative Humidity (%)."""
        t_c = cls.f_to_c(temp_f)
        e = (float(ah) * (273.15 + t_c)) / (2.16679 * 100.0)
        es = cls.saturation_vapor_pressure(temp_f)
        if es <= 0:
            return 0.0
        rh = (e / es) * 100.0
        return max(0.0, min(100.0, rh))

    @classmethod
    def projected_rh(cls, source_temp_f: float, source_rh: float, target_temp_f: float) -> float:
        """Calculates what the Relative Humidity of air would be if heated or cooled
        to target_temp_f at constant absolute moisture content / vapor pressure.
        """
        e_source = cls.actual_vapor_pressure(source_temp_f, source_rh)
        es_target = cls.saturation_vapor_pressure(target_temp_f)
        if es_target <= 0:
            return 100.0
        projected = (e_source / es_target) * 100.0
        return max(0.0, min(100.0, projected))
