"""10-Minute micro-step thermal and moisture forward simulation engine.
Formulations derived from building envelope heat and mass transfer.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Callable

from .models import RateModel, SimulationAction
from .psychrometrics import PsychrometricEngine
from .solar import calculate_solar_position, decompose_facade_insolation, interpolate_solcast_power

STEPS_PER_HOUR = 6  # 10-minute micro-steps


def _parse_utc_dt(dt_raw: Any) -> datetime.datetime | None:
    """Parses a string or datetime into a timezone-aware UTC datetime."""
    if isinstance(dt_raw, str):
        try:
            dt = datetime.datetime.fromisoformat(dt_raw)
        except Exception:
            return None
    elif isinstance(dt_raw, datetime.datetime):
        dt = dt_raw
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _prepare_forecast_timeline(
    forecast: list[dict[str, Any]],
) -> list[ datetime.datetime | None]:
    """Extracts parsed UTC timestamps for each forecast entry."""
    return [_parse_utc_dt(f.get("datetime")) for f in forecast]


def _interpolate_weather_at_step(
    forecast: list[dict[str, Any]],
    timeline: list[datetime.datetime | None],
    hour_idx: int,
    step: int,
) -> tuple[float, float, float, float, datetime.datetime]:
    """Interpolates weather variables and resolves exact wall-clock step_dt.

    When forecast[0] is a mid-hour observation anchor (0 < dt_1 - dt_0 < 3600s),
    interpolates weather at true wall-clock time (dt_0 + hour_idx*60m + step*10m)
    across the timestamped forecast points so partial first hours are not stretched.
    Falls back to index-based interpolation when timestamps are absent or non-monotonic.
    """
    dt_0 = timeline[0] if timeline else None
    use_wall_clock = (
        len(timeline) >= 2
        and dt_0 is not None
        and timeline[1] is not None
        and 0.0 < (timeline[1] - dt_0).total_seconds() < 3600.0
        and all(
            timeline[k] is not None and timeline[k + 1] is not None and timeline[k + 1] >= timeline[k]
            for k in range(len(timeline) - 1)
        )
    )

    if use_wall_clock and dt_0 is not None:
        step_dt = dt_0 + datetime.timedelta(minutes=(hour_idx * 60 + step * (60 / STEPS_PER_HOUR)))
        # Find bracketing forecast interval [k, k+1]
        k = len(forecast) - 1
        progress = 0.0
        for idx in range(len(timeline) - 1):
            t_start = timeline[idx]
            t_end = timeline[idx + 1]
            if t_start is not None and t_end is not None and t_start <= step_dt <= t_end:
                k = idx
                span = (t_end - t_start).total_seconds()
                progress = ((step_dt - t_start).total_seconds() / span) if span > 0 else 0.0
                break
        else:
            if timeline[0] is not None and step_dt < timeline[0]:
                k = 0
                progress = 0.0
        current_f = forecast[k]
        next_f = forecast[k + 1] if (k + 1 < len(forecast) and progress > 0.0) else current_f
    else:
        current_f = forecast[hour_idx]
        next_f = forecast[hour_idx + 1] if hour_idx + 1 < len(forecast) else current_f
        progress = step / STEPS_PER_HOUR
        utc_dt = timeline[hour_idx] or datetime.datetime.now(datetime.timezone.utc)
        step_dt = utc_dt + datetime.timedelta(minutes=(step * (60 / STEPS_PER_HOUR)))

    outside_temp = float(current_f["temperature"]) + (float(next_f["temperature"]) - float(current_f["temperature"])) * progress
    outside_humidity = float(current_f["humidity"]) + (float(next_f["humidity"]) - float(current_f["humidity"])) * progress
    cur_wind = float(current_f.get("wind_speed", 0.0))
    nxt_wind = float(next_f.get("wind_speed", cur_wind))
    wind_speed = cur_wind + (nxt_wind - cur_wind) * progress

    cur_clouds = float(current_f.get("cloud_coverage", 50.0))
    nxt_clouds = float(next_f.get("cloud_coverage", cur_clouds))
    cloud_coverage = cur_clouds + (nxt_clouds - cur_clouds) * progress

    return outside_temp, outside_humidity, wind_speed, cloud_coverage, step_dt

def run_hybrid_simulation(
    initial_temp: float,
    initial_humidity: float,
    forecast: list[dict[str, Any]],
    models: dict[str, RateModel],
    hvac_mode: str,
    seasonal_mode: str,
    safe_min_temp: float,
    safe_max_temp: float,
    lat: float,
    lon: float,
    favorability_evaluator: Callable[..., tuple[bool, str]],
    comfort_bounds_getter: Callable[[float], tuple[float | None, float | None]],
    solcast_forecast: list[dict[str, Any]] | None = None,
    macro_override: bool = False,
    is_currently_open: bool = False,
) -> tuple[list[dict[str, float]], list[SimulationAction]]:
    """Runs a 10-minute microstep forward simulation evaluating dynamic window actions."""
    simulated_temp = initial_temp
    simulated_humidity = initial_humidity
    history: list[dict[str, float]] = []
    actions: list[SimulationAction] = []

    model_temp_open = models.get("temp_open") or RateModel("ransac", slope=0.3, intercept=0.0)
    model_temp_closed = models.get("temp_closed") or RateModel("ransac", slope=0.05, intercept=0.0)
    model_hum_open = models.get("humidity_open")
    model_hum_closed = models.get("humidity_closed")

    last_action = "open" if is_currently_open else "closed"
    timeline = _prepare_forecast_timeline(forecast)

    for i in range(len(forecast)):
        first_step_open = False
        open_steps = 0

        for step in range(STEPS_PER_HOUR):
            outside_temp, outside_humidity, wind_speed, cloud_coverage, step_dt = _interpolate_weather_at_step(
                forecast, timeline, i, step
            )

            elevation, azimuth = calculate_solar_position(step_dt, lat, lon)
            solcast_irradiance = interpolate_solcast_power(step_dt, solcast_forecast or [])

            _, _, _, az_sin, az_cos = decompose_facade_insolation(elevation, azimuth, solcast_irradiance)

            # Evaluate comfort bounds and favorability
            lower_bound, upper_bound = comfort_bounds_getter(simulated_temp)
            is_open_step = (last_action == "open")

            is_favorable, _ = favorability_evaluator(
                outside_temp=outside_temp,
                outside_rh=outside_humidity,
                inside_temp=simulated_temp,
                inside_rh=simulated_humidity,
                hvac_mode=hvac_mode,
                seasonal_mode=seasonal_mode,
                inside_lower_bound=lower_bound,
                inside_upper_bound=upper_bound,
                safe_min_temp=safe_min_temp,
                safe_max_temp=safe_max_temp,
                is_currently_open=is_open_step,
                macro_override=macro_override,
            )

            delta_t = outside_temp - simulated_temp

            rate_open = model_temp_open.calculate_rate(
                delta_t, wind_speed, cloud_coverage, elevation, az_sin, az_cos, "open", solcast_irradiance, False
            ) / STEPS_PER_HOUR

            rate_closed = model_temp_closed.calculate_rate(
                delta_t, wind_speed, cloud_coverage, elevation, az_sin, az_cos, "closed", solcast_irradiance, False
            ) / STEPS_PER_HOUR

            # Hard safety clamps to prevent model divergence (+/- 2.5°F per 10 mins = 15°F/h)
            rate_open = max(-2.5, min(2.5, rate_open))
            rate_closed = max(-2.5, min(2.5, rate_closed))

            # Thermodynamic monotonicity constraint:
            # Convective ventilation adds a parallel advective exchange path along delta_t
            # and cannot transfer heat opposite to the temperature gradient.
            advective_min = (0.05 + 0.003 * wind_speed) * delta_t / STEPS_PER_HOUR
            if delta_t < 0:
                rate_open = min(rate_open, rate_closed + advective_min)
            elif delta_t > 0:
                rate_open = max(rate_open, rate_closed + advective_min)

            # Directional verification:
            should_open = is_favorable
            if is_favorable and (hvac_mode == "cool" or seasonal_mode in ["spring_transition", "heatwave_prep"]) and not macro_override:
                # In cooling season, only open if opening actively cools faster than staying closed
                if rate_open > 0.0 or rate_open >= rate_closed:
                    should_open = False
            elif is_favorable and (hvac_mode == "heat" or seasonal_mode in ["fall_transition", "coldsnap_prep"]):
                # In heating season, only open if opening actively adds warmth and beats staying closed
                if rate_open <= 0.0 or (not macro_override and rate_open <= rate_closed):
                    should_open = False

            current_action = "open" if should_open else "closed"
            last_action = current_action

            if step == 0:
                first_step_open = (current_action == "open")
            if current_action == "open":
                open_steps += 1

            rate_of_change = rate_open if current_action == "open" else rate_closed
            if current_action == "open":
                if delta_t > 0 and rate_of_change > delta_t:
                    rate_of_change = delta_t
                elif delta_t < 0 and rate_of_change < delta_t:
                    rate_of_change = delta_t

            # --- Moisture Physics ---
            outside_ah = PsychrometricEngine.absolute_humidity(outside_temp, outside_humidity)
            simulated_ah = PsychrometricEngine.absolute_humidity(simulated_temp, simulated_humidity)

            simulated_temp += rate_of_change

            hum_model = model_hum_open if current_action == "open" else model_hum_closed
            if hum_model:
                delta_ah = outside_ah - simulated_ah
                h_rate = hum_model.calculate_rate(
                    delta_ah, wind_speed, cloud_coverage, elevation, az_sin, az_cos, current_action, solcast_irradiance, True
                ) / STEPS_PER_HOUR
                h_rate = max(-1.0, min(1.0, h_rate))
                simulated_ah += h_rate
            elif current_action == "open":
                simulated_ah += 1.0 * (outside_ah - simulated_ah) / STEPS_PER_HOUR

            simulated_humidity = PsychrometricEngine.rh_from_absolute_humidity(simulated_temp, simulated_ah)
            simulated_humidity = max(0.0, min(100.0, simulated_humidity))

        history.append({"temp": round(simulated_temp, 2), "humidity": round(simulated_humidity, 2)})

        if i == 0:
            action_lit = "open" if (first_step_open and open_steps >= (STEPS_PER_HOUR // 2)) else "closed"
        else:
            action_lit = "open" if (first_step_open or open_steps >= (STEPS_PER_HOUR // 2 + 1)) else "closed"
        if not actions or actions[-1].action != action_lit:
            actions.append(SimulationAction(action=action_lit, hours=1))
        else:
            actions[-1].hours += 1

    return history, actions

def run_static_simulation(
    initial_temp: float,
    initial_humidity: float,
    forecast: list[dict[str, Any]],
    model_temp: RateModel | None,
    model_hum: RateModel | None,
    strategy: str,
    lat: float,
    lon: float,
    solcast_forecast: list[dict[str, Any]] | None = None,
    model_temp_closed: RateModel | None = None,
) -> list[dict[str, float]]:
    """Runs a baseline static simulation holding the windows in a fixed strategy ('closed' or 'open')."""
    simulated_temp = initial_temp
    simulated_humidity = initial_humidity
    history: list[dict[str, float]] = []

    active_temp_model = model_temp or RateModel("ransac", slope=(0.3 if strategy == "open" else 0.05), intercept=0.0)
    closed_ref_model = model_temp_closed or RateModel("ransac", slope=0.05, intercept=0.0)
    timeline = _prepare_forecast_timeline(forecast)

    for i in range(len(forecast)):
        for step in range(STEPS_PER_HOUR):
            outside_temp, outside_humidity, wind_speed, cloud_coverage, step_dt = _interpolate_weather_at_step(
                forecast, timeline, i, step
            )

            elevation, azimuth = calculate_solar_position(step_dt, lat, lon)
            solcast_irradiance = interpolate_solcast_power(step_dt, solcast_forecast or [])
            _, _, _, az_sin, az_cos = decompose_facade_insolation(elevation, azimuth, solcast_irradiance)

            delta_t = outside_temp - simulated_temp
            rate = active_temp_model.calculate_rate(
                delta_t, wind_speed, cloud_coverage, elevation, az_sin, az_cos, strategy, solcast_irradiance, False
            ) / STEPS_PER_HOUR
            rate = max(-2.5, min(2.5, rate))
            if strategy == "open":
                rate_closed = closed_ref_model.calculate_rate(
                    delta_t, wind_speed, cloud_coverage, elevation, az_sin, az_cos, "closed", solcast_irradiance, False
                ) / STEPS_PER_HOUR
                rate_closed = max(-2.5, min(2.5, rate_closed))
                # Thermodynamic monotonicity constraint:
                # Convective ventilation adds a parallel advective exchange path along delta_t
                # and cannot transfer heat opposite to the temperature gradient.
                advective_min = (0.05 + 0.003 * wind_speed) * delta_t / STEPS_PER_HOUR
                if delta_t < 0:
                    rate = min(rate, rate_closed + advective_min)
                elif delta_t > 0:
                    rate = max(rate, rate_closed + advective_min)
                if delta_t > 0 and rate > delta_t:
                    rate = delta_t
                elif delta_t < 0 and rate < delta_t:
                    rate = delta_t

            outside_ah = PsychrometricEngine.absolute_humidity(outside_temp, outside_humidity)
            simulated_ah = PsychrometricEngine.absolute_humidity(simulated_temp, simulated_humidity)

            simulated_temp += rate

            if model_hum:
                delta_ah = outside_ah - simulated_ah
                h_rate = model_hum.calculate_rate(
                    delta_ah, wind_speed, cloud_coverage, elevation, az_sin, az_cos, strategy, solcast_irradiance, True
                ) / STEPS_PER_HOUR
                h_rate = max(-1.0, min(1.0, h_rate))
                simulated_ah += h_rate
            elif strategy == "open":
                simulated_ah += 1.0 * (outside_ah - simulated_ah) / STEPS_PER_HOUR

            simulated_humidity = PsychrometricEngine.rh_from_absolute_humidity(simulated_temp, simulated_ah)
            simulated_humidity = max(0.0, min(100.0, simulated_humidity))

        history.append({"temp": round(simulated_temp, 2), "humidity": round(simulated_humidity, 2)})

    return history
