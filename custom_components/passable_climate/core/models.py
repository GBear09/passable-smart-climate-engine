"""Data structures and representations for Passable Smart Climate Engine.
Zero Home Assistant dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
from typing import Any, Literal

@dataclass
class RateModel:
    """Represents a trained thermal or moisture rate model (RANSAC or MLR)."""
    model_type: Literal["ransac", "mlr"] = "ransac"
    slope: float | None = None
    intercept: float | None = None
    equation: str | None = None
    fit_x: list[float] = field(default_factory=list)
    fit_y: list[float] = field(default_factory=list)
    x_data: list[float] = field(default_factory=list)
    y_data: list[float] = field(default_factory=list)

    # MLR Specific Coefficients
    c_delta_t: float | None = None
    c_wind_draft: float | None = None
    c_clouds: float | None = None
    c_irradiance: float | None = None
    c_solar_east: float | None = None
    c_solar_south: float | None = None
    mlr_intercept: float | None = None
    mlr_data_points: int = 0
    uses_true_solcast: bool = True

    # Normalized Coefficients (For Radar Charts)
    norm_c_delta_t: float | None = None
    norm_c_wind_draft: float | None = None
    norm_c_clouds: float | None = None
    norm_c_irradiance: float | None = None
    norm_c_solar_east: float | None = None
    norm_c_solar_south: float | None = None

    last_updated: str = ""

    def calculate_rate(
        self,
        delta: float,
        wind_speed: float = 0.0,
        cloud_coverage: float = 50.0,
        elevation: float = 0.0,
        az_sin: float = 0.0,
        az_cos: float = 0.0,
        action: str = "closed",
        solcast_irradiance: float = 0.0,
        is_humidity: bool = False,
    ) -> float:
        """Calculates expected rate of change (°F/h or g/m³/h) given current conditions."""
        if self.model_type == "mlr" and self.c_delta_t is not None and self.mlr_intercept is not None:
            wind_draft = wind_speed * delta
            irradiance = (
                solcast_irradiance
                if self.uses_true_solcast
                else (elevation * ((100.0 - cloud_coverage) / 100.0) if elevation > 0 else 0.0)
            )
            solar_east = irradiance * az_sin if irradiance > 0 else 0.0
            solar_south = irradiance * az_cos if irradiance > 0 else 0.0

            rate = (
                (self.c_delta_t * delta)
                + ((self.c_wind_draft or 0.0) * wind_draft)
                + ((self.c_clouds or 0.0) * cloud_coverage)
                + ((self.c_irradiance or 0.0) * irradiance)
                + ((self.c_solar_east or 0.0) * solar_east)
                + ((self.c_solar_south or 0.0) * solar_south)
                + self.mlr_intercept
            )
            return rate

        elif self.slope is not None and self.intercept is not None:
            rate = (self.slope * delta) + self.intercept
            if action == "open":
                rate *= (1.0 + (wind_speed * 0.05))
            elif action == "closed" and delta > 0 and not is_humidity:
                if elevation > 0:
                    solar_gain = ((100.0 - cloud_coverage) / 100.0) * 0.2 * (elevation / 90.0)
                    rate += solar_gain
            return rate

        # Fallback default
        default_rate = 0.3 if action == "open" else 0.05
        return default_rate * delta

@dataclass
class ZoneThermalState:
    """Current live thermal snapshot of a zone."""
    zone_id: str
    name: str
    temperature: float = 72.0
    humidity: float = 50.0
    active_heat_sp: float = 68.0
    active_cool_sp: float = 75.0
    hvac_mode: str = "off"
    hvac_action: str = "idle"
    is_currently_open: bool = False
    models: dict[str, RateModel] = field(default_factory=dict)
    circulation_source_temp: float | None = None

@dataclass
class SimulationAction:
    """Discrete action period (e.g. open for 3 hours)."""
    action: Literal["open", "closed"]
    hours: int

@dataclass
class ZonePlanResult:
    """Outcome of a forward micro-step simulation and decision tree."""
    recommended_state: Literal["Open Windows", "Close Windows"]
    details_message: str
    history: list[dict[str, float]]
    actions: list[SimulationAction]
    eco_mode_requested: bool
    comfort_recovery_triggered: bool = False
