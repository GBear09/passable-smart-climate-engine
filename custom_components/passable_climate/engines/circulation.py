"""Convective HVAC circulation manager for attic and basement thermal redistribution.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
import logging
import math
import time
from typing import Any

from ..core.models import RateModel

_LOGGER = logging.getLogger(__name__)

class CirculationManager:
    """Manages convective circulation decisions and stall detection feedback."""

    def __init__(self) -> None:
        self.state_memory: dict[str, dict[str, Any]] = {
            "upstairs": {"start_time": None, "start_temp": None, "lockout_until": None},
            "downstairs": {"start_time": None, "start_temp": None, "lockout_until": None},
        }
        self.learning_outcomes: dict[str, list[dict[str, Any]]] = {
            "upstairs": [],
            "downstairs": [],
        }

    def load_learning_data(self, data: dict[str, Any]) -> None:
        """Loads historical learning data and state memory from storage dictionary."""
        if not data:
            return
        saved_memory = data.get("state_memory", {})
        for z_key in ["upstairs", "downstairs"]:
            if z_key in saved_memory:
                mem = saved_memory[z_key]
                st = mem.get("start_time")
                lo = mem.get("lockout_until")
                self.state_memory[z_key] = {
                    "start_time": datetime.datetime.fromisoformat(st) if st else None,
                    "start_temp": mem.get("start_temp"),
                    "lockout_until": datetime.datetime.fromisoformat(lo) if lo else None,
                }
        saved_outcomes = data.get("circulation_outcomes", {})
        for z_key in ["upstairs", "downstairs"]:
            if z_key in saved_outcomes:
                self.learning_outcomes[z_key] = saved_outcomes[z_key]

    def export_learning_data(self) -> dict[str, Any]:
        """Exports learning data and state memory to a serializable dictionary."""
        serialized_memory: dict[str, Any] = {}
        for z_key, mem in self.state_memory.items():
            st = mem.get("start_time")
            lo = mem.get("lockout_until")
            serialized_memory[z_key] = {
                "start_time": st.isoformat() if st else None,
                "start_temp": mem.get("start_temp"),
                "lockout_until": lo.isoformat() if lo else None,
            }
        return {
            "circulation_outcomes": self.learning_outcomes,
            "state_memory": serialized_memory,
        }

    def predict_circulation_success(
        self,
        z_key: str,
        needs_cooling: bool,
        out_temp: float,
        area_temp: float,
        source_temp: float,
    ) -> bool:
        """Calculates success probability based on historical records with exponential distance decay."""
        history = self.learning_outcomes.get(z_key, [])
        if len(history) < 5:
            return True

        total_w = 0.0
        weighted_success = 0.0
        now_ts = time.time()

        for record in history:
            if record.get("needs_cooling") != needs_cooling:
                continue
            c = record.get("conditions", {})

            diff_out = abs(c.get("out_temp", out_temp) - out_temp)
            diff_area = abs(c.get("area_temp", area_temp) - area_temp)
            diff_source = abs(c.get("source_temp", source_temp) - source_temp)

            dist = diff_out + diff_area + (diff_source * 2.0)
            w = math.exp(-dist / 5.0)

            age_days = (now_ts - record.get("timestamp", now_ts)) / 86400.0
            w *= math.exp(-age_days / 30.0)

            if record.get("success"):
                weighted_success += w
            total_w += w

        if total_w < 0.1:
            return True

        probability = weighted_success / total_w
        return probability > 0.35

    def record_outcome(
        self,
        z_key: str,
        needs_cooling: bool,
        success: bool,
        perf_vs_expected: float,
        out_temp: float,
        area_temp: float,
        source_temp: float,
    ) -> None:
        """Records circulation run outcome."""
        if z_key not in self.learning_outcomes:
            self.learning_outcomes[z_key] = []

        entry = {
            "timestamp": time.time(),
            "needs_cooling": needs_cooling,
            "success": success,
            "perf_vs_expected": perf_vs_expected,
            "conditions": {
                "out_temp": out_temp,
                "area_temp": area_temp,
                "source_temp": source_temp,
            },
        }
        self.learning_outcomes[z_key].append(entry)
        if len(self.learning_outcomes[z_key]) > 100:
            self.learning_outcomes[z_key].pop(0)

    def evaluate_circulation(
        self,
        z_key: str,
        area_temp: float,
        source_temp: float,
        target_cool: float,
        target_heat: float,
        hvac_mode: str,
        seasonal_mode: str,
        windows_are_open: bool,
        out_temp: float,
        model: RateModel | None,
        now_utc: datetime.datetime,
        is_active: bool,
        delta_threshold: float = 4.0,
        min_runtime_minutes: int = 15,
        max_runtime_minutes: int = 120,
        stall_margin: float = 0.2,
        lockout_hours: int = 1,
    ) -> tuple[str, int]:
        """Evaluates circulation action.
        Returns: (action: 'turn_on' | 'turn_off' | 'hold', lockout_hours: int)
        """
        if windows_are_open or hvac_mode == "off":
            if is_active:
                return "turn_off", 0
            return "hold", 0

        deadband = 0.5 if is_active else 0.0
        needs_cooling = (hvac_mode == "cool") and (area_temp > (target_cool - deadband))
        needs_heating = (hvac_mode == "heat") and (area_temp < (target_heat + deadband))

        if seasonal_mode == "fall_transition":
            needs_cooling = False
        elif seasonal_mode in ["spring_transition", "heatwave_prep"]:
            needs_heating = False

        if is_active:
            mem = self.state_memory[z_key]
            start_time = mem.get("start_time") or now_utc
            start_temp = mem.get("start_temp") or area_temp

            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=datetime.timezone.utc)

            minutes_running = (now_utc - start_time).total_seconds() / 60.0

            # 1. Runtime Cap
            if minutes_running >= max_runtime_minutes:
                return "turn_off", lockout_hours

            # 2. Anti-short-cycle
            if minutes_running < min_runtime_minutes:
                return "hold", 0

            # 3. Stall detection
            if minutes_running >= 20.0 and model:
                expected_rate = model.calculate_rate(out_temp - area_temp, action="closed")
                expected_rate = max(-3.0, min(3.0, expected_rate))
                expected_change = expected_rate * (minutes_running / 60.0)
                actual_change = area_temp - start_temp
                perf_vs_expected = actual_change - expected_change

                is_stalled = False
                if needs_cooling:
                    if perf_vs_expected > -stall_margin or actual_change > stall_margin:
                        is_stalled = True
                elif needs_heating:
                    if perf_vs_expected < stall_margin or actual_change < -stall_margin:
                        is_stalled = True

                if is_stalled:
                    self.record_outcome(z_key, needs_cooling, False, perf_vs_expected, out_temp, area_temp, source_temp)
                    return "turn_off", lockout_hours

            # 4. Normal shutoff
            if not needs_cooling and not needs_heating:
                if minutes_running >= min_runtime_minutes and model:
                    actual_change = area_temp - start_temp
                    self.record_outcome(z_key, needs_cooling, True, actual_change, out_temp, area_temp, source_temp)
                return "turn_off", 0

            # 5. Insufficient source delta
            if abs(source_temp - area_temp) < 2.0:
                return "turn_off", 2

            return "hold", 0

        else:
            # Check lockout
            lockout = self.state_memory[z_key].get("lockout_until")
            if lockout:
                if lockout.tzinfo is None:
                    lockout = lockout.replace(tzinfo=datetime.timezone.utc)
                if now_utc < lockout:
                    return "hold", 0

            source_cooler = source_temp < area_temp
            source_warmer = source_temp > area_temp

            if needs_cooling and source_cooler and (area_temp - source_temp) >= delta_threshold:
                if self.predict_circulation_success(z_key, True, out_temp, area_temp, source_temp):
                    return "turn_on", 0

            elif needs_heating and source_warmer and (source_temp - area_temp) >= delta_threshold:
                if self.predict_circulation_success(z_key, False, out_temp, area_temp, source_temp):
                    return "turn_on", 0

            return "hold", 0
