"""혼합빈도 결측을 허용하는 주간 국소선형추세 동적요인모형."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .base import FactorEstimate, FactorModel


class DynamicFactorModel(FactorModel):
    """1차원 잠재 경기요인을 Kalman filter로 순방향 추정한다.

    상태는 `[level, slope]`이고 각 지표 신호는 같은 level의 잡음 있는 관측으로
    취급한다. 월간 지표가 없는 주는 관측 업데이트 없이 예측만 수행한다. 이는
    완전한 계열별 loading 재추정 DFM보다 단순하지만, 이름뿐인 이동평균이 아니라
    결측 관측식을 가진 실제 상태공간 필터다. backward smoothing은 사용하지 않는다.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.level_q = float(config["level_process_variance"])
        self.slope_q = float(config["slope_process_variance"])
        self.observation_r = float(config["observation_variance"])
        self.initial_variance = float(config["initial_variance"])

    def fit_filter(self, events: pd.DataFrame) -> FactorEstimate:
        transition = np.array([[1.0, 1.0], [0.0, 1.0]])
        process = np.diag([self.level_q, self.slope_q])
        state = np.zeros(2, dtype=float)
        covariance = np.eye(2) * self.initial_variance
        levels: list[float] = []
        slopes: list[float] = []
        updates: list[int] = []
        innovations = pd.DataFrame(np.nan, index=events.index, columns=events.columns)
        initialized = False
        for timestamp, row in events.iterrows():
            state = transition @ state
            covariance = transition @ covariance @ transition.T + process
            observed = row.dropna()
            if not initialized and not observed.empty:
                state[0] = float(observed.mean())
                initialized = True
            for indicator_id, value in observed.items():
                h = np.array([1.0, 0.0])
                innovation = float(value) - float(h @ state)
                variance = float(h @ covariance @ h.T + self.observation_r)
                gain = covariance @ h / variance
                state = state + gain * innovation
                covariance = (np.eye(2) - np.outer(gain, h)) @ covariance
                innovations.loc[timestamp, indicator_id] = innovation
            levels.append(float(state[0]) if initialized else np.nan)
            slopes.append(float(state[1]) if initialized else np.nan)
            updates.append(int(observed.size))
        factor = pd.Series(levels, index=events.index, name="dynamic_factor")
        return FactorEstimate(
            factor=factor,
            contributions=innovations,
            metadata={
                "model": "local_linear_dynamic_factor",
                "slopes": pd.Series(slopes, index=events.index),
                "observation_updates": pd.Series(updates, index=events.index),
                "uses_backward_smoothing": False,
            },
        )
