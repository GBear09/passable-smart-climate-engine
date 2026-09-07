"""Model training coordinator for Passable Smart Climate Engine.
Executes batch InfluxDB regressions and builds in-memory RateModel instances.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from ..core.models import RateModel
from .influx_client import InfluxClient
from .regression import multiple_linear_regression, ransac_linear_fit

_LOGGER = logging.getLogger(__name__)

TEMP_CONFIGS = [
    {"key": "downstairs_temp_profile_win_closed", "entity": "downstairs_heat_loss_gain_windows_closed", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "downstairs_temp_profile_win_open", "entity": "downstairs_heat_loss_gain_windows_open", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "upstairs_temp_profile_win_closed", "entity": "upstairs_heat_loss_gain_windows_closed", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "upstairs_temp_profile_win_open", "entity": "upstairs_heat_loss_gain_windows_open", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "upstairs_temp_profile_hvac_heat", "entity": "hvac_heating_rate_upstairs", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "upstairs_temp_profile_hvac_cool", "entity": "hvac_cooling_rate_upstairs", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "downstairs_temp_profile_hvac_heat", "entity": "hvac_heating_rate_downstairs", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
    {"key": "downstairs_temp_profile_hvac_cool", "entity": "hvac_cooling_rate_downstairs", "unit": "°F/h", "out_field": "outside_temperature", "in_field": "inside_temperature"},
]

HUMIDITY_CONFIGS = [
    {"key": "downstairs_humidity_profile_hvac_cool", "entity": "humidity_rate_cooling_downstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "upstairs_humidity_profile_hvac_cool", "entity": "humidity_rate_cooling_upstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "downstairs_humidity_profile_hvac_heat", "entity": "humidity_rate_heating_downstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "upstairs_humidity_profile_hvac_heat", "entity": "humidity_rate_heating_upstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "downstairs_humidity_profile_win_closed", "entity": "humidity_rate_windows_closed_downstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "upstairs_humidity_profile_win_closed", "entity": "humidity_rate_windows_closed_upstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "downstairs_humidity_profile_win_open", "entity": "humidity_rate_windows_open_downstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
    {"key": "upstairs_humidity_profile_win_open", "entity": "humidity_rate_windows_open_upstairs", "unit": "g/m³/h", "out_field": "outside_ah", "in_field": "inside_ah"},
]

def train_all_models_sync(
    client: InfluxClient,
    lookback_days: int = 365,
) -> dict[str, RateModel]:
    """Synchronous training worker for all 16 plot models.
    Executed in Home Assistant's thread-pool executor.
    """
    _LOGGER.info("Passable Smart Climate Engine: Commencing batch model training (%dd lookback)...", lookback_days)
    time_range_str = f"{lookback_days}d"
    results: dict[str, RateModel] = {}
    now_iso = datetime.datetime.now().isoformat()

    all_configs = [(c, False) for c in TEMP_CONFIGS] + [(c, True) for c in HUMIDITY_CONFIGS]

    for cfg, is_hum in all_configs:
        key = cfg["key"]
        entity_id = cfg["entity"]
        unit = cfg["unit"]
        out_f = cfg["out_field"]
        in_f = cfg["in_field"]
        bin_size = 1.0 if is_hum else 0.5
        threshold = 2.0 if is_hum else 0.5

        # 1. 2D RANSAC scatter points
        unique_deltas, avg_values, count_raw = client.fetch_and_bin_profile_data(
            entity_id=entity_id,
            measurement_name=unit,
            primary_field="value",
            out_field=out_f,
            in_field=in_f,
            time_range_str=time_range_str,
            bin_size=bin_size,
        )

        model = RateModel(model_type="ransac", last_updated=now_iso)

        if count_raw > 0 and len(unique_deltas) >= 10:
            ransac_pts = [[x, y] for x, y in zip(unique_deltas, avg_values)]
            fit_res, inliers_mask = ransac_linear_fit(ransac_pts, threshold=threshold)

            if fit_res and inliers_mask:
                slope, intercept = fit_res
                inlier_x = [x for i, x in enumerate(unique_deltas) if inliers_mask[i]]
                inlier_y = [y for i, y in enumerate(avg_values) if inliers_mask[i]]

                model.slope = round(slope, 4)
                model.intercept = round(intercept, 4)
                model.equation = f"y = {slope:.3f}x {intercept:+.3f}"
                model.x_data = inlier_x
                model.y_data = inlier_y
                if inlier_x:
                    min_x, max_x = min(inlier_x), max(inlier_x)
                    model.fit_x = [float(min_x), float(max_x)]
                    model.fit_y = [float(slope * min_x + intercept), float(slope * max_x + intercept)]
            else:
                model.x_data = unique_deltas
                model.y_data = avg_values
        else:
            model.x_data = unique_deltas
            model.y_data = avg_values

        # 2. 6D Ridge Multiple Linear Regression
        mlr_data, uses_true_solcast = client.fetch_mlr_data(
            entity_id=entity_id,
            measurement_name=unit,
            time_range_str=time_range_str,
            out_field=out_f,
            in_field=in_f,
        )

        if mlr_data and len(mlr_data) >= 300:
            coeffs, mlr_intercept, std_coeffs = multiple_linear_regression(mlr_data)
            if coeffs and mlr_intercept is not None:
                c_dt, c_wd, c_c, c_se, c_ss = coeffs
                # Sanity check validation bounds:
                max_irr = 1000.0 if uses_true_solcast else 90.0
                is_sane = (
                    abs(c_dt * 50.0) < 15.0
                    and abs(c_wd * 2000.0) < 15.0
                    and abs(c_c * 100.0) < 10.0
                    and abs(c_se * max_irr) < 15.0
                    and abs(c_ss * max_irr) < 15.0
                )

                if is_sane:
                    model.model_type = "mlr"
                    model.c_delta_t = round(c_dt, 4)
                    model.c_wind_draft = round(c_wd, 4)
                    model.c_clouds = round(c_c, 4)
                    model.c_irradiance = 0.0
                    model.c_solar_east = round(c_se, 4)
                    model.c_solar_south = round(c_ss, 4)
                    model.mlr_intercept = round(mlr_intercept, 4)
                    model.mlr_data_points = len(mlr_data)
                    model.uses_true_solcast = uses_true_solcast

                    if std_coeffs:
                        nc_dt, nc_wd, nc_c, nc_se, nc_ss = std_coeffs
                        model.norm_c_delta_t = round(nc_dt, 4)
                        model.norm_c_wind_draft = round(nc_wd, 4)
                        model.norm_c_clouds = round(nc_c, 4)
                        model.norm_c_irradiance = 0.0
                        model.norm_c_solar_east = round(nc_se, 4)
                        model.norm_c_solar_south = round(nc_ss, 4)

        results[key] = model

    _LOGGER.info("Passable Smart Climate Engine: Successfully trained all %d models.", len(results))
    return results
