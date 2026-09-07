# Passable Smart Climate Engine

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![GitHub Release](https://img.shields.io/github/v/release/GBear09/passable-smart-climate-engine)](https://github.com/GBear09/passable-smart-climate-engine/releases)

A unified, high-performance Home Assistant custom integration for thermodynamic building modeling, psychrometric enthalpy evaluation, natural ventilation advisory, and convective HVAC circulation.

---

## Features

- **ASHRAE Moist Air Specific Enthalpy ($h$):** Evaluates true thermal energy (BTU/lb) and moisture loads rather than relying solely on dry-bulb temperature, preventing latent heat penalties.
- **Dual-Zone Thermal Micro-Simulation:** Projects 10-minute micro-steps 12–24 hours forward to optimize peak free cooling/heating windows.
- **Anti-Flapping & Forward Lookahead:** Enforces 60-minute forecast trajectory stability and 45-minute minimum dwell latches to eliminate notification spam.
- **Cardinal Facade Solar Decomposition:** Computes direct normal insolation ($I_{south}, I_{east}, I_{west}$) mapped against building orientation.
- **In-Memory Machine Learning:** Automatically fits RANSAC and Ridge Multiple Linear Regression (MLR) models from historical InfluxDB data inside thread-pool executors.
- **Convective Convective Circulation Manager:** Orchestrates attic and basement fan air redistribution with stall detection and ML feedback.
- **Zero-Disruption Dashboard Compatibility:** Automatically registers canonical `sensor.plot_data_*` entities for instant plug-and-play with existing Lovelace Plotly scatter plots and radar charts.

---

## Installation via HACS

1. In Home Assistant, open **HACS** $\rightarrow$ **Integrations**.
2. Click the three dots in the upper right corner $\rightarrow$ **Custom repositories**.
3. Enter `https://github.com/GBear09/passable-smart-climate-engine`, select **Integration**, and click **Add**.
4. Search for **Passable Smart Climate Engine** and click **Download**.
5. Restart Home Assistant.
6. Go to **Settings** $\rightarrow$ **Devices & Services** $\rightarrow$ **Add Integration** $\rightarrow$ Search for **Passable Smart Climate Engine**.

---

## Configuration

The integration is 100% configured through the Home Assistant UI via Config Flow and Options Flow:

- **Atmospheric Sources:** Weather entity (`weather.home`), solar irradiance, and home occupancy (`input_select.home_mode`).
- **Thermal Zones:** Upstairs & Downstairs thermostats (`climate.*`), average temperature/humidity sensors, and open window contact sensors.
- **Convective Circulation:** Attic and basement temperature sensors.
- **InfluxDB Connection:** Host, port, credentials (masked password/token), database/bucket.
- **Custom Deadbands:** Enthalpy threshold, dew point limits, wind speed safety gates, and dwell filters.

---

## Entities Provided

- **Advisory & Plan:**
  - `sensor.advisor_simulation_data` (forward 10-minute simulation trajectory)
  - `binary_sensor.passable_climate_upstairs_free_cooling`
  - `binary_sensor.passable_climate_downstairs_free_cooling`
  - `binary_sensor.passable_climate_weather_hazard_veto`
- **16 Empirical Scatter Plot Sensors (`sensor.plot_data_*`):**
  - Temp: Closed, Open, HVAC Heat, HVAC Cool (Upstairs & Downstairs)
  - Humidity: Closed, Open, HVAC Heat, HVAC Cool (Upstairs & Downstairs)
- **Controls & Actions:**
  - `button.retrain_thermal_models` (manually triggers background InfluxDB regression)
