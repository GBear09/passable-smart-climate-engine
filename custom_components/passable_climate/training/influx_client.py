"""InfluxDB v1 and v2 client for historical rate queries and model data extraction."""

from __future__ import annotations

import datetime
import logging
import math
from typing import Any

import numpy as np
import requests

_LOGGER = logging.getLogger(__name__)

class InfluxClient:
    """Client for InfluxDB v1 and v2 historical data queries."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8086,
        version: str = "1",
        database: str = "homeassistant",
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        org: str | None = None,
        bucket: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.version = str(version)
        self.database = database
        self.username = username
        self.password = password
        self.token = token
        self.org = org
        self.bucket = bucket or f"{database}/autogen"

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def test_connection(self) -> tuple[bool, str]:
        """Tests the InfluxDB connection and credentials. Returns (success, message)."""
        try:
            if self.version == "1":
                auth = (self.username, self.password) if self.username and self.password else None
                resp = requests.get(
                    f"{self.base_url}/query",
                    params={"db": self.database, "q": "SHOW MEASUREMENTS LIMIT 1"},
                    auth=auth,
                    timeout=10,
                )
                if resp.status_code == 401:
                    return False, "InfluxDB v1 authentication failed: Invalid username or password."
                resp.raise_for_status()
                return True, "InfluxDB v1 connection successful."
            else:
                headers = {"Authorization": f"Token {self.token}"}
                resp = requests.get(
                    f"{self.base_url}/api/v2/buckets?org={self.org}",
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code == 401:
                    return False, "InfluxDB v2 authentication failed: Invalid API Token."
                resp.raise_for_status()
                return True, "InfluxDB v2 connection successful."
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to InfluxDB at {self.base_url}. Verify host and port."
        except Exception as err:
            return False, f"InfluxDB error: {err}"

    def fetch_and_bin_profile_data(
        self,
        entity_id: str,
        measurement_name: str,
        primary_field: str,
        out_field: str,
        in_field: str,
        time_range_str: str,
        bin_size: float,
        alignment_window: str = "5m",
    ) -> tuple[list[float], list[float], int]:
        """Queries and bins historical rate data against temperature or humidity delta.
        Returns: (unique_deltas, avg_values, total_raw_count)
        """
        binned_data: dict[float, list[float]] = {}
        count = 0

        try:
            if self.version == "1":
                auth = (self.username, self.password) if self.username and self.password else None
                query_str = (
                    f'SELECT mean("{primary_field}") AS "p", mean("{out_field}") AS "o", mean("{in_field}") AS "i" '
                    f'FROM "{measurement_name}" '
                    f"WHERE entity_id = '{entity_id}' AND time > now() - {time_range_str} "
                    f"GROUP BY time({alignment_window})"
                )
                resp = requests.get(
                    f"{self.base_url}/query",
                    params={"db": self.database, "q": query_str, "epoch": "ns"},
                    auth=auth,
                    timeout=60,
                )
                resp.raise_for_status()
                data_json = resp.json()

                if "results" in data_json and data_json["results"] and "series" in data_json["results"][0]:
                    series = data_json["results"][0]["series"][0]
                    columns = series.get("columns", [])
                    try:
                        p_idx = columns.index("p")
                        o_idx = columns.index("o")
                        i_idx = columns.index("i")
                    except ValueError:
                        return [], [], 0

                    for point in series.get("values", []):
                        val_p, val_o, val_i = point[p_idx], point[o_idx], point[i_idx]
                        if val_p is not None and val_o is not None and val_i is not None:
                            delta = val_o - val_i
                            bin_center = round(delta / bin_size) * bin_size
                            if bin_center not in binned_data:
                                binned_data[bin_center] = []
                            binned_data[bin_center].append(float(val_p))
                            count += 1

            elif self.version == "2":
                headers = {
                    "Authorization": f"Token {self.token}",
                    "Accept": "application/csv",
                    "Content-Type": "application/vnd.flux",
                }
                flux_query = f"""
                    from(bucket: "{self.bucket}")
                    |> range(start: -{time_range_str})
                    |> filter(fn: (r) => r["_measurement"] == "{measurement_name}" and r["entity_id"] == "{entity_id}")
                    |> filter(fn: (r) => r["_field"] == "{primary_field}" or r["_field"] == "{out_field}" or r["_field"] == "{in_field}")
                    |> aggregateWindow(every: {alignment_window}, fn: mean, createEmpty: false)
                    |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
                """
                resp = requests.post(
                    f"{self.base_url}/api/v2/query?org={self.org}",
                    headers=headers,
                    data=flux_query,
                    timeout=60,
                )
                resp.raise_for_status()

                csv_lines = resp.text.splitlines()
                if len(csv_lines) > 1:
                    header_row = [h.strip() for h in csv_lines[0].split(",")]
                    try:
                        p_idx = header_row.index(primary_field)
                        o_idx = header_row.index(out_field)
                        i_idx = header_row.index(in_field)
                    except ValueError:
                        return [], [], 0

                    for row_str in csv_lines[1:]:
                        if not row_str.strip() or row_str.startswith("#"):
                            continue
                        row = [r.strip() for r in row_str.split(",")]
                        if (
                            len(row) > max(p_idx, o_idx, i_idx)
                            and row[p_idx]
                            and row[o_idx]
                            and row[i_idx]
                        ):
                            delta = float(row[o_idx]) - float(row[i_idx])
                            bin_center = round(delta / bin_size) * bin_size
                            if bin_center not in binned_data:
                                binned_data[bin_center] = []
                            binned_data[bin_center].append(float(row[p_idx]))
                            count += 1

            if count == 0:
                return [], [], 0

            unique_deltas = sorted(binned_data.keys())
            avg_values = [float(np.mean(binned_data[d])) for d in unique_deltas]
            return unique_deltas, avg_values, count

        except Exception as err:
            _LOGGER.error("Error in fetch_and_bin_profile_data for %s: %s", entity_id, err)
            return [], [], 0

    def fetch_mlr_data(
        self,
        entity_id: str,
        measurement_name: str,
        time_range_str: str,
        out_field: str,
        in_field: str,
    ) -> tuple[list[tuple[float, ...]], bool]:
        """Fetches multivariate dataset for Ridge MLR model training.
        Returns: (data_list, uses_true_solcast)
        """
        data_list: list[tuple[float, ...]] = []
        uses_true_solcast = False
        try:
            if self.version == "1":
                auth = (self.username, self.password) if self.username and self.password else None
                query_str = (
                    f"SELECT value, {out_field}, {in_field}, wind_speed, cloud_coverage, "
                    f"sun_elevation, sun_azimuth, solcast_power, actual_solar_power "
                    f'FROM "{measurement_name}" '
                    f"WHERE entity_id = '{entity_id}' AND time > now() - {time_range_str}"
                )
                resp = requests.get(
                    f"{self.base_url}/query",
                    params={"db": self.database, "q": query_str},
                    auth=auth,
                    timeout=60,
                )
                resp.raise_for_status()
                data_json = resp.json()

                if "results" in data_json and data_json["results"] and "series" in data_json["results"][0]:
                    series = data_json["results"][0]["series"][0]
                    columns = series.get("columns", [])
                    values = series.get("values", [])

                    try:
                        val_idx = columns.index("value")
                        out_idx = columns.index(out_field)
                        in_idx = columns.index(in_field)
                        wind_idx = columns.index("wind_speed")
                        cloud_idx = columns.index("cloud_coverage")
                        elev_idx = columns.index("sun_elevation")
                        az_idx = columns.index("sun_azimuth")
                        solcast_idx = columns.index("solcast_power") if "solcast_power" in columns else -1
                        actual_idx = columns.index("actual_solar_power") if "actual_solar_power" in columns else -1
                    except ValueError:
                        return [], False

                    actual_valid_count = sum(
                        1 for r in values if actual_idx >= 0 and len(r) > actual_idx and r[actual_idx] is not None
                    )
                    solcast_valid_count = sum(
                        1 for r in values if solcast_idx >= 0 and len(r) > solcast_idx and r[solcast_idx] is not None
                    )

                    uses_actual_solar = actual_valid_count >= 300
                    uses_true_solcast = uses_actual_solar or (solcast_valid_count >= 300)

                    for row in values:
                        if all(row[i] is not None for i in [val_idx, out_idx, in_idx, wind_idx, cloud_idx, elev_idx, az_idx]):
                            rate = float(row[val_idx])
                            delta_t = float(row[out_idx]) - float(row[in_idx])
                            wind = float(row[wind_idx])
                            clouds = float(row[cloud_idx])
                            elev = float(row[elev_idx])
                            az = float(row[az_idx])

                            az_rad = math.radians(az)
                            az_sin = math.sin(az_rad)
                            az_cos = math.cos(az_rad)
                            wind_draft = wind * delta_t

                            if uses_actual_solar and len(row) > actual_idx and row[actual_idx] is not None:
                                try:
                                    irradiance = float(row[actual_idx])
                                except ValueError:
                                    irradiance = 0.0
                            elif uses_true_solcast and len(row) > solcast_idx and row[solcast_idx] is not None:
                                try:
                                    irradiance = float(row[solcast_idx])
                                except ValueError:
                                    irradiance = 0.0
                            else:
                                irradiance = elev * ((100.0 - clouds) / 100.0) if elev > 0 else 0.0

                            solar_east = irradiance * az_sin if irradiance > 0 else 0.0
                            solar_south = irradiance * az_cos if irradiance > 0 else 0.0

                            data_list.append((rate, delta_t, wind_draft, clouds, solar_east, solar_south))

            elif self.version == "2":
                headers = {
                    "Authorization": f"Token {self.token}",
                    "Accept": "application/csv",
                    "Content-Type": "application/vnd.flux",
                }
                flux_query = f"""
                    from(bucket: "{self.bucket}")
                    |> range(start: -{time_range_str})
                    |> filter(fn: (r) => r["_measurement"] == "{measurement_name}" and r["entity_id"] == "{entity_id}")
                    |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
                """
                resp = requests.post(
                    f"{self.base_url}/api/v2/query?org={self.org}",
                    headers=headers,
                    data=flux_query,
                    timeout=60,
                )
                resp.raise_for_status()

                csv_lines = resp.text.splitlines()
                if len(csv_lines) > 1:
                    header_row = [h.strip() for h in csv_lines[0].split(",")]
                    try:
                        val_idx = header_row.index("value")
                        out_idx = header_row.index(out_field)
                        in_idx = header_row.index(in_field)
                        wind_idx = header_row.index("wind_speed")
                        cloud_idx = header_row.index("cloud_coverage")
                        elev_idx = header_row.index("sun_elevation")
                        az_idx = header_row.index("sun_azimuth")
                        solcast_idx = header_row.index("solcast_power") if "solcast_power" in header_row else -1
                        actual_idx = header_row.index("actual_solar_power") if "actual_solar_power" in header_row else -1
                    except ValueError:
                        return [], False

                    actual_valid_count = sum(
                        1
                        for r_str in csv_lines[1:]
                        if r_str.strip() and not r_str.startswith("#")
                        and len(r_str.split(",")) > actual_idx >= 0
                        and r_str.split(",")[actual_idx].strip()
                    )
                    solcast_valid_count = sum(
                        1
                        for r_str in csv_lines[1:]
                        if r_str.strip() and not r_str.startswith("#")
                        and len(r_str.split(",")) > solcast_idx >= 0
                        and r_str.split(",")[solcast_idx].strip()
                    )

                    uses_actual_solar = actual_valid_count >= 300
                    uses_true_solcast = uses_actual_solar or (solcast_valid_count >= 300)

                    for row_str in csv_lines[1:]:
                        if not row_str.strip() or row_str.startswith("#"):
                            continue
                        row = [r.strip() for r in row_str.split(",")]
                        if (
                            len(row) > max(val_idx, out_idx, in_idx, wind_idx, cloud_idx, elev_idx, az_idx)
                            and all(bool(row[i]) for i in [val_idx, out_idx, in_idx, wind_idx, cloud_idx, elev_idx, az_idx])
                        ):
                            rate = float(row[val_idx])
                            delta_t = float(row[out_idx]) - float(row[in_idx])
                            wind = float(row[wind_idx])
                            clouds = float(row[cloud_idx])
                            elev = float(row[elev_idx])
                            az = float(row[az_idx])

                            az_rad = math.radians(az)
                            az_sin = math.sin(az_rad)
                            az_cos = math.cos(az_rad)
                            wind_draft = wind * delta_t

                            if uses_actual_solar and len(row) > actual_idx and row[actual_idx]:
                                try:
                                    irradiance = float(row[actual_idx])
                                except ValueError:
                                    irradiance = 0.0
                            elif uses_true_solcast and len(row) > solcast_idx and row[solcast_idx]:
                                try:
                                    irradiance = float(row[solcast_idx])
                                except ValueError:
                                    irradiance = 0.0
                            else:
                                irradiance = elev * ((100.0 - clouds) / 100.0) if elev > 0 else 0.0

                            solar_east = irradiance * az_sin if irradiance > 0 else 0.0
                            solar_south = irradiance * az_cos if irradiance > 0 else 0.0

                            data_list.append((rate, delta_t, wind_draft, clouds, solar_east, solar_south))

            return data_list, uses_true_solcast

        except Exception as err:
            _LOGGER.error("Error fetching MLR data for %s: %s", entity_id, err)
            return [], False
