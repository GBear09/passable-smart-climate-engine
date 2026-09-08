"""Temporal phasing and setpoint resolution engine.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
from typing import Any

PHASE_DAYTIME = "Daytime"
PHASE_EVENING = "Evening (Pre-Sleep)"
PHASE_OVERNIGHT = "Overnight (Sleep)"


def determine_temporal_phase(
    home_state: str | None,
    hvac_profile: str | None,
    now: datetime.datetime,
    bedtime_start: datetime.time = datetime.time(18, 0),
    parent_bedtime: datetime.time = datetime.time(20, 30),
    morning_start: datetime.time = datetime.time(4, 30),
) -> tuple[str, bool, bool]:
    """Determines current temporal phase and returns (phase_name, is_bedtime, is_evening).

    Prioritizes explicit home state / profile helpers if set, falling back
    to localized wall-clock hour and minute thresholds.
    """
    # 1. Explicit state checks
    if home_state == "Sleep" or (hvac_profile and "Sleep" in hvac_profile and "Pre-Sleep" not in hvac_profile):
        return PHASE_OVERNIGHT, True, False
    elif home_state == "Pre-Sleep" or (hvac_profile and "Pre-Sleep" in hvac_profile):
        return PHASE_EVENING, False, True

    # 2. Time-of-day calculation fallback
    now_time = now.time()
    is_bedtime = False
    is_evening = False

    # Evening phase (between child bedtime and parent bedtime)
    if bedtime_start > parent_bedtime:
        if now_time >= bedtime_start or now_time < parent_bedtime:
            is_evening = True
    else:
        if bedtime_start <= now_time < parent_bedtime:
            is_evening = True

    # Bedtime phase (between parent bedtime and morning wakeup)
    if parent_bedtime > morning_start:
        if now_time >= parent_bedtime or now_time < morning_start:
            is_bedtime = True
    else:
        if parent_bedtime <= now_time < morning_start:
            is_bedtime = True

    if is_bedtime:
        return PHASE_OVERNIGHT, True, False
    elif is_evening:
        return PHASE_EVENING, False, True

    return PHASE_DAYTIME, False, False


def resolve_zone_setpoints(
    zone_id: str,
    is_bedtime: bool,
    is_evening: bool,
    climate_state: str,
    climate_attrs: dict[str, Any],
    eco_active: bool,
    preset_home_cool: float,
    preset_home_heat: float,
    preset_sleep_cool: float | None = None,
    preset_sleep_heat: float | None = None,
    protect_cool: float | None = None,
    protect_heat: float | None = None,
) -> tuple[float, float]:
    """Resolves active heating and cooling setpoints using the Dual-Source Setpoint Hierarchy.

    Avoids the eco-setback circular dependency by trusting the thermostat only
    when normal comfort mode is active, falling back to phase-aware presets when
    eco-mode or HVAC is off, and applying hard protection clamping.

    Returns: (active_heat_sp, active_cool_sp)
    """
    preset_mode = climate_attrs.get("preset_mode", "none")
    trust_thermostat = (
        climate_state in ["heat", "cool", "auto", "heat_cool"]
        and not eco_active
        and preset_mode != "eco"
    )

    base_cool = preset_home_cool
    base_heat = preset_home_heat

    if trust_thermostat:
        if climate_state == "heat":
            act_heat = float(climate_attrs.get("temperature") or climate_attrs.get("target_temp_low") or base_heat)
            act_cool = base_cool
        elif climate_state == "cool":
            act_heat = base_heat
            act_cool = float(climate_attrs.get("temperature") or climate_attrs.get("target_temp_high") or base_cool)
        else:
            act_heat = float(climate_attrs.get("target_temp_low") or base_heat)
            act_cool = float(climate_attrs.get("target_temp_high") or base_cool)
    else:
        if is_bedtime or is_evening:
            act_heat = preset_sleep_heat if preset_sleep_heat is not None else base_heat
            act_cool = preset_sleep_cool if preset_sleep_cool is not None else base_cool
        else:
            act_heat = base_heat
            act_cool = base_cool

    # Hard protection clamping
    if protect_cool is not None:
        act_cool = min(act_cool, protect_cool)
    if protect_heat is not None:
        act_heat = max(act_heat, protect_heat)

    return act_heat, act_cool
