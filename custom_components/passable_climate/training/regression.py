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
) -> tuple[tuple[float, float] | None, list[bool] | None]:
    """Robust 2D linear regression using Random Sample Consensus (RANSAC).
    Returns: ((slope, intercept), inlier_mask) or (None, None)
    """
    try:
        data = np.array(data_list, dtype=np.float64)
        n_samples = data.shape[0]
        if n_samples < min_inliers or n_samples < 2:
            return None, None

        best_inlier_count = 0
        best_model: tuple[float, float] | None = None
        best_inliers_mask: np.ndarray | None = None

        for _ in range(n_iterations):
            idx = np.random.choice(n_samples, 2, replace=False)
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
                # Refit using all inliers via standard least squares
                inliers = data[inlier_mask]
                A = np.c_[inliers[:, 0], np.ones(inliers.shape[0])]
                m_fit, b_fit = np.linalg.lstsq(A, inliers[:, 1], rcond=None)[0]
                best_model = (float(m_fit), float(b_fit))

        if best_model and best_inliers_mask is not None:
            return best_model, best_inliers_mask.tolist()

        return None, None

    except Exception as err:
        _LOGGER.error("RANSAC regression error: %s", err)
        return None, None

def multiple_linear_regression(
    data_list: list[tuple[float, ...]],
    alphas: list[float] | None = None,
) -> tuple[list[float] | None, float | None, list[float] | None]:
    """Multiple Linear Regression with Ridge regularization and cross-validation.
    Target Y is column 0, features X are columns 1..N.
    Returns: (coefficients, intercept, standardized_coefficients)
    """
    try:
        if alphas is None:
            alphas = [10.0, 50.0, 100.0, 500.0, 1000.0, 5000.0, 10000.0, 25000.0, 50000.0]

        data = np.array(data_list, dtype=np.float64)
        if data.shape[0] <= data.shape[1] or data.shape[0] < 50:
            _LOGGER.warning("MLR: Insufficient data points (%d)", data.shape[0])
            return None, None, None

        Y = data[:, 0]
        X = data[:, 1:]

        best_alpha = alphas[0]

        # 80/20 Cross-validation for alpha tuning
        if len(alphas) > 1 and data.shape[0] > 100:
            rng = np.random.RandomState(42)
            indices = np.arange(data.shape[0])
            rng.shuffle(indices)
            split_idx = int(data.shape[0] * 0.8)

            train_idx, test_idx = indices[:split_idx], indices[split_idx:]
            X_train, Y_train = X[train_idx], Y[train_idx]
            X_test, Y_test = X[test_idx], Y[test_idx]

            X_mean_train = np.mean(X_train, axis=0)
            X_std_train = np.std(X_train, axis=0, ddof=1)
            X_std_train[X_std_train == 0] = 1e-9

            X_scaled_train = (X_train - X_mean_train) / X_std_train
            A_scaled_train = np.c_[X_scaled_train, np.ones(X_scaled_train.shape[0])]

            I = np.eye(A_scaled_train.shape[1])
            I[-1, -1] = 0.0

            best_mse = float("inf")
            X_scaled_test = (X_test - X_mean_train) / X_std_train
            A_scaled_test = np.c_[X_scaled_test, np.ones(X_scaled_test.shape[0])]

            for a in alphas:
                c_scaled = np.linalg.solve(
                    A_scaled_train.T @ A_scaled_train + a * I,
                    A_scaled_train.T @ Y_train,
                )
                Y_pred = A_scaled_test @ c_scaled
                mse = float(np.mean((Y_test - Y_pred) ** 2))

                if mse < best_mse:
                    best_mse = mse
                    best_alpha = a

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
