"""실현변동성 대조와 세 갈래 비교.

트랙 24의 `spread.compare`와 같은 골격이다. 설계행렬만 바뀐다 — 기간 스프레드 하나였던
자리에 **실현분산 + 그 제곱근 + 기간 스프레드**가 들어간다.

## 지연을 만들지 않는다

t주의 실현분산은 t주까지의 주간 수익으로 계산한다. t주 수익을 포함하는 것이 옳다 —
가격은 그 주 금요일에 이미 알려져 있고, 이 대조의 논점이 바로 **가격은 발표 지연이
없다**는 것이기 때문이다. 여기서 대조에 지연을 주면 대조를 인위적으로 약화시키는 것이 된다.

## 왜 수준과 제곱근을 함께 주는가

목표가 제곱 수익이므로 척도가 맞는 것은 분산이다. 그러나 관계가 제곱근 쪽에서 선형일
수도 있다. 둘 다 주면 형태를 잘못 골라 대조가 지는 일이 없다. 대조에 유리한 설정이고,
그것이 이 검정에서 옳은 방향이다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.labels import PHASES
from ..phase_returns.significance import MINIMUM_SHIFT

#: 이동 간격. 트랙 24와 같다.
SHIFT_STRIDE: Final[int] = 2

#: 회귀를 돌릴 최소 주 수. 이보다 적으면 맞추는 것이지 재는 것이 아니다.
MINIMUM_WEEKS: Final[int] = 100


def realised_variance(weekly: pd.Series, lookback: int) -> pd.Series:
    """t주까지 ``lookback``주의 평균 제곱수익. t주를 포함한다 — 가격은 지연이 없다."""

    squared = pd.Series(weekly.to_numpy(dtype=float) ** 2, index=weekly.index)
    return squared.rolling(lookback, min_periods=lookback).mean()


def _design(
    weeks: int,
    control: np.ndarray | None,
    phase: np.ndarray | None,
) -> np.ndarray:
    """절편 + (대조 열들) + (국면 더미 3개). 마지막 국면은 기준으로 뺀다."""

    columns = [np.ones(weeks)]
    if control is not None:
        columns.extend(control[:, index] for index in range(control.shape[1]))
    if phase is not None:
        columns.extend((phase == name).astype(float) for name in PHASES[:-1])
    return np.column_stack(columns)


def _r_squared(design: np.ndarray, target: np.ndarray) -> float:
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coefficients
    total = float(((target - target.mean()) ** 2).sum())
    if total <= 0:
        return 0.0
    return float(1.0 - float((residual**2).sum()) / total)


def _aligned(
    phase: pd.Series, control: pd.DataFrame, target: pd.Series
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = control.copy()
    frame["__phase"] = phase.astype(str)
    frame["__target"] = target.astype(float)
    frame = frame.dropna()
    frame = frame[frame["__phase"].isin(PHASES)]
    columns = [name for name in frame.columns if not name.startswith("__")]
    return (
        frame["__phase"].to_numpy(),
        frame[columns].to_numpy(dtype=float),
        frame["__target"].to_numpy(dtype=float),
    )


def compare(
    phase: pd.Series,
    control: pd.DataFrame,
    target: pd.Series,
    label: str,
    stride: int = SHIFT_STRIDE,
) -> dict[str, Any]:
    """대조 단독 / 국면 단독 / 둘 다, 그리고 양방향 증분과 이동 귀무분포."""

    labels, values, outcome = _aligned(phase, control, target)
    weeks = len(outcome)
    if weeks < MINIMUM_WEEKS:
        return {"target": label, "weeks": weeks, "usable": False}

    control_only = _r_squared(_design(weeks, values, None), outcome)
    phase_only = _r_squared(_design(weeks, None, labels), outcome)
    both = _r_squared(_design(weeks, values, labels), outcome)
    increment = both - control_only
    reverse = both - phase_only

    offsets = [
        offset
        for offset in range(0, weeks, stride)
        if MINIMUM_SHIFT <= offset <= weeks - MINIMUM_SHIFT
    ]
    draws = np.array(
        [
            _r_squared(_design(weeks, values, np.roll(labels, offset)), outcome) - control_only
            for offset in offsets
        ]
    )
    extreme = int((draws >= increment).sum())

    return {
        "target": label,
        "weeks": weeks,
        "usable": True,
        "control_columns": [str(name) for name in control.columns],
        "control_only_r_squared": round(control_only, 6),
        "phase_only_r_squared": round(phase_only, 6),
        "both_r_squared": round(both, 6),
        "incremental_r_squared_of_phase": round(increment, 6),
        "incremental_r_squared_of_control_over_phase": round(reverse, 6),
        "phase_absorbs_the_control": bool(reverse < increment / 10.0),
        "shifts_used": len(offsets),
        "null_median_increment": round(float(np.median(draws)), 6),
        "null_p90_increment": round(float(np.quantile(draws, 0.90)), 6),
        "null_p": round(float((extreme + 1) / (len(offsets) + 1)), 4),
    }


def build_control(
    weekly: pd.Series, spread: pd.Series, lookback: int, keep_spread: bool = True
) -> pd.DataFrame:
    """한 되돌아보기 창의 대조 행렬. 수준과 제곱근을 함께 준다."""

    variance = realised_variance(weekly, lookback)
    frame = pd.DataFrame(
        {
            f"realised_variance_{lookback}w": variance,
            f"realised_volatility_{lookback}w": np.sqrt(variance.clip(lower=0.0)),
        }
    )
    if keep_spread:
        frame["term_spread"] = spread.astype(float)
    return frame


def lookback_table(
    phase: pd.Series,
    weekly: pd.Series,
    spread: pd.Series,
    target: pd.Series,
    lookbacks: tuple[int, ...],
    stride: int = SHIFT_STRIDE,
) -> list[dict[str, Any]]:
    """되돌아보기 창마다 대조 단독 설명력. 어느 것이 강한지 규칙이 고르게 한다."""

    rows: list[dict[str, Any]] = []
    for lookback in lookbacks:
        control = build_control(weekly, spread, lookback)
        read = compare(phase, control, target, f"lookback {lookback}w", stride=stride)
        rows.append(
            {
                "lookback_weeks": lookback,
                "control_only_r_squared": read.get("control_only_r_squared"),
                "phase_only_r_squared": read.get("phase_only_r_squared"),
                "both_r_squared": read.get("both_r_squared"),
                "incremental_r_squared_of_phase": read.get("incremental_r_squared_of_phase"),
                "null_p": read.get("null_p"),
                "weeks": read.get("weeks"),
            }
        )
    return rows
