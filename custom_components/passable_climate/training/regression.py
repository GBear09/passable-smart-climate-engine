"""High-performance RANSAC and Ridge Multiple Linear Regression solvers using NumPy.
Thread-safe and designed to execute inside Home Assistant's thread-pool executor.
"""

from __future__ import annotations

import logging
import numpy as np

_LOGGER = logging.getLogger(__name__)

def ransac_linear_fit(
    data_list: list[list[float]] | list[tuple[float, float]],
    n_iterations: int = 150,
    threshold: float = 0.5,
    min_inliers: int = 10,
    seed: int = 42,
) -> tuple[tuple[float, float] | None, list[bool] | None]:
    """Robust 2D linear regression using Random Sample Consensus (RANSAC).
    Returns: ((slope, intercept), inlier_mask) or (None, None)
    """
    try:
        data = np.array(data_list, dtype=np.float64)
        n_samples = data.shape[0]
        if n_samples < min_inliers or n_samples < 2:
            return None, None

        rng = np.random.default_rng(seed)
        best_inlier_count = 0
        best_model: tuple[float, float] | None = None
        best_inliers_mask: np.ndarray | None = None

        for _ in range(n_iterations):
            idx = rng.choice(n_samples, 2, replace=False)
            p1, p2 = data[idx[0]], data[idx[1]]
            if abs(p2[0] - p1[0]) < 1e-9:
                continue

            m = (p2[1] - p1[1]) / (p2[0] - p1[0])
            b = p1[1] - m * p1[0]

            norm_factor = np.sqrt(m**2 + 1.0)
            distances = np.abs(m * data[:, 0] - data[:, 1] + b) / norm_factor
            inlier_mask = distances < threshold
            inlier_count = int(np.count_nonzero(inlier_mask))

            if inlier_count > best_inlier_count and inlier_count >= min_inliers:
                best_inlier_count = inlier_count
                best_inliers_mask = inlier_mask

        if best_inliers_mask is not None:
            # Refit once using all inliers via standard least squares
            inliers = data[best_inliers_mask]
            A = np.c_[inliers[:, 0], np.ones(inliers.shape[0])]
            m_fit, b_fit = np.linalg.lstsq(A, inliers[:, 1], rcond=None)[0]
            best_model = (float(m_fit), float(b_fit))
            return best_model, best_inliers_mask.tolist()

        return None, None

    except Exception as err:
        _LOGGER.error("RANSAC regression error: %s", err)
        return None, None

def multiple_linear_regression(
    data_list: list[tuple[float, ...]],
    alphas: list[float] | None = None,
) -> tuple[list[float] | None, float | None, list[float] | None]:
    """Multiple Linear Regression with Ridge regularization and blocked cross-validation.
    Target Y is column 0, features X are columns 1..N.
    Returns: (coefficients, intercept, standardized_coefficients)
    """
    try:
        if alphas is None:
            alphas = [0.1, 1.0, 5.0, 10.0, 50.0, 100.0, 500.0, 1000.0, 5000.0]

        data = np.array(data_list, dtype=np.float64)
        if data.shape[0] <= data.shape[1] or data.shape[0] < 50:
            _LOGGER.warning("MLR: Insufficient data points (%d)", data.shape[0])
            return None, None, None

        Y = data[:, 0]
        X = data[:, 1:]

        best_alpha = alphas[0]

        # Blocked K-fold over time-ordered samples to prevent autocorrelation leakage
        if len(alphas) > 1 and data.shape[0] > 100:
            k_folds = min(8, max(3, data.shape[0] // 50))
            blocks = np.array_split(np.arange(data.shape[0]), k_folds)
            alpha_mse: dict[float, float] = {a: 0.0 for a in alphas}

            for k, test_idx in enumerate(blocks):
                # 1-block gap buffer to eliminate autocorrelation leakage across boundaries
                train_blocks = [b for j, b in enumerate(blocks) if abs(j - k) > 1]
                if not train_blocks:
                    train_blocks = [b for j, b in enumerate(blocks) if j != k]
                train_idx = np.concatenate(train_blocks)

                X_train, Y_train = X[train_idx], Y[train_idx]
                X_test, Y_test = X[test_idx], Y[test_idx]

                X_mean_train = np.mean(X_train, axis=0)
                X_std_train = np.std(X_train, axis=0, ddof=1)
                X_std_train[X_std_train == 0] = 1e-9

                X_scaled_train = (X_train - X_mean_train) / X_std_train
                A_scaled_train = np.c_[X_scaled_train, np.ones(X_scaled_train.shape[0])]

                I = np.eye(A_scaled_train.shape[1])
                I[-1, -1] = 0.0

                X_scaled_test = (X_test - X_mean_train) / X_std_train
                A_scaled_test = np.c_[X_scaled_test, np.ones(X_scaled_test.shape[0])]

                AtA = A_scaled_train.T @ A_scaled_train
                AtY = A_scaled_train.T @ Y_train

                for a in alphas:
                    try:
                        c_scaled = np.linalg.solve(AtA + a * I, AtY)
                        Y_pred = A_scaled_test @ c_scaled
                        mse = float(np.mean((Y_test - Y_pred) ** 2))
                        alpha_mse[a] += mse
                    except np.linalg.LinAlgError:
                        alpha_mse[a] += 1e9

            best_alpha = min(alpha_mse, key=alpha_mse.get)

        # Final regression on full dataset with optimal alpha
        X_mean = np.mean(X, axis=0)
        X_std = np.std(X, axis=0, ddof=1)
        X_std[X_std == 0] = 1e-9

        X_scaled = (X - X_mean) / X_std
        A_scaled = np.c_[X_scaled, np.ones(X_scaled.shape[0])]

        I = np.eye(A_scaled.shape[1])
        I[-1, -1] = 0.0

        c_scaled = np.linalg.solve(
            A_scaled.T @ A_scaled + best_alpha * I,
            A_scaled.T @ Y,
        )

        coefficients = (c_scaled[:-1] / X_std).tolist()
        intercept = float(c_scaled[-1] - np.sum(c_scaled[:-1] * X_mean / X_std))

        # Standardized coefficients for radar charts
        Y_std = float(np.std(Y, ddof=1))
        if Y_std == 0:
            Y_std = 1e-9

        std_coefficients = [float(c * (X_std[i] / Y_std)) for i, c in enumerate(coefficients)]

        return coefficients, intercept, std_coefficients

    except Exception as err:
        _LOGGER.error("Multiple Linear Regression error: %s", err)
        return None, None, None


def fit_online_rolling_ols(
    samples: list[tuple[float, float]],
    regime: str = "temp_closed",
) -> tuple[float | None, float | None, float]:
    """Fit a physical-sign-clamped 1D Ordinary Least Squares model (rate vs delta_t)
    from a rolling window of homogeneous telemetry observations.
    Requires at least 6 observations (~30 minutes of 5-min ticks or 6+ 1-min ticks).
    Returns: (slope, intercept, r_squared) or (None, None, 0.0) if invalid.
    """
    try:
        if len(samples) < 6:
            return None, None, 0.0

        arr = np.array(samples, dtype=np.float64)
        x = arr[:, 0]
        y = arr[:, 1]
        if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
            return None, None, 0.0

        mean_x = float(np.mean(x))
        mean_y = float(np.mean(y))
        std_x = float(np.std(x))
        std_y = float(np.std(y))

        if std_x >= 0.15:
            A = np.c_[x, np.ones(len(x))]
            m_raw, b_raw = np.linalg.lstsq(A, y, rcond=None)[0]
            slope = float(m_raw)
            intercept = float(b_raw)
            y_pred = slope * x + intercept
            ss_res = float(np.sum((y - y_pred) ** 2))
            ss_tot = float(np.sum((y - mean_y) ** 2))
            r_sq = max(0.0, min(1.0, 1.0 - (ss_res / ss_tot))) if ss_tot > 1e-6 else 0.5
        else:
            # Near-constant delta_t over the window: derive single-parameter physical estimate
            if regime in ("temp_closed", "temp_open"):
                if abs(mean_x) < 1.5:
                    return None, None, 0.0
                slope = mean_y / mean_x
                intercept = 0.0
                y_pred = slope * x
                ss_res = float(np.sum((y - y_pred) ** 2))
                denom = max(0.05, float(np.sum(y**2)))
                r_sq = max(0.0, min(1.0, 1.0 - (ss_res / denom)))
            else:
                slope = 0.02
                intercept = mean_y - slope * mean_x
                r_sq = 0.6 if std_y < max(0.2, abs(mean_y) * 0.5) else 0.3

        # Enforce physical sign and magnitude constraints per regime
        if regime == "temp_closed":
            if slope <= 0.0:
                return None, None, 0.0
            slope = max(0.005, min(0.25, slope))
            intercept = max(-1.5, min(1.5, intercept))
        elif regime == "temp_open":
            if slope <= 0.0:
                return None, None, 0.0
            slope = max(0.02, min(0.80, slope))
            intercept = max(-2.0, min(2.0, intercept))
        elif regime == "temp_heat":
            if mean_y <= 0.2:
                return None, None, 0.0
            slope = max(0.005, min(0.20, slope if slope > 0 else 0.02))
            intercept = max(0.3, min(10.0, mean_y - slope * mean_x))
        elif regime == "temp_cool":
            if mean_y >= -0.2:
                return None, None, 0.0
            slope = max(0.005, min(0.20, slope if slope > 0 else 0.02))
            intercept = max(-10.0, min(-0.3, mean_y - slope * mean_x))

        return round(slope, 4), round(intercept, 4), round(r_sq, 3)

    except Exception as err:
        _LOGGER.debug("Online rolling OLS error: %s", err)
        return None, None, 0.0
