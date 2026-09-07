"""Astronomical solar tracking and facade irradiance decomposition.
Pure-Python calculations with zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
import math
from typing import Any

def calculate_solar_position(dt_utc: datetime.datetime, lat: float, lon: float) -> tuple[float, float]:
    """Calculates approximate sun elevation and azimuth (degrees) for a UTC datetime.
    Elevation: angle above horizon (-90 to 90).
    Azimuth: compass bearing in degrees (0 = North, 90 = East, 180 = South, 270 = West).
    """
    days_since_2000 = (
        (dt_utc.date() - datetime.date(2000, 1, 1)).days
        + (dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0) / 24.0
        - 0.5
    )

    L = (280.460 + 0.9856474 * days_since_2000) % 360.0
    g_rad = math.radians((357.528 + 0.9856003 * days_since_2000) % 360.0)

    lambda_rad = math.radians(L + 1.915 * math.sin(g_rad) + 0.020 * math.sin(2 * g_rad))
    ep_rad = math.radians(23.439 - 0.0000004 * days_since_2000)

    decl_rad = math.asin(math.sin(ep_rad) * math.sin(lambda_rad))

    t_utc = dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0
    lstm = t_utc + (lon / 15.0)
    ha_rad = math.radians((lstm - 12.0) * 15.0)
    lat_rad = math.radians(lat)

    sin_el = math.sin(lat_rad) * math.sin(decl_rad) + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(ha_rad)
    sin_el = max(-1.0, min(1.0, sin_el))
    elevation = math.degrees(math.asin(sin_el))

    cos_az_num = math.sin(decl_rad) - math.sin(math.radians(elevation)) * math.sin(lat_rad)
    cos_az_den = math.cos(math.radians(elevation)) * math.cos(lat_rad)
    if abs(cos_az_den) > 1e-6:
        cos_az = max(-1.0, min(1.0, cos_az_num / cos_az_den))
        azimuth = math.degrees(math.acos(cos_az))
        if math.sin(ha_rad) > 0:
            azimuth = 360.0 - azimuth
    else:
        azimuth = 180.0

    return elevation, azimuth

def decompose_facade_insolation(
    elevation_deg: float, azimuth_deg: float, irradiance_watts: float
) -> tuple[float, float, float, float, float]:
    """Decomposes total irradiance into cardinal facade incident components.
    Returns: (solar_south, solar_east, solar_west, az_sin, az_cos)
    """
    if elevation_deg <= 0 or irradiance_watts <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    az_rad = math.radians(azimuth_deg)
    el_rad = math.radians(elevation_deg)
    cos_el = math.cos(el_rad)

    az_sin = math.sin(az_rad)
    az_cos = math.cos(az_rad)

    # South facade: maximum exposure when sun is directly South (azimuth 180°, cos(180°)=-1)
    solar_south = irradiance_watts * max(0.0, -az_cos) * cos_el

    # East facade: morning sun (0° <= azimuth < 180°, sin > 0)
    solar_east = irradiance_watts * max(0.0, az_sin) * cos_el

    # West facade: afternoon sun (180° <= azimuth < 360°, sin < 0)
    solar_west = irradiance_watts * max(0.0, -az_sin) * cos_el

    return solar_south, solar_east, solar_west, az_sin, az_cos

def interpolate_solcast_power(
    step_dt: datetime.datetime, solcast_forecast: list[dict[str, Any]]
) -> float:
    """Interpolates Solcast PV power (Watts) for a given datetime."""
    if not solcast_forecast:
        return 0.0

    if step_dt.tzinfo is None:
        step_dt = step_dt.replace(tzinfo=datetime.timezone.utc)

    for i in range(len(solcast_forecast) - 1):
        p1 = solcast_forecast[i]
        p2 = solcast_forecast[i + 1]
        dt1 = p1["dt"]
        dt2 = p2["dt"]
        if dt1.tzinfo is None:
            dt1 = dt1.replace(tzinfo=datetime.timezone.utc)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=datetime.timezone.utc)

        if dt1 <= step_dt <= dt2:
            total_sec = (dt2 - dt1).total_seconds()
            if total_sec <= 0:
                return float(p1["power"])
            elapsed_sec = (step_dt - dt1).total_seconds()
            progress = elapsed_sec / total_sec
            return float(p1["power"] + (p2["power"] - p1["power"]) * progress)

    first_dt = solcast_forecast[0]["dt"]
    if first_dt.tzinfo is None:
        first_dt = first_dt.replace(tzinfo=datetime.timezone.utc)
    if step_dt < first_dt:
        return float(solcast_forecast[0]["power"])

    last_dt = solcast_forecast[-1]["dt"]
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
    if step_dt > last_dt:
        return float(solcast_forecast[-1]["power"])

    return 0.0
