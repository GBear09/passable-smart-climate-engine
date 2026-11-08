"""Recommendation latch state machine for Passable Smart Climate Engine.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

OPEN = "Open Windows"
CLOSE = "Close Windows"

SAFETY_SCENARIOS = frozenset({
    "hazard_vetoed",
    "telemetry_degraded",
    "overcooling",
    "overheating",
    "comfort_recovery",
    "block_outside_heat",
    "fall_trap_heat",
    "veto_temp_rise",
    "veto_temp_drop",
    "weather_unstable",
    "latent_breach",
})


@dataclass
class RecommendationLatch:
    """State machine governing window recommendation anti-flapping and dwell timers."""

    state: str = CLOSE
    since: datetime.datetime | None = None
    last_scenario: str = "init"

    def step(
        self,
        raw_state: str,
        now: datetime.datetime,
        *,
        min_open: datetime.timedelta,
        min_closed: datetime.timedelta,
        safety_close: bool = False,
        scenario: str = "standard",
    ) -> tuple[str, bool]:
        """Evaluates state transition with two-sided hysteresis and safety preemption.
        Returns: (final_state, held_by_dwell)
        """
        if self.since is None:
            self.state = raw_state
            self.since = now
            self.last_scenario = scenario
            return raw_state, False

        # If arriving home after being away, bypass the closed-dwell latch so
        # favorable outdoor air is immediately recommended upon arrival.
        if (
            self.state == CLOSE
            and raw_state == OPEN
            and "unoccupied" in self.last_scenario
        ):
            self.state = OPEN
            self.since = now
            self.last_scenario = scenario
            return OPEN, False

        if raw_state == self.state:
            self.last_scenario = scenario
            return self.state, False

        # Safety-critical reasons immediately override an active Open dwell timer
        if raw_state == CLOSE and safety_close:
            self.state = CLOSE
            self.since = now
            self.last_scenario = scenario
            return CLOSE, False

        dwell = min_open if self.state == OPEN else min_closed
        if (now - self.since) < dwell:
            return self.state, True

        self.state = raw_state
        self.since = now
        self.last_scenario = scenario
        return raw_state, False

    def as_dict(self) -> dict[str, str | None]:
        """Serializes latch state for persistence."""
        return {
            "state": self.state,
            "since": self.since.isoformat() if self.since else None,
            "last_scenario": self.last_scenario,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str | None] | None) -> RecommendationLatch:
        """Restores latch state from dictionary."""
        if not data:
            return cls()
        since_str = data.get("since")
        since_dt = None
        if since_str:
            try:
                since_dt = datetime.datetime.fromisoformat(since_str)
            except Exception:
                since_dt = None
        return cls(
            state=data.get("state", CLOSE),
            since=since_dt,
            last_scenario=data.get("last_scenario", "init"),
        )


@dataclass
class _ContactSensorState:
    """Internal per-contact-sensor debounce and availability tracking state."""

    last_raw_state: str = "unknown"
    confirmed_open: bool = False
    open_since: datetime.datetime | None = None
    unavailable_since: datetime.datetime | None = None
    from_unavailable: bool = False
    initialized: bool = False


class WindowContactTracker:
    """Tracks per-sensor window/door binary contact states with open debounce
    and Zigbee/Z-Wave reconnect-blip immunity.
    """

    def __init__(self) -> None:
        self._contacts: dict[str, _ContactSensorState] = {}

    def evaluate_sensor(
        self,
        entity_id: str,
        raw_state: str | None,
        now: datetime.datetime,
        debounce_seconds: float = 120.0,
        reconnect_grace_seconds: float = 60.0,
    ) -> tuple[bool, bool, bool]:
        """Evaluate a single binary contact sensor.
        Returns: (confirmed_open, is_pending_debounce, is_unavailable)
        """
        state_str = (raw_state or "unavailable").strip().lower()
        is_unavail = state_str in ("unavailable", "unknown", "none", "")

        entry = self._contacts.get(entity_id)
        if entry is None:
            entry = _ContactSensorState()
            self._contacts[entity_id] = entry

        if not entry.initialized:
            entry.initialized = True
            entry.last_raw_state = state_str
            if state_str == "on":
                entry.confirmed_open = True
                entry.open_since = now
                return True, False, False
            if is_unavail:
                entry.confirmed_open = False
                entry.unavailable_since = now
                entry.from_unavailable = True
                return False, False, True
            entry.confirmed_open = False
            return False, False, False

        if is_unavail:
            if entry.unavailable_since is None:
                entry.unavailable_since = now
            entry.from_unavailable = True
            entry.last_raw_state = state_str
            # Hold last known confirmed_open state when a single mesh contact drops offline
            return entry.confirmed_open, False, True

        if state_str == "off":
            entry.confirmed_open = False
            entry.open_since = None
            entry.unavailable_since = None
            entry.from_unavailable = False
            entry.last_raw_state = "off"
            return False, False, False

        if state_str == "on":
            entry.unavailable_since = None
            if entry.confirmed_open:
                entry.last_raw_state = "on"
                return True, False, False

            if entry.open_since is None:
                entry.open_since = now
                if entry.last_raw_state in ("unavailable", "unknown", "none", ""):
                    entry.from_unavailable = True

            entry.last_raw_state = "on"
            req_sec = (
                max(float(debounce_seconds), float(reconnect_grace_seconds))
                if entry.from_unavailable
                else float(debounce_seconds)
            )
            elapsed = (now - entry.open_since).total_seconds()
            if elapsed >= req_sec:
                entry.confirmed_open = True
                entry.from_unavailable = False
                return True, False, False
            return False, True, False

        # Any unexpected non-open state is treated as closed
        entry.confirmed_open = False
        entry.open_since = None
        entry.last_raw_state = state_str
        return False, False, False

    def evaluate_zone_contacts(
        self,
        entity_ids: list[str],
        raw_states: dict[str, str | None],
        now: datetime.datetime,
        debounce_seconds: float = 120.0,
        reconnect_grace_seconds: float = 60.0,
    ) -> dict[str, Any]:
        """Evaluate all window/door contact sensors for a zone."""
        confirmed_open_sensors: list[str] = []
        pending_debounce_sensors: list[str] = []
        unavailable_sensors: list[str] = []
        raw_open_count = 0

        for ent_id in entity_ids:
            raw_val = raw_states.get(ent_id)
            if (raw_val or "").strip().lower() == "on":
                raw_open_count += 1
            confirmed, pending, unavail = self.evaluate_sensor(
                ent_id,
                raw_val,
                now,
                debounce_seconds=debounce_seconds,
                reconnect_grace_seconds=reconnect_grace_seconds,
            )
            if confirmed:
                confirmed_open_sensors.append(ent_id)
            if pending:
                pending_debounce_sensors.append(ent_id)
            if unavail:
                unavailable_sensors.append(ent_id)

        return {
            "confirmed_count": len(confirmed_open_sensors),
            "raw_open_count": raw_open_count,
            "confirmed_open_sensors": confirmed_open_sensors,
            "pending_debounce_sensors": pending_debounce_sensors,
            "unavailable_sensors": unavailable_sensors,
        }


@dataclass
class DailyComplianceRecord:
    """Daily audit record of window recommendations vs. user action and comfort."""

    date: str
    open_recommended_at: str | None = None
    open_complied_at: str | None = None
    close_recommended_at: str | None = None
    close_complied_at: str | None = None
    peak_open_windows_fraction_up: float = 0.0
    peak_open_windows_fraction_dn: float = 0.0
    comfort_violation_minutes_up: float = 0.0
    comfort_violation_minutes_dn: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "open_recommended_at": self.open_recommended_at,
            "open_complied_at": self.open_complied_at,
            "close_recommended_at": self.close_recommended_at,
            "close_complied_at": self.close_complied_at,
            "peak_open_windows_fraction_up": round(self.peak_open_windows_fraction_up, 3),
            "peak_open_windows_fraction_dn": round(self.peak_open_windows_fraction_dn, 3),
            "comfort_violation_minutes_up": round(self.comfort_violation_minutes_up, 1),
            "comfort_violation_minutes_dn": round(self.comfort_violation_minutes_dn, 1),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyComplianceRecord:
        return cls(
            date=str(data.get("date", "")),
            open_recommended_at=data.get("open_recommended_at"),
            open_complied_at=data.get("open_complied_at"),
            close_recommended_at=data.get("close_recommended_at"),
            close_complied_at=data.get("close_complied_at"),
            peak_open_windows_fraction_up=float(data.get("peak_open_windows_fraction_up", 0.0)),
            peak_open_windows_fraction_dn=float(data.get("peak_open_windows_fraction_dn", 0.0)),
            comfort_violation_minutes_up=float(data.get("comfort_violation_minutes_up", 0.0)),
            comfort_violation_minutes_dn=float(data.get("comfort_violation_minutes_dn", 0.0)),
        )


@dataclass
class ComplianceLedger:
    """Rolling 30-day ledger of window recommendation compliance and open-window fraction."""

    records: dict[str, DailyComplianceRecord] = field(default_factory=dict)
    max_days: int = 30

    def record_step(
        self,
        now: datetime.datetime,
        recommendation: str,
        up_open_count: int,
        up_total_sensors: int,
        dn_open_count: int,
        dn_total_sensors: int,
        up_in_comfort: bool = True,
        dn_in_comfort: bool = True,
        dt_minutes: float = 5.0,
    ) -> None:
        """Update today's compliance record with current telemetry."""
        date_key = now.strftime("%Y-%m-%d")
        rec = self.records.get(date_key)
        if rec is None:
            rec = DailyComplianceRecord(date=date_key)
            self.records[date_key] = rec
            self._prune()

        up_frac = (
            min(1.0, max(0.0, up_open_count / max(1, up_total_sensors)))
            if up_total_sensors > 0
            else (1.0 if up_open_count > 0 else 0.0)
        )
        dn_frac = (
            min(1.0, max(0.0, dn_open_count / max(1, dn_total_sensors)))
            if dn_total_sensors > 0
            else (1.0 if dn_open_count > 0 else 0.0)
        )
        rec.peak_open_windows_fraction_up = max(rec.peak_open_windows_fraction_up, up_frac)
        rec.peak_open_windows_fraction_dn = max(rec.peak_open_windows_fraction_dn, dn_frac)

        any_open = (up_open_count + dn_open_count) > 0
        now_iso = now.isoformat()

        if recommendation == OPEN:
            if rec.open_recommended_at is None:
                rec.open_recommended_at = now_iso
            if any_open and rec.open_complied_at is None:
                rec.open_complied_at = now_iso
        else:
            if any_open and rec.open_complied_at is not None and rec.close_recommended_at is None:
                rec.close_recommended_at = now_iso
            elif not any_open and rec.close_recommended_at is not None and rec.close_complied_at is None:
                rec.close_complied_at = now_iso

        if not up_in_comfort:
            rec.comfort_violation_minutes_up = round(
                rec.comfort_violation_minutes_up + max(0.0, float(dt_minutes)), 1
            )
        if not dn_in_comfort:
            rec.comfort_violation_minutes_dn = round(
                rec.comfort_violation_minutes_dn + max(0.0, float(dt_minutes)), 1
            )

    def _prune(self) -> None:
        if len(self.records) <= self.max_days:
            return
        sorted_keys = sorted(self.records.keys())
        for k in sorted_keys[: len(self.records) - self.max_days]:
            self.records.pop(k, None)

    def get_summary(self, lookback_days: int = 14) -> dict[str, Any]:
        """Compute rolling compliance statistics and learned open-window fraction."""
        sorted_keys = sorted(self.records.keys())[-lookback_days:]
        recent = [self.records[k] for k in sorted_keys]

        recommended_days = 0
        complied_days = 0
        open_lags: list[float] = []
        close_lags: list[float] = []
        peak_fractions: list[float] = []

        for r in recent:
            if r.open_recommended_at is not None:
                recommended_days += 1
                if r.open_complied_at is not None:
                    complied_days += 1
                    try:
                        t_rec = datetime.datetime.fromisoformat(r.open_recommended_at)
                        t_cmp = datetime.datetime.fromisoformat(r.open_complied_at)
                        lag_m = max(0.0, (t_cmp - t_rec).total_seconds() / 60.0)
                        open_lags.append(lag_m)
                    except Exception:
                        pass

            if r.close_recommended_at is not None and r.close_complied_at is not None:
                try:
                    t_crec = datetime.datetime.fromisoformat(r.close_recommended_at)
                    t_ccmp = datetime.datetime.fromisoformat(r.close_complied_at)
                    clag_m = max(0.0, (t_ccmp - t_crec).total_seconds() / 60.0)
                    close_lags.append(clag_m)
                except Exception:
                    pass

            day_peak = max(r.peak_open_windows_fraction_up, r.peak_open_windows_fraction_dn)
            if day_peak > 0.0:
                peak_fractions.append(day_peak)

        follow_rate_pct = (
            round(100.0 * complied_days / recommended_days, 1)
            if recommended_days > 0
            else None
        )
        avg_open_lag = (
            round(sum(open_lags) / len(open_lags), 1) if open_lags else None
        )
        avg_close_lag = (
            round(sum(close_lags) / len(close_lags), 1) if close_lags else None
        )
        learned_fraction = (
            round(max(0.25, min(1.0, sum(peak_fractions) / len(peak_fractions))), 2)
            if peak_fractions
            else 1.0
        )

        if recommended_days > 0 and follow_rate_pct is not None:
            summary = f"Window advice followed {complied_days}/{recommended_days} days ({follow_rate_pct:.0f}%)"
            if avg_open_lag is not None:
                summary += f" • Avg response {avg_open_lag:.0f}m"
        else:
            summary = f"No open-window recommendations in the last {lookback_days} days"

        return {
            "compliance_summary": summary,
            "recommended_days": recommended_days,
            "complied_days": complied_days,
            "follow_rate_pct": follow_rate_pct,
            "avg_open_lag_minutes": avg_open_lag,
            "avg_close_lag_minutes": avg_close_lag,
            "learned_open_windows_fraction": learned_fraction,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": {k: v.as_dict() for k, v in self.records.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ComplianceLedger:
        if not data or not isinstance(data, dict):
            return cls()
        raw_recs = data.get("records", {})
        parsed: dict[str, DailyComplianceRecord] = {}
        if isinstance(raw_recs, dict):
            for k, v in raw_recs.items():
                if isinstance(v, dict):
                    parsed[str(k)] = DailyComplianceRecord.from_dict(v)
        ledger = cls(records=parsed)
        ledger._prune()
        return ledger
