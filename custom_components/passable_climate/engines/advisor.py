"""Thermodynamic and psychrometric window advisory engine.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
from typing import Any, Literal

from ..core.models import RateModel, SimulationAction, ZonePlanResult
from ..core.psychrometrics import PsychrometricEngine
from ..core.simulator import run_hybrid_simulation, run_static_simulation

BAD_WEATHER_CONDITIONS = {
    "rainy",
    "pouring",
    "lightning",
    "lightning-rainy",
    "hail",
    "snowy",
    "snowy-rainy",
    "exceptional",
}

def evaluate_environmental_vetoes(
    weather_condition: str | None,
    precipitation_probability: float | None,
    wind_speed: float | None,
    wind_gust: float | None,
    aqi_value: float | None,
    home_mode: str | None,
    someone_is_home: bool | None,
    high_wind_speed: float = 18.0,
    high_wind_gust: float = 25.0,
    max_precip_prob: float = 25.0,
    aqi_threshold: float = 50.0,
) -> tuple[bool, str]:
    """Evaluates environmental hazards and occupancy status.
    Returns: (is_vetoed: bool, reason: str)
    """
    # 1. Occupancy Veto
    if home_mode in ["Away", "Vacation", "Armed Away"] or someone_is_home is False:
        return True, "Windows must remain closed while the home is unoccupied."

    # 2. Precipitation Veto
    if weather_condition and weather_condition.lower() in BAD_WEATHER_CONDITIONS:
        return True, f"Hazardous weather condition ({weather_condition}). Keep windows closed."

    if precipitation_probability is not None and precipitation_probability >= max_precip_prob:
        return True, f"High precipitation probability ({round(precipitation_probability)}%). Keep windows closed."

    # 3. Wind Hazard Veto
    if wind_speed is not None and wind_speed >= high_wind_speed:
        return True, f"High sustained wind speed ({round(wind_speed, 1)} mph) causes severe interior drafts."

    if wind_gust is not None and wind_gust >= high_wind_gust:
        return True, f"High wind gusts ({round(wind_gust, 1)} mph) risk window or property damage."

    # 4. Air Quality Veto
    if aqi_value is not None and aqi_value >= aqi_threshold:
        return True, f"Unfavorable Air Quality Index (AQI {round(aqi_value)}). Keep windows closed."

    return False, ""

def is_outside_air_favorable(
    outside_temp: float,
    outside_rh: float,
    inside_temp: float,
    inside_rh: float,
    hvac_mode: str,
    seasonal_mode: str,
    inside_lower_bound: float | None,
    inside_upper_bound: float | None,
    safe_min_temp: float,
    safe_max_temp: float,
    min_enthalpy_delta: float = 1.2,
    max_dew_point: float = 58.0,
    open_temp_margin: float = 1.0,
    close_temp_margin: float = 0.2,
    is_currently_open: bool = False,
    macro_override: bool = False,
) -> tuple[bool, str]:
    """Evaluates outdoor atmospheric suitability using specific enthalpy, dew point,
    dry-bulb temperature, and comfort bounds with full hysteresis deadbands.
    """
    psy = PsychrometricEngine

    h_out = psy.specific_enthalpy(outside_temp, outside_rh)
    h_in = psy.specific_enthalpy(inside_temp, inside_rh)
    dp_out = psy.dew_point(outside_temp, outside_rh)
    proj_rh = psy.projected_rh(outside_temp, outside_rh, inside_temp)

    # Dynamic Hysteresis Margins
    temp_margin = close_temp_margin if is_currently_open else open_temp_margin
    enthalpy_margin = 0.2 if is_currently_open else min_enthalpy_delta
    dew_point_ceiling = (max_dew_point + 3.0) if is_currently_open else max_dew_point
    humidity_buffer = -1.0 if is_currently_open else 3.0

    # 1. Dew Point Humidity Envelope Clamp (prevents mugginess)
    if dp_out > dew_point_ceiling and hvac_mode == "cool":
        return False, f"Outdoor dew point ({dp_out:.1f}°F) introduces unacceptable latent load."

    # 2. Projected RH Comfort Bounds Check
    if inside_upper_bound is not None:
        if proj_rh > (inside_upper_bound - humidity_buffer):
            return False, f"Projected indoor RH ({proj_rh:.1f}%) exceeds comfort limit ({inside_upper_bound:.1f}%)."
    if inside_lower_bound is not None:
        if proj_rh < (inside_lower_bound + humidity_buffer):
            return False, f"Projected indoor RH ({proj_rh:.1f}%) is too dry (limit: {inside_lower_bound:.1f}%)."

    # 3. Cooling Regimes (Free Cooling, Spring Transition, Heatwave Prep)
    if hvac_mode == "cool" or seasonal_mode in ["spring_transition", "heatwave_prep"]:
        if outside_temp >= (inside_temp - temp_margin) and not macro_override:
            return False, f"Outdoor temperature ({outside_temp:.1f}°F) is not cool enough."

        if (h_in - h_out) < enthalpy_margin and not macro_override:
            return False, f"Outdoor enthalpy ({h_out:.1f} BTU/lb) carries too much latent heat vs indoor ({h_in:.1f} BTU/lb)."

        if inside_temp <= safe_min_temp:
            return False, f"Indoor temperature ({inside_temp:.1f}°F) reached safe minimum floor."

        return True, "Favorable free cooling enthalpy and temperature."

    # 4. Heating Regimes (Free Heating, Fall Transition, Coldsnap Prep)
    elif hvac_mode == "heat" or seasonal_mode in ["fall_transition", "coldsnap_prep"]:
        if outside_temp <= (inside_temp + temp_margin) and not macro_override:
            return False, f"Outdoor temperature ({outside_temp:.1f}°F) is not warm enough."

        if (h_out - h_in) < 0.5 and not macro_override:
            return False, "Insufficient thermal energy in outdoor air."

        if inside_temp >= safe_max_temp:
            return False, f"Indoor temperature ({inside_temp:.1f}°F) reached safe maximum ceiling."

        if dp_out < 32.0:
            return False, f"Outdoor air is desiccatingly dry (dew point {dp_out:.1f}°F)."

        return True, "Favorable solar/sensible free heating."

    # 5. Off / Neutral Mode
    elif hvac_mode == "off":
        if outside_temp < safe_min_temp or outside_temp > safe_max_temp:
            return False, f"Outdoor temperature ({outside_temp:.1f}°F) is outside comfortable band."
        return True, "Comfortable ambient conditions."

    return False, "Unfavorable conditions."

def get_comfort_bounds(
    temp_f: float,
    comfort_profile: dict[str, Any] | None,
) -> tuple[float | None, float | None]:
    """Calculates polynomial comfort humidity lower and upper bounds for a given temperature."""
    if not comfort_profile:
        return None, None

    upper = comfort_profile.get("upper_profile")
    lower = comfort_profile.get("lower_profile")
    if not upper or not lower:
        return None, None

    lower_bound, upper_bound = None, None

    # Process lower bound
    lower_temps = lower.get("temperature_data_points", [])
    lower_humids = lower.get("humidity_data_points", [])
    if lower_temps and lower_humids:
        la, lb, lc = lower.get("a"), lower.get("b"), lower.get("c")
        min_l, max_l = min(lower_temps), max(lower_temps)
        if min_l <= temp_f <= max_l and la is not None:
            lower_bound = (la * temp_f**2) + (lb * temp_f) + lc
        elif temp_f < min_l and la is not None:
            slope = (2 * la * min_l) + lb
            lower_bound = lower_humids[lower_temps.index(min_l)] + (slope * (temp_f - min_l))
        elif temp_f > max_l and la is not None:
            slope = (2 * la * max_l) + lb
            lower_bound = lower_humids[lower_temps.index(max_l)] + (slope * (temp_f - max_l))

    # Process upper bound
    upper_temps = upper.get("temperature_data_points", [])
    upper_humids = upper.get("humidity_data_points", [])
    if upper_temps and upper_humids:
        ua, ub, uc = upper.get("a"), upper.get("b"), upper.get("c")
        min_u, max_u = min(upper_temps), max(upper_temps)
        if min_u <= temp_f <= max_u and ua is not None:
            upper_bound = (ua * temp_f**2) + (ub * temp_f) + uc
        elif temp_f < min_u and ua is not None:
            slope = (2 * ua * min_u) + ub
            upper_bound = upper_humids[upper_temps.index(min_u)] + (slope * (temp_f - min_u))
        elif temp_f > max_u and ua is not None:
            slope = (2 * ua * max_u) + ub
            upper_bound = upper_humids[upper_temps.index(max_u)] + (slope * (temp_f - max_u))

    if lower_bound is not None:
        lower_bound = max(15.0, min(80.0, lower_bound))
    if upper_bound is not None:
        upper_bound = max(20.0, min(85.0, upper_bound))

    return lower_bound, upper_bound

def determine_seasonal_mode(
    hvac_mode: str,
    forecast: list[dict[str, Any]],
    comfort_profile: dict[str, Any] | None,
    active_heat_sp: float,
    active_cool_sp: float,
    lookahead_days: int = 7,
) -> tuple[str, str | None]:
    """Determines macro seasonal mode (Heatwave Prep, Spring/Fall Transition, or Normal)."""
    if not forecast:
        return "normal", None

    lookahead_forecast = forecast[: (lookahead_days * 24)]
    if not lookahead_forecast:
        return "normal", None

    daily_highs: dict[str, float] = {}
    for f in lookahead_forecast:
        dt_raw = f.get("datetime")
        if isinstance(dt_raw, str):
            dt = datetime.datetime.fromisoformat(dt_raw)
        elif isinstance(dt_raw, datetime.datetime):
            dt = dt_raw
        else:
            continue
        day_str = dt.strftime("%Y-%m-%d")
        t = float(f.get("temperature", 70.0))
        if day_str not in daily_highs or t > daily_highs[day_str]:
            daily_highs[day_str] = t

    high_temps = list(daily_highs.values())
    max_lookahead_temp = max(high_temps) if high_temps else max(float(f["temperature"]) for f in lookahead_forecast)
    avg_daily_high = sum(high_temps) / len(high_temps) if high_temps else max_lookahead_temp

    # Severe Heatwave Check: >= 7°F spike above average daily high and >= 88°F
    heat_spike = max_lookahead_temp - avg_daily_high
    if heat_spike >= 7.0 and max_lookahead_temp >= max(88.0, active_cool_sp + 12.0):
        return (
            "heatwave_prep",
            f"Seasonal Mode: Severe heatwave approaching ({round(max_lookahead_temp, 1)}°F peak, +{round(heat_spike, 1)}°F above 7-day average high). Engaging aggressive thermal pre-cooling.",
        )

    avg_temp = sum(float(f["temperature"]) for f in lookahead_forecast) / len(lookahead_forecast)
    min_comfort = 68.0
    if comfort_profile and comfort_profile.get("lower_profile", {}).get("temperature_data_points"):
        min_comfort = min(comfort_profile["lower_profile"]["temperature_data_points"])

    if hvac_mode == "cool" and avg_temp < min_comfort:
        return (
            "fall_transition",
            f"Seasonal Mode: Avg forecast over next {lookahead_days} days is {round(avg_temp, 1)}°F (below comfort minimum). Prioritizing heat retention.",
        )

    if hvac_mode == "heat" and avg_temp > (min_comfort - 5.0):
        return (
            "spring_transition",
            f"Seasonal Mode: Avg forecast over next {lookahead_days} days is {round(avg_temp, 1)}°F. Prioritizing cool air retention.",
        )

    return "normal", None

def evaluate_zone_plan(
    zone_name: str,
    inside_temp: float,
    inside_humidity: float,
    forecast: list[dict[str, Any]],
    current_outside_temp: float,
    models: dict[str, RateModel],
    hvac_mode: str,
    seasonal_mode: str,
    comfort_profile: dict[str, Any] | None,
    safe_min_temp: float,
    safe_max_temp: float,
    lat: float,
    lon: float,
    is_bedtime: bool = False,
    is_evening: bool = False,
    morning_start_hour: int = 7,
    solcast_forecast: list[dict[str, Any]] | None = None,
    is_currently_open: bool = False,
    min_enthalpy_delta: float = 1.2,
    max_dew_point: float = 58.0,
    open_temp_margin: float = 1.0,
    close_temp_margin: float = 0.2,
) -> ZonePlanResult:
    """Runs simulation and evaluates the optimal action plan for a zone."""
    comfort_bounds_fn = lambda t: get_comfort_bounds(t, comfort_profile)

    evaluator_fn = lambda **kwargs: is_outside_air_favorable(
        min_enthalpy_delta=min_enthalpy_delta,
        max_dew_point=max_dew_point,
        open_temp_margin=open_temp_margin,
        close_temp_margin=close_temp_margin,
        **kwargs,
    )

    sim_limit = 12
    if is_bedtime or is_evening:
        sim_limit = 14

    sim_forecast = forecast[:sim_limit] if forecast else []
    if not sim_forecast:
        return ZonePlanResult(
            recommended_state="Close Windows",
            details_message=f"Keep {zone_name} windows closed (no forecast data available).",
            history=[],
            actions=[SimulationAction("closed", 1)],
            eco_mode_requested=False,
        )

    history, actions = run_hybrid_simulation(
        initial_temp=inside_temp,
        initial_humidity=inside_humidity,
        forecast=sim_forecast,
        models=models,
        hvac_mode=hvac_mode,
        seasonal_mode=seasonal_mode,
        safe_min_temp=safe_min_temp,
        safe_max_temp=safe_max_temp,
        lat=lat,
        lon=lon,
        favorability_evaluator=evaluator_fn,
        comfort_bounds_getter=comfort_bounds_fn,
        solcast_forecast=solcast_forecast,
        is_currently_open=is_currently_open,
    )

    closed_history = run_static_simulation(
        initial_temp=inside_temp,
        initial_humidity=inside_humidity,
        forecast=sim_forecast,
        model_temp=models.get("temp_closed"),
        model_hum=models.get("humidity_closed"),
        strategy="closed",
        lat=lat,
        lon=lon,
        solcast_forecast=solcast_forecast,
    )

    should_open_first = actions and actions[0].action == "open"
    eco_requested = (seasonal_mode != "normal" and seasonal_mode != "heatwave_prep")

    if should_open_first:
        open_hours = actions[0].hours
        final_temp = history[min(open_hours - 1, len(history) - 1)]["temp"]
        temp_diff = final_temp - inside_temp
        dir_word = "drop" if temp_diff < 0 else "rise"

        msg = f"Open {zone_name} windows. Expect indoor temp to {dir_word} by {abs(temp_diff):.1f}°F (to {final_temp:.1f}°F)."
        return ZonePlanResult(
            recommended_state="Open Windows",
            details_message=msg,
            history=history,
            actions=actions,
            eco_mode_requested=eco_requested,
        )
    else:
        close_reason = "to maintain current temperature"
        if current_outside_temp > inside_temp:
            close_reason = "to keep the cool air inside" if hvac_mode == "cool" else "due to outside temperature"
        elif current_outside_temp < inside_temp:
            close_reason = "to keep the heat inside" if hvac_mode == "heat" else "due to outside temperature"

        msg = f"Keep {zone_name} windows closed {close_reason}."
        return ZonePlanResult(
            recommended_state="Close Windows",
            details_message=msg,
            history=closed_history,
            actions=actions,
            eco_mode_requested=eco_requested,
        )
