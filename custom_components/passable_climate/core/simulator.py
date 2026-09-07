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

    for i in range(len(forecast)):
        current_f = forecast[i]
        next_f = forecast[i + 1] if i + 1 < len(forecast) else current_f
        hourly_action: str | None = None

        for step in range(STEPS_PER_HOUR):
            progress = step / STEPS_PER_HOUR
            outside_temp = current_f["temperature"] + (next_f["temperature"] - current_f["temperature"]) * progress
            outside_humidity = current_f["humidity"] + (next_f["humidity"] - current_f["humidity"]) * progress
            wind_speed = float(current_f.get("wind_speed", 0.0))
            cloud_coverage = float(current_f.get("cloud_coverage", 50.0))

            dt_raw = current_f.get("datetime")
            if isinstance(dt_raw, str):
                utc_dt = datetime.datetime.fromisoformat(dt_raw)
            elif isinstance(dt_raw, datetime.datetime):
                utc_dt = dt_raw
            else:
                utc_dt = datetime.datetime.now(datetime.timezone.utc)

            if utc_dt.tzinfo is None:
                utc_dt = utc_dt.replace(tzinfo=datetime.timezone.utc)

            step_dt = utc_dt + datetime.timedelta(minutes=(step * (60 / STEPS_PER_HOUR)))

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

            # Directional verification:
            should_open = is_favorable
            if is_favorable and (hvac_mode == "cool" or seasonal_mode in ["spring_transition", "heatwave_prep"]) and not macro_override:
                # In cooling season, only open if opening actively cools faster than staying closed
                if rate_open > 0.0 or rate_open >= rate_closed:
                    should_open = False
            elif is_favorable and (hvac_mode == "heat" or seasonal_mode in ["fall_transition", "coldsnap_prep"]) and not macro_override:
                # In heating season, only open if opening actively adds warmth
                if rate_open <= 0.0:
                    should_open = False

            current_action = "open" if should_open else "closed"
            last_action = current_action

            if hourly_action is None:
                hourly_action = current_action

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

        action_lit = "open" if hourly_action == "open" else "closed"
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
) -> list[dict[str, float]]:
    """Runs a baseline static simulation holding the windows in a fixed strategy ('closed' or 'open')."""
    simulated_temp = initial_temp
    simulated_humidity = initial_humidity
    history: list[dict[str, float]] = []

    active_temp_model = model_temp or RateModel("ransac", slope=(0.3 if strategy == "open" else 0.05), intercept=0.0)

    for i in range(len(forecast)):
        current_f = forecast[i]
        next_f = forecast[i + 1] if i + 1 < len(forecast) else current_f

        for step in range(STEPS_PER_HOUR):
            progress = step / STEPS_PER_HOUR
            outside_temp = current_f["temperature"] + (next_f["temperature"] - current_f["temperature"]) * progress
            outside_humidity = current_f["humidity"] + (next_f["humidity"] - current_f["humidity"]) * progress
            wind_speed = float(current_f.get("wind_speed", 0.0))
            cloud_coverage = float(current_f.get("cloud_coverage", 50.0))

            dt_raw = current_f.get("datetime")
            if isinstance(dt_raw, str):
                utc_dt = datetime.datetime.fromisoformat(dt_raw)
            elif isinstance(dt_raw, datetime.datetime):
                utc_dt = dt_raw
            else:
                utc_dt = datetime.datetime.now(datetime.timezone.utc)

            if utc_dt.tzinfo is None:
                utc_dt = utc_dt.replace(tzinfo=datetime.timezone.utc)

            step_dt = utc_dt + datetime.timedelta(minutes=(step * (60 / STEPS_PER_HOUR)))

            elevation, azimuth = calculate_solar_position(step_dt, lat, lon)
            solcast_irradiance = interpolate_solcast_power(step_dt, solcast_forecast or [])
            _, _, _, az_sin, az_cos = decompose_facade_insolation(elevation, azimuth, solcast_irradiance)

            delta_t = outside_temp - simulated_temp
            rate = active_temp_model.calculate_rate(
                delta_t, wind_speed, cloud_coverage, elevation, az_sin, az_cos, strategy, solcast_irradiance, False
            ) / STEPS_PER_HOUR
            rate = max(-2.5, min(2.5, rate))

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
