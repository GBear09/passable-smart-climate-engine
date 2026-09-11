"""Thermodynamic and psychrometric window advisory engine.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
from typing import Any, Callable

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


def _parse_dt(raw_dt: Any) -> datetime.datetime | None:
    if isinstance(raw_dt, str):
        try:
            return datetime.datetime.fromisoformat(raw_dt)
        except Exception:
            return None
    elif isinstance(raw_dt, datetime.datetime):
        return raw_dt
    return None


def _parse_local_dt(raw_dt: Any, ref_now: datetime.datetime | None = None) -> datetime.datetime:
    parsed = _parse_dt(raw_dt)
    tz = ref_now.tzinfo if ref_now and ref_now.tzinfo else datetime.timezone.utc
    if parsed is None:
        return ref_now if ref_now is not None else datetime.datetime.now(tz)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(tz)


def _format_time(dt: datetime.datetime | None) -> str:
    if not dt:
        return ""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.strftime('%M %p')}"


def _get_temp_change_msg(
    inside_temp: float,
    history: list[dict[str, float]],
    actions: list[SimulationAction],
    closed_history: list[dict[str, float]] | None = None,
) -> str:
    if not history or not actions:
        return ""
    if actions[0].action == "open":
        idx = min(actions[0].hours - 1, len(history) - 1)
        idx = max(0, idx)
        final_temp = history[idx]["temp"]
        temp_change = final_temp - inside_temp

        comparison_msg = ""
        if closed_history and len(closed_history) > idx:
            closed_final_temp = closed_history[idx]["temp"]
            diff = closed_final_temp - final_temp
            if abs(diff) >= 0.1:
                direction_label = "cooler" if diff > 0 else "warmer"
                comparison_msg = f", which is {abs(diff):.1f}°F {direction_label} than if kept closed"

        if abs(temp_change) >= 0.1:
            dir_word = "drop" if temp_change < 0 else "rise"
            return f" Expect indoor temp to {dir_word} by {abs(temp_change):.1f}°F (to {final_temp:.1f}°F){comparison_msg}."
        else:
            return f" Expect indoor temp to remain around {final_temp:.1f}°F{comparison_msg}."
    return ""


def evaluate_environmental_vetoes(
    weather_condition: str | None,
    precipitation_probability: float | None,
    wind_speed: float | None,
    wind_gust: float | None,
    aqi_value: float | None,
    home_mode: str | None = None,
    someone_is_home: bool | None = None,
    high_wind_speed: float = 18.0,
    high_wind_gust: float = 25.0,
    max_precip_prob: float = 25.0,
    aqi_threshold: float = 50.0,
    **kwargs: Any,
) -> tuple[bool, str]:
    """Evaluates environmental hazards (precipitation, high wind, AQI).
    Returns: (is_vetoed: bool, reason: str)
    """
    # 1. Precipitation Veto
    if weather_condition and weather_condition.lower() in BAD_WEATHER_CONDITIONS:
        return True, f"Hazardous weather condition ({weather_condition}). Keep windows closed."

    if precipitation_probability is not None and precipitation_probability >= max_precip_prob:
        return True, f"High precipitation probability ({round(precipitation_probability)}%). Keep windows closed."

    # 2. Wind Hazard Veto
    if wind_speed is not None and wind_speed >= high_wind_speed:
        return True, f"High sustained wind speed ({round(wind_speed, 1)} mph) causes severe interior drafts."

    if wind_gust is not None and wind_gust >= high_wind_gust:
        return True, f"High wind gusts ({round(wind_gust, 1)} mph) risk window or property damage."

    # 3. Air Quality Veto
    if aqi_value is not None and aqi_value >= aqi_threshold:
        return True, f"Unfavorable Air Quality Index (AQI {round(aqi_value)}). Keep windows closed."

    return False, ""


def is_weather_stable(
    forecast: list[dict[str, Any]],
    current_condition: str | None,
    last_bad_weather_time: str | datetime.datetime | None = None,
    hours: int = 2,
    now: datetime.datetime | None = None,
) -> bool:
    """Verifies that weather is stable with zero bad weather in immediate lookahead
    and enforces a 60-minute quiet period after bad weather (wet ground protection).
    """
    if last_bad_weather_time:
        try:
            if isinstance(last_bad_weather_time, str):
                last_dt = datetime.datetime.fromisoformat(last_bad_weather_time)
            elif isinstance(last_bad_weather_time, datetime.datetime):
                last_dt = last_bad_weather_time
            else:
                last_dt = None

            if last_dt:
                curr_dt = now or datetime.datetime.now(datetime.timezone.utc)
                if curr_dt.tzinfo is None:
                    curr_dt = curr_dt.replace(tzinfo=datetime.timezone.utc)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
                if (curr_dt - last_dt).total_seconds() < 3600:
                    return False
        except Exception:
            pass

    if current_condition and current_condition.lower() in BAD_WEATHER_CONDITIONS:
        return False

    for i in range(min(hours, len(forecast))):
        f = forecast[i]
        cond = f.get("condition")
        if cond and str(cond).lower() in BAD_WEATHER_CONDITIONS:
            return False
        if float(f.get("precipitation_probability", 0.0) or 0.0) > 30.0:
            return False

    return True


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
    is_bedtime: bool = False,
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
        return False, f"Outdoor dew point ({dp_out:.1f}°F) introduces unacceptable latent load (limit: {dew_point_ceiling:.1f}°F)."

    # 2. Projected RH Comfort Bounds Check
    if inside_upper_bound is not None:
        if proj_rh > (inside_upper_bound - humidity_buffer):
            return False, f"Projected indoor RH ({proj_rh:.1f}%) exceeds comfort limit ({inside_upper_bound:.1f}%)."
    if inside_lower_bound is not None and not is_bedtime:
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
    """Calculates psychrometric box (or legacy polynomial) comfort humidity lower and upper bounds for a given temperature."""
    if not comfort_profile:
        return None, None

    # 1. Psychrometric Box Formulation
    if comfort_profile.get("type") == "psychrometric_box" or "temp_min" in comfort_profile:
        temp_min = float(comfort_profile.get("temp_min", 66.0))
        temp_max = float(comfort_profile.get("temp_max", 76.0))
        hum_min = float(comfort_profile.get("humidity_min", 25.0))
        hum_max = float(comfort_profile.get("humidity_max", 60.0))
        dp_max = float(comfort_profile.get("dew_point_max", 58.0))
        roll_off_temp = float(comfort_profile.get("roll_off_temp", min(75.0, temp_max)))

        # Upper Bound
        if temp_f <= roll_off_temp:
            dp_rh = PsychrometricEngine.projected_rh(dp_max, 100.0, temp_f)
            upper_bound = min(hum_max, dp_rh)
        else:
            # Linear roll-off to 20.0% at temp_max
            dp_rh_at_rolloff = PsychrometricEngine.projected_rh(dp_max, 100.0, roll_off_temp)
            rh_at_rolloff = min(hum_max, dp_rh_at_rolloff)
            target_rh_at_max = 20.0
            if temp_max > roll_off_temp:
                slope = (target_rh_at_max - rh_at_rolloff) / (temp_max - roll_off_temp)
                upper_bound = rh_at_rolloff + slope * (temp_f - roll_off_temp)
            else:
                upper_bound = target_rh_at_max

        # Lower Bound
        if temp_f >= 70.0:
            lower_bound = hum_min
        elif temp_f >= temp_min:
            slope = (hum_min - 50.0) / (70.0 - temp_min)
            lower_bound = 50.0 + slope * (temp_f - temp_min)
        else:
            lower_bound = 50.0

        lower_bound = max(15.0, min(80.0, lower_bound))
        upper_bound = max(20.0, min(85.0, upper_bound))
        if lower_bound > upper_bound:
            lower_bound = upper_bound

        return lower_bound, upper_bound

    # 2. Legacy Polynomial Fallback
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
    """Determines macro seasonal mode (Heatwave Prep, Spring/Fall Transition, or Normal)
    and evaluates the 48-Hour Imminent Threat Suspension Gate.
    """
    if not forecast:
        return "normal", None

    lookahead_forecast = forecast[: (lookahead_days * 24)]
    if not lookahead_forecast:
        return "normal", None

    daily_highs: dict[str, float] = {}
    for f in lookahead_forecast:
        dt_raw = f.get("datetime")
        if isinstance(dt_raw, str):
            try:
                dt = datetime.datetime.fromisoformat(dt_raw)
            except Exception:
                continue
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

    candidate_mode = "normal"
    candidate_details: str | None = None

    # Severe Heatwave Check: >= 7°F spike above average daily high and >= 88°F
    heat_spike = max_lookahead_temp - avg_daily_high
    if heat_spike >= 7.0 and max_lookahead_temp >= max(88.0, active_cool_sp + 12.0):
        candidate_mode = "heatwave_prep"
        candidate_details = (
            f"Seasonal Mode: Severe heatwave approaching ({round(max_lookahead_temp, 1)}°F peak, "
            f"+{round(heat_spike, 1)}°F above 7-day average high). Engaging aggressive thermal pre-cooling."
        )
    else:
        avg_temp = sum(float(f["temperature"]) for f in lookahead_forecast) / len(lookahead_forecast)
        min_comfort = 68.0
        if comfort_profile:
            if "temp_min" in comfort_profile:
                min_comfort = float(comfort_profile["temp_min"])
            elif comfort_profile.get("lower_profile", {}).get("temperature_data_points"):
                min_comfort = min(comfort_profile["lower_profile"]["temperature_data_points"])

        if hvac_mode == "cool" and avg_temp < min_comfort:
            candidate_mode = "fall_transition"
            candidate_details = (
                f"Seasonal Mode: Avg forecast over next {lookahead_days} days is {round(avg_temp, 1)}°F "
                f"(below comfort minimum). Prioritizing heat retention."
            )
        elif hvac_mode == "heat" and avg_temp > (min_comfort - 5.0):
            candidate_mode = "spring_transition"
            candidate_details = (
                f"Seasonal Mode: Avg forecast over next {lookahead_days} days is {round(avg_temp, 1)}°F. "
                f"Prioritizing cool air retention."
            )

    # 48-Hour Imminent Threat Suspension Gate
    if candidate_mode in ["spring_transition", "heatwave_prep"]:
        upcoming_48h = forecast[:48] if forecast else []
        upcoming_peak = max([float(f.get("temperature", 70.0)) for f in upcoming_48h]) if upcoming_48h else active_cool_sp
        if upcoming_peak < active_cool_sp:
            mode_name = "Heatwave Prep" if candidate_mode == "heatwave_prep" else "Spring Transition"
            return (
                "normal",
                f"Seasonal Mode: 7-day forecast indicates {mode_name}, but suspended for today. "
                f"Upcoming 48h high is only {round(upcoming_peak, 1)}°F (below cooling threshold {round(active_cool_sp, 1)}°F).",
            )
        return candidate_mode, candidate_details

    elif candidate_mode == "fall_transition":
        upcoming_48h = forecast[:48] if forecast else []
        upcoming_low = min([float(f.get("temperature", 70.0)) for f in upcoming_48h]) if upcoming_48h else active_heat_sp
        if upcoming_low > active_heat_sp:
            return (
                "normal",
                f"Seasonal Mode: 7-day average indicates Fall Transition, but suspended for today. "
                f"Upcoming 48h low is {round(upcoming_low, 1)}°F (above heating threshold {round(active_heat_sp, 1)}°F).",
            )
        return candidate_mode, candidate_details

    return candidate_mode, candidate_details


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
    parent_bedtime: datetime.time | None = None,
    bedtime_start: datetime.time | None = None,
    morning_start: datetime.time | None = None,
    parent_bedtime_hour: int | None = None,
    morning_start_hour: int = 4,
    active_heat_sp: float = 68.0,
    active_cool_sp: float = 75.0,
    now: datetime.datetime | None = None,
    solcast_forecast: list[dict[str, Any]] | None = None,
    is_currently_open: bool = False,
    last_bad_weather_time: str | datetime.datetime | None = None,
    min_enthalpy_delta: float = 1.2,
    max_dew_point: float = 58.0,
    open_temp_margin: float = 1.0,
    close_temp_margin: float = 0.2,
) -> ZonePlanResult:
    """Runs simulation and evaluates the optimal action plan for a zone.
    Implements 100% parity with legacy smart_window_advisor.py, with minute-precise
    bedtime anchoring and fixed 24-hour rollover logic.
    """
    ref_now = now or datetime.datetime.now(datetime.timezone.utc)
    if comfort_profile and "dew_point_max" in comfort_profile:
        max_dew_point = float(comfort_profile["dew_point_max"])
    comfort_bounds_fn = lambda t: get_comfort_bounds(t, comfort_profile)

    evaluator_fn = lambda **kwargs: is_outside_air_favorable(
        min_enthalpy_delta=min_enthalpy_delta,
        max_dew_point=max_dew_point,
        open_temp_margin=open_temp_margin,
        close_temp_margin=close_temp_margin,
        is_bedtime=is_bedtime,
        **kwargs,
    )

    if not forecast:
        return ZonePlanResult(
            recommended_state="Close Windows",
            details_message=f"Keep {zone_name} windows closed (no forecast data available).",
            history=[],
            actions=[SimulationAction("closed", 1)],
            eco_mode_requested=False,
            scenario="no_forecast",
        )

    # Standard closed baseline history for fallback
    closed_baseline = run_static_simulation(
        initial_temp=inside_temp,
        initial_humidity=inside_humidity,
        forecast=forecast[:12],
        model_temp=models.get("temp_closed"),
        model_hum=models.get("humidity_closed"),
        strategy="closed",
        lat=lat,
        lon=lon,
        solcast_forecast=solcast_forecast,
    )

    lower_bound, upper_bound = get_comfort_bounds(inside_temp, comfort_profile)

    # Calculate exact morning target datetime
    m_hour = morning_start.hour if morning_start else morning_start_hour
    m_min = morning_start.minute if morning_start else 0
    morning_dt = ref_now.replace(hour=m_hour, minute=m_min, second=0, microsecond=0)
    if morning_dt <= ref_now:
        morning_dt += datetime.timedelta(days=1)

    # =========================================================================
    # 1. Check Comfort Veto / Comfort Recovery
    # =========================================================================
    if is_bedtime:
        is_uncomfortable = (upper_bound is not None and inside_humidity > upper_bound)
    else:
        is_uncomfortable = (
            (upper_bound is not None and inside_humidity > upper_bound)
            or (lower_bound is not None and inside_humidity < lower_bound)
        )

    if is_uncomfortable and forecast:
        hours_until_morning = max(1, int((morning_dt - ref_now).total_seconds() / 3600))
        sim_limit = max(12, hours_until_morning + 1)
        comfort_sim_forecast = forecast[:sim_limit]

        recov_history, recov_actions = run_hybrid_simulation(
            initial_temp=inside_temp,
            initial_humidity=inside_humidity,
            forecast=comfort_sim_forecast,
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

        recov_closed = run_static_simulation(
            initial_temp=inside_temp,
            initial_humidity=inside_humidity,
            forecast=comfort_sim_forecast,
            model_temp=models.get("temp_closed"),
            model_hum=models.get("humidity_closed"),
            strategy="closed",
            lat=lat,
            lon=lon,
            solcast_forecast=solcast_forecast,
        )

        recovery_hour = None
        for i, point in enumerate(recov_history):
            lb, ub = get_comfort_bounds(point["temp"], comfort_profile)
            if is_bedtime:
                if ub is not None and point["humidity"] <= ub:
                    recovery_hour = i
                    break
            else:
                if lb is not None and ub is not None and lb <= point["humidity"] <= ub:
                    recovery_hour = i
                    break

        if upper_bound is not None and inside_humidity > upper_bound:
            if recovery_hour is not None and recovery_hour <= hours_until_morning:
                display_hours = max(1, recovery_hour)
                if recov_actions and recov_actions[0].action == "open":
                    idx = min(recov_actions[0].hours - 1, len(recov_history) - 1)
                    idx = max(0, idx)
                    if recov_closed and len(recov_closed) > idx and recov_closed[idx]["temp"] < recov_history[idx]["temp"]:
                        return ZonePlanResult(
                            recommended_state="Close Windows",
                            details_message=f"Keep {zone_name} windows closed. Opening them would result in a warmer temperature than keeping them closed.",
                            history=recov_closed,
                            actions=[SimulationAction("closed", 1)],
                            eco_mode_requested=False,
                            scenario="comfort_recovery",
                        )
                    return ZonePlanResult(
                        recommended_state="Open Windows",
                        details_message=f"Open {zone_name} windows to passively cool. Comfort will be restored in ~{display_hours} hour(s).{_get_temp_change_msg(inside_temp, recov_history, recov_actions, recov_closed)}",
                        history=recov_history,
                        actions=recov_actions,
                        eco_mode_requested=False,
                        scenario="comfort_recovery",
                        hours_to_comfort=display_hours,
                    )
                else:
                    change_dt_str = None
                    if len(recov_actions) > 1 and recov_actions[0].action == "closed" and recov_actions[1].action == "open":
                        change_dt = _parse_local_dt(comfort_sim_forecast[recov_actions[0].hours].get("datetime"), ref_now)
                        change_dt_str = _format_time(change_dt)
                    msg = (
                        f"Keep {zone_name} windows closed for now, open around {change_dt_str} to passively cool. Comfort restored in ~{display_hours} hour(s)."
                        if change_dt_str
                        else f"Keep {zone_name} windows closed. Passive cooling will restore comfort in ~{display_hours} hour(s)."
                    )
                    return ZonePlanResult(
                        recommended_state="Close Windows",
                        details_message=msg,
                        history=recov_history,
                        actions=recov_actions,
                        eco_mode_requested=False,
                        scenario="comfort_recovery",
                        hours_to_comfort=display_hours,
                    )
            else:
                msg = "HVAC is needed." if hvac_mode == "cool" else "A change to 'cool' mode is needed."
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=f"Keep {zone_name} windows closed. {msg} Passive recovery would take too long.",
                    history=recov_closed,
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    comfort_recovery_triggered=True,
                    scenario="comfort_recovery",
                )

        elif not is_bedtime and lower_bound is not None and inside_humidity < lower_bound:
            if recovery_hour is not None and recovery_hour <= hours_until_morning:
                display_hours = max(1, recovery_hour)
                if recov_actions and recov_actions[0].action == "open":
                    return ZonePlanResult(
                        recommended_state="Open Windows",
                        details_message=f"Open {zone_name} windows to passively heat. Comfort will be restored in ~{display_hours} hour(s).{_get_temp_change_msg(inside_temp, recov_history, recov_actions, recov_closed)}",
                        history=recov_history,
                        actions=recov_actions,
                        eco_mode_requested=False,
                        scenario="comfort_recovery",
                        hours_to_comfort=display_hours,
                    )
                else:
                    change_dt_str = None
                    if len(recov_actions) > 1 and recov_actions[0].action == "closed" and recov_actions[1].action == "open":
                        change_dt = _parse_local_dt(comfort_sim_forecast[recov_actions[0].hours].get("datetime"), ref_now)
                        change_dt_str = _format_time(change_dt)
                    msg = (
                        f"Keep {zone_name} windows closed for now, open around {change_dt_str} to passively heat. Comfort restored in ~{display_hours} hour(s)."
                        if change_dt_str
                        else f"Keep {zone_name} windows closed. Passive heating will restore comfort in ~{display_hours} hour(s)."
                    )
                    return ZonePlanResult(
                        recommended_state="Close Windows",
                        details_message=msg,
                        history=recov_history,
                        actions=recov_actions,
                        eco_mode_requested=False,
                        scenario="comfort_recovery",
                        hours_to_comfort=display_hours,
                    )
            else:
                msg = "HVAC is needed." if hvac_mode == "heat" else "A change to 'heat' mode is needed."
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=f"Keep {zone_name} windows closed. {msg} Passive recovery would take too long.",
                    history=recov_closed,
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    comfort_recovery_triggered=True,
                    scenario="comfort_recovery",
                )

    # =========================================================================
    # 2. Check Seasonal Override (Early Returns)
    # =========================================================================
    if seasonal_mode in ["fall_transition", "coldsnap_prep"]:
        threshold = inside_temp if is_currently_open else inside_temp + 0.5
        if current_outside_temp < threshold:
            return ZonePlanResult(
                recommended_state="Close Windows",
                details_message=f"Keep {zone_name} closed to trap free heat.",
                history=closed_baseline,
                actions=[SimulationAction("closed", len(forecast[:12]))],
                eco_mode_requested=True,
                scenario="fall_trap_heat",
            )

    if seasonal_mode in ["spring_transition", "heatwave_prep"]:
        threshold = inside_temp if is_currently_open else inside_temp - 0.5
        if current_outside_temp > threshold:
            eco_flag = (seasonal_mode == "spring_transition")
            return ZonePlanResult(
                recommended_state="Close Windows",
                details_message=f"Keep {zone_name} closed to block outside heat.",
                history=closed_baseline,
                actions=[SimulationAction("closed", len(forecast[:12]))],
                eco_mode_requested=eco_flag,
                scenario="block_outside_heat",
            )

    # =========================================================================
    # 3. Check Bedtime Active Purge Logic
    # =========================================================================
    if is_bedtime and hvac_mode == "cool":
        if inside_temp > active_cool_sp:
            passive_history = run_static_simulation(
                initial_temp=inside_temp,
                initial_humidity=inside_humidity,
                forecast=forecast[:2],
                model_temp=models.get("temp_closed"),
                model_hum=models.get("humidity_closed"),
                strategy="closed",
                lat=lat,
                lon=lon,
                solcast_forecast=solcast_forecast,
            )
            if passive_history and passive_history[-1]["temp"] <= active_cool_sp:
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=f"Keep {zone_name} closed. Will cool passively.",
                    history=passive_history,
                    actions=[SimulationAction("closed", 2)],
                    eco_mode_requested=True,
                    scenario="passive_sleep_cooling",
                )

            favorable_now, _ = evaluator_fn(
                outside_temp=forecast[0]["temperature"],
                outside_rh=forecast[0]["humidity"],
                inside_temp=inside_temp,
                inside_rh=inside_humidity,
                hvac_mode=hvac_mode,
                seasonal_mode=seasonal_mode,
                inside_lower_bound=lower_bound,
                inside_upper_bound=upper_bound,
                safe_min_temp=safe_min_temp,
                safe_max_temp=safe_max_temp,
                is_currently_open=is_currently_open,
            )
            if favorable_now:
                active_history = run_static_simulation(
                    initial_temp=inside_temp,
                    initial_humidity=inside_humidity,
                    forecast=forecast[:2],
                    model_temp=models.get("temp_open"),
                    model_hum=models.get("humidity_open"),
                    strategy="open",
                    lat=lat,
                    lon=lon,
                    solcast_forecast=solcast_forecast,
                )
                if active_history and active_history[-1]["temp"] <= active_cool_sp:
                    return ZonePlanResult(
                        recommended_state="Open Windows",
                        details_message=f"Open {zone_name} for an active bedtime purge.",
                        history=active_history,
                        actions=[SimulationAction("open", 2)],
                        eco_mode_requested=True,
                        scenario="bedtime_purge",
                    )

            return ZonePlanResult(
                recommended_state="Close Windows",
                details_message=f"Keep {zone_name} closed. HVAC needed for sleep.",
                history=passive_history or closed_baseline,
                actions=[SimulationAction("closed", 2)],
                eco_mode_requested=False,
                scenario="hvac_needed_for_sleep",
            )

    # =========================================================================
    # 4. Evening & Overnight Simulation Slicing
    # =========================================================================
    if is_bedtime or is_evening:
        overnight_forecast: list[dict[str, Any]] = []
        for i, hour_data in enumerate(forecast):
            local_dt = _parse_local_dt(hour_data.get("datetime"), ref_now)
            overnight_forecast.append(hour_data)
            if local_dt >= morning_dt and i > 0:
                break

        macro_override = False
        if is_bedtime:
            o_hist = run_static_simulation(
                initial_temp=inside_temp,
                initial_humidity=inside_humidity,
                forecast=overnight_forecast,
                model_temp=models.get("temp_open"),
                model_hum=models.get("humidity_open"),
                strategy="open",
                lat=lat,
                lon=lon,
                solcast_forecast=solcast_forecast,
            )
            c_hist = run_static_simulation(
                initial_temp=inside_temp,
                initial_humidity=inside_humidity,
                forecast=overnight_forecast,
                model_temp=models.get("temp_closed"),
                model_hum=models.get("humidity_closed"),
                strategy="closed",
                lat=lat,
                lon=lon,
                solcast_forecast=solcast_forecast,
            )
            if o_hist and c_hist:
                o_temp, c_temp = o_hist[-1]["temp"], c_hist[-1]["temp"]
                if hvac_mode == "cool" or seasonal_mode in ["spring_transition", "heatwave_prep"]:
                    if o_temp < c_temp and o_temp <= (inside_temp + 3.0):
                        macro_override = True
                elif hvac_mode == "heat" or seasonal_mode in ["fall_transition", "coldsnap_prep"]:
                    if o_temp > c_temp and o_temp >= (inside_temp - 3.0):
                        macro_override = True

        history, actions = run_hybrid_simulation(
            initial_temp=inside_temp,
            initial_humidity=inside_humidity,
            forecast=overnight_forecast,
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
            macro_override=macro_override,
            is_currently_open=is_currently_open,
        )

        is_safe_all_night = len(actions) == 1 and actions[0].action == "open"
        eco_flag = (seasonal_mode != "normal" and seasonal_mode != "heatwave_prep")

        if is_safe_all_night:
            if not is_weather_stable(forecast, forecast[0].get("condition"), last_bad_weather_time, now=ref_now):
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=f"Thermally favorable, but keep {zone_name} closed due to unstable weather/rain.",
                    history=closed_baseline,
                    actions=[SimulationAction("closed", len(overnight_forecast))],
                    eco_mode_requested=False,
                    scenario="weather_unstable",
                )
            pre_cool_msg = "to pre-cool" if seasonal_mode in ["spring_transition", "heatwave_prep"] else "for the entire overnight period"
            return ZonePlanResult(
                recommended_state="Open Windows",
                details_message=f"Open {zone_name} {pre_cool_msg}.",
                history=history,
                actions=actions,
                eco_mode_requested=eco_flag,
                scenario="safe_all_night",
            )
        else:
            msg = "for the entire overnight period."
            scenario = "unfavorable_all_night"
            target_time_str = None

            if actions and actions[0].action == "open":
                close_hour_idx = actions[0].hours
                if close_hour_idx < len(overnight_forecast):
                    close_dt = _parse_local_dt(overnight_forecast[close_hour_idx].get("datetime"), ref_now)
                    target_time_str = _format_time(close_dt)
                    actual_temp_at_close = history[max(0, min(close_hour_idx - 1, len(history) - 1))]["temp"]
                    if actual_temp_at_close <= safe_min_temp:
                        msg = f"for the entire overnight period. Leaving them open would over-cool the house by {target_time_str}."
                        scenario = "overcooling"
                    else:
                        msg = f"for the entire overnight period. Outside conditions (temperature/humidity) become unfavorable by {target_time_str}."
                        scenario = "late_night_unfavorable"

            elif actions and actions[0].action == "closed":
                if len(actions) > 1 and actions[1].action == "open":
                    open_hour_idx = actions[0].hours
                    if open_hour_idx < len(overnight_forecast):
                        open_dt = _parse_local_dt(overnight_forecast[open_hour_idx].get("datetime"), ref_now)
                        target_time_str = _format_time(open_dt)

                        if is_evening and (parent_bedtime is not None or parent_bedtime_hour is not None):
                            pb_h = parent_bedtime.hour if parent_bedtime else (parent_bedtime_hour or 20)
                            pb_m = parent_bedtime.minute if parent_bedtime else 30
                            pb_dt = ref_now.replace(hour=pb_h, minute=pb_m, second=0, microsecond=0)
                            bs_h = bedtime_start.hour if bedtime_start else 18
                            if pb_h < bs_h and ref_now.hour >= bs_h:
                                pb_dt += datetime.timedelta(days=1)

                            # FIXED: Only advise "before bed" if:
                            # 1. We are currently before parent bedtime (ref_now < pb_dt)
                            # 2. The opening window occurs BEFORE parent bedtime (open_dt <= pb_dt)
                            if ref_now < pb_dt and open_dt <= pb_dt:
                                return ZonePlanResult(
                                    recommended_state="Close Windows",
                                    details_message=f"Keep {zone_name} closed now. Open around {target_time_str} before bed.",
                                    history=history,
                                    actions=[SimulationAction("closed", actions[0].hours)],
                                    eco_mode_requested=eco_flag,
                                    scenario="delayed_evening_open",
                                    target_time=target_time_str,
                                )

                        msg = f"for the entire overnight period. Outside air is unfavorable until {target_time_str}."
                        scenario = "unfavorable_all_night"

            return ZonePlanResult(
                recommended_state="Close Windows",
                details_message=f"Keep {zone_name} closed {msg}",
                history=history,
                actions=[SimulationAction("closed", len(overnight_forecast))],
                eco_mode_requested=eco_flag,
                scenario=scenario,
                target_time=target_time_str,
            )

    # =========================================================================
    # 5. Daytime Forward Simulation & Peak Benefit Optimization
    # =========================================================================
    sim_forecast = forecast[:12]
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

    if not actions:
        return ZonePlanResult(
            recommended_state="Close Windows",
            details_message=f"Keep {zone_name} windows closed (unable to compute simulation).",
            history=closed_history,
            actions=[SimulationAction("closed", 1)],
            eco_mode_requested=False,
            scenario="error",
        )

    projected_rh = (
        PsychrometricEngine.projected_rh(current_outside_temp, forecast[0]["humidity"], inside_temp)
        if forecast
        else None
    )

    temp_change = 0.0
    predicted_temp = inside_temp

    if actions and actions[0].action == "open":
        open_hours = actions[0].hours
        open_temps = [h["temp"] for h in history[:open_hours]]

        # Peak benefit duration optimization
        if hvac_mode == "cool" or seasonal_mode in ["spring_transition", "heatwave_prep"]:
            min_temp = min(open_temps)
            best_idx = open_temps.index(min_temp)
            optimal_hours = best_idx + 1
            predicted_temp = min_temp
            temp_change = predicted_temp - inside_temp
        elif hvac_mode == "heat" or seasonal_mode in ["fall_transition", "coldsnap_prep"]:
            max_temp = max(open_temps)
            best_idx = open_temps.index(max_temp)
            optimal_hours = best_idx + 1
            predicted_temp = max_temp
            temp_change = predicted_temp - inside_temp
        else:
            optimal_hours = open_hours
            predicted_temp = open_temps[-1]
            temp_change = predicted_temp - inside_temp

        if optimal_hours < open_hours:
            new_actions = [SimulationAction("open", optimal_hours)]
            rem_hours = sum(a.hours for a in actions) - optimal_hours
            if rem_hours > 0:
                new_actions.append(SimulationAction("closed", rem_hours))
            actions = new_actions

        # Directionality & Noticeable Benefit Vetoes
        if hvac_mode == "cool" or seasonal_mode in ["spring_transition", "heatwave_prep"]:
            if temp_change > 0.05:
                msg = f"Keep {zone_name} windows closed. Opening them would result in the temperature rising (from {inside_temp:.1f}°F to {predicted_temp:.1f}°F); let the A/C handle it."
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=msg,
                    history=closed_history,
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    scenario="veto_temp_rise",
                )
            elif (
                not is_currently_open
                and temp_change > -0.5
                and (lower_bound is None or upper_bound is None or (lower_bound <= inside_humidity <= upper_bound))
            ):
                msg = f"Keep {zone_name} windows closed. Potential cooling gain is negligible (< 0.5°F drop); let the HVAC maintain temperature."
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=msg,
                    history=closed_history,
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    scenario="veto_negligible_gain",
                )

        elif hvac_mode == "heat" or seasonal_mode in ["fall_transition", "coldsnap_prep"]:
            if temp_change < -0.05:
                msg = f"Keep {zone_name} windows closed. Opening them would result in the temperature dropping (from {inside_temp:.1f}°F to {predicted_temp:.1f}°F); let the heater handle it."
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=msg,
                    history=closed_history,
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    scenario="veto_temp_drop",
                )
            elif (
                not is_currently_open
                and temp_change < 0.5
                and (lower_bound is None or upper_bound is None or (lower_bound <= inside_humidity <= upper_bound))
            ):
                msg = f"Keep {zone_name} windows closed. Potential heating gain is negligible (< 0.5°F rise); let the heater handle it."
                return ZonePlanResult(
                    recommended_state="Close Windows",
                    details_message=msg,
                    history=closed_history,
                    actions=[SimulationAction("closed", 1)],
                    eco_mode_requested=False,
                    scenario="veto_negligible_gain",
                )

    # Contextual Explanation Strings
    if seasonal_mode in ["spring_transition", "heatwave_prep"]:
        open_reason = "to actively pre-cool the house"
        if current_outside_temp > inside_temp:
            close_reason = "to block outside heat"
        elif upper_bound is not None and projected_rh is not None and projected_rh > upper_bound:
            close_reason = f"due to high humidity ({projected_rh:.1f}% projected vs {upper_bound:.1f}% limit at {inside_temp:.1f}°F)"
        elif lower_bound is not None and projected_rh is not None and projected_rh < lower_bound:
            close_reason = f"due to low humidity ({projected_rh:.1f}% projected vs {lower_bound:.1f}% limit at {inside_temp:.1f}°F)"
        else:
            close_reason = "due to unfavorable outside conditions"
    elif seasonal_mode in ["fall_transition", "coldsnap_prep"]:
        open_reason = "to actively pre-heat the house"
        if current_outside_temp < inside_temp:
            close_reason = "to trap indoor heat"
        elif upper_bound is not None and projected_rh is not None and projected_rh > upper_bound:
            close_reason = f"due to high humidity ({projected_rh:.1f}% projected vs {upper_bound:.1f}% limit at {inside_temp:.1f}°F)"
        elif lower_bound is not None and projected_rh is not None and projected_rh < lower_bound:
            close_reason = f"due to low humidity ({projected_rh:.1f}% projected vs {lower_bound:.1f}% limit at {inside_temp:.1f}°F)"
        else:
            close_reason = "due to unfavorable outside conditions"
    elif hvac_mode == "cool":
        open_reason = "to offset the natural temperature rise" if temp_change > 0.05 else "for free cooling"
        if current_outside_temp > inside_temp:
            close_reason = "to keep the cool air inside"
        elif upper_bound is not None and projected_rh is not None and projected_rh > upper_bound:
            close_reason = f"due to high humidity ({projected_rh:.1f}% projected vs {upper_bound:.1f}% limit at {inside_temp:.1f}°F)"
        else:
            close_reason = "due to unfavorable outside conditions"
    elif hvac_mode == "heat":
        open_reason = "to offset natural heat loss" if temp_change < -0.05 else "for free heating"
        if current_outside_temp < inside_temp:
            close_reason = "to keep the heat inside"
        elif lower_bound is not None and projected_rh is not None and projected_rh < lower_bound:
            close_reason = f"due to low humidity ({projected_rh:.1f}% projected vs {lower_bound:.1f}% limit at {inside_temp:.1f}°F)"
        else:
            close_reason = "due to unfavorable outside conditions"
    else:
        open_reason = "to improve comfort"
        close_reason = "to maintain current temperature"

    eco_mode_on = (seasonal_mode != "normal" and seasonal_mode != "heatwave_prep")

    if actions[0].action == "open":
        if not is_weather_stable(forecast, forecast[0].get("condition"), last_bad_weather_time, now=ref_now):
            return ZonePlanResult(
                recommended_state="Close Windows",
                details_message=f"Thermally favorable, but keep {zone_name} closed due to unstable weather/rain.",
                history=closed_history,
                actions=actions,
                eco_mode_requested=eco_mode_on,
                scenario="weather_unstable",
            )

        temp_msg = _get_temp_change_msg(inside_temp, history, actions, closed_history)
        if len(actions) > 1 and actions[0].hours < len(sim_forecast):
            change_dt = _parse_local_dt(sim_forecast[actions[0].hours].get("datetime"), ref_now)
            target_time_str = _format_time(change_dt)
            return ZonePlanResult(
                recommended_state="Open Windows",
                details_message=f"Open {zone_name} windows {open_reason}. Close around {target_time_str}.{temp_msg}",
                history=history,
                actions=actions,
                eco_mode_requested=eco_mode_on,
                scenario="open_with_close_target",
                target_time=target_time_str,
                temp_change=temp_change,
                predicted_temp=predicted_temp,
            )

        return ZonePlanResult(
            recommended_state="Open Windows",
            details_message=f"Open {zone_name} windows {open_reason}.{temp_msg}",
            history=history,
            actions=actions,
            eco_mode_requested=eco_mode_on,
            scenario="open_continuous",
            temp_change=temp_change,
            predicted_temp=predicted_temp,
        )
    else:
        if len(actions) > 1 and actions[0].hours < len(sim_forecast):
            change_dt = _parse_local_dt(sim_forecast[actions[0].hours].get("datetime"), ref_now)
            target_time_str = _format_time(change_dt)
            return ZonePlanResult(
                recommended_state="Close Windows",
                details_message=f"Keep {zone_name} windows closed {close_reason}. Open around {target_time_str} {open_reason}.",
                history=history,
                actions=actions,
                eco_mode_requested=eco_mode_on,
                scenario="closed_with_open_target",
                target_time=target_time_str,
            )

        return ZonePlanResult(
            recommended_state="Close Windows",
            details_message=f"Keep {zone_name} windows closed {close_reason}.",
            history=history,
            actions=actions,
            eco_mode_requested=eco_mode_on,
            scenario="closed_continuous",
        )
