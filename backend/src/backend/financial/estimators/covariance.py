"""Covariance estimator Σ.

Σ is computed on the fixed 30-day hourly window (720 observations), NOT on the
user's τ-window — it is shared across all agents and recomputed on each solve
(see CLAUDE.md §5.3). This module is pure: it takes a returns
matrix and produces the sample covariance.
"""

from __future__ import annotations

import numpy as np


def covariance(returns: np.ndarray) -> np.ndarray:
    """Sample covariance Σ (N, N) from an hourly returns matrix.

    Args:
        returns: shape (T, N) — T hourly observations across N assets.

    Returns:
        Σ (N, N), symmetric, sample covariance with ddof=1.
    """
    if returns.ndim != 2:
        raise ValueError(f"returns must be 2-D (T, N), got shape {returns.shape}")
    if returns.shape[0] < 2:
        raise ValueError("need at least 2 observations for a sample covariance")
    # np.cov collapses N=1 to a scalar; keep the (N, N) contract.
    return np.atleast_2d(np.cov(returns, rowvar=False, ddof=1))
