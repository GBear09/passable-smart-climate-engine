"""Recommendation latch state machine for Passable Smart Climate Engine.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

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
