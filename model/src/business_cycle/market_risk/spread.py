"""기간 스프레드 대조 — **결정적인 부분.**

트랙 19는 국면 모델이 가치 타이밍에서 기간 스프레드를 넘어서지 못한다는 것을 보였다.
같은 물음이 여기서 더 날카롭다. 기간 스프레드(10년-3개월)는 그 자체로 잘 알려진 위험
신호이고, 매일 공짜로 받을 수 있는 계열 하나다.

세 모형을 같은 자리에서 견준다.

    spread_only   전방 수익 ~ 스프레드
    phase_only    전방 수익 ~ 국면 더미
    both          전방 수익 ~ 스프레드 + 국면 더미

`both`가 `spread_only`보다 얼마나 더 설명하는가가 국면의 몫이다. 그런데 설명변수를
더하면 R²는 **반드시** 오르므로, 그 증분이 우연보다 큰지를 라벨 이동 귀무분포로 잰다.

## 왜 이동 귀무분포인가

전방 창이 겹치고 라벨이 지속적이라 통상적인 F 검정의 자유도가 맞지 않는다. 라벨 순서
전체를 k주 이동시키면 라벨의 자기상관과 수익의 자기상관은 그대로 두고 **대응만** 끊는다.
트랙 17이 쓴 것과 같은 장치다.

## 위험도 함께 본다

수익 회귀만으로는 이 단계의 물음에 답하지 못한다. 노출 결정은 꼬리에 대한 결정이므로,
같은 세 모형을 **전방 수익의 제곱**에도 돌린다 — 분산을 설명하는가를 보는 것이다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.labels import PHASES
from ..phase_returns.significance import MINIMUM_SHIFT

#: 이동 간격. 촘촘히 해도 서로 거의 같은 정렬이라 정보가 늘지 않는다.
SHIFT_STRIDE: Final[int] = 2


def _design(spread: np.ndarray | None, phase: np.ndarray | None, weeks: int) -> np.ndarray:
    """절편 + (스프레드) + (국면 더미 3개). 마지막 국면은 기준으로 빼 공선성을 막는다."""

    columns = [np.ones(weeks)]
    if spread is not None:
        columns.append(spread)
    if phase is not None:
        for name in PHASES[:-1]:
            columns.append((phase == name).astype(float))
    return np.column_stack(columns)


def _r_squared(design: np.ndarray, target: np.ndarray) -> float:
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coefficients
    total = float(((target - target.mean()) ** 2).sum())
    if total <= 0:
        return 0.0
    return float(1.0 - float((residual**2).sum()) / total)


def _aligned(
    phase: pd.Series, spread: pd.Series, target: pd.Series
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    frame = pd.DataFrame(
        {"phase": phase.astype(str), "spread": spread.astype(float), "target": target.astype(float)}
    ).dropna()
    frame = frame[frame["phase"].isin(PHASES)]
    return (
        frame["phase"].to_numpy(),
        frame["spread"].to_numpy(dtype=float),
        frame["target"].to_numpy(dtype=float),
    )


def compare(
    phase: pd.Series,
    spread: pd.Series,
    target: pd.Series,
    label: str,
    stride: int = SHIFT_STRIDE,
) -> dict[str, Any]:
    """세 모형과, 국면 증분의 이동 귀무분포."""

    labels, values, outcome = _aligned(phase, spread, target)
    weeks = len(outcome)
    if weeks < 100:
        return {"target": label, "weeks": weeks, "usable": False}

    spread_only = _r_squared(_design(values, None, weeks), outcome)
    phase_only = _r_squared(_design(None, labels, weeks), outcome)
    both = _r_squared(_design(values, labels, weeks), outcome)
    increment = both - spread_only

    offsets = [
        offset
        for offset in range(0, weeks, stride)
        if MINIMUM_SHIFT <= offset <= weeks - MINIMUM_SHIFT
    ]
    draws = np.array(
        [
            _r_squared(_design(values, np.roll(labels, offset), weeks), outcome) - spread_only
            for offset in offsets
        ]
    )
    extreme = int((draws >= increment).sum())

    return {
        "target": label,
        "weeks": weeks,
        "usable": True,
        "spread_only_r_squared": round(spread_only, 6),
        "phase_only_r_squared": round(phase_only, 6),
        "both_r_squared": round(both, 6),
        "incremental_r_squared_of_phase": round(increment, 6),
        "incremental_r_squared_of_spread_over_phase": round(both - phase_only, 6),
        "shifts_used": len(offsets),
        "null_median_increment": round(float(np.median(draws)), 6),
        "null_p90_increment": round(float(np.quantile(draws, 0.90)), 6),
        "null_p": round(float((extreme + 1) / (len(offsets) + 1)), 4),
    }


def read(returns: dict[str, Any], variance: dict[str, Any]) -> dict[str, Any]:
    """두 회귀를 하나의 문장으로. 결정적 판정은 수익 쪽에 건다.

    분산 쪽은 보강 증거다 — 수익을 설명하지 못해도 분산을 설명하면 그것은 노출 결정에
    쓸모가 있을 수 있고, 그 가능성을 지워 버리지 않기 위해 함께 낸다.
    """

    beats_on_returns = bool(returns.get("usable")) and float(returns["null_p"]) <= 0.05
    beats_on_variance = bool(variance.get("usable")) and float(variance["null_p"]) <= 0.05
    return {
        "returns": returns,
        "variance": variance,
        "phase_adds_beyond_the_spread_on_returns": beats_on_returns,
        "phase_adds_beyond_the_spread_on_variance": beats_on_variance,
        "reading": (
            (
                "국면이 스프레드 위에 수익 설명력을 얹는다."
                if beats_on_returns
                else "**국면이 스프레드 위에 수익 설명력을 얹지 못한다.**"
            )
            + " "
            + (
                "분산 쪽에서는 얹는다 — 수익의 방향이 아니라 **흔들림의 크기**를 "
                "가른다는 뜻이고, 노출 결정이 묻는 것이 그쪽이므로 그대로 적는다."
                if beats_on_variance
                else "분산 쪽에서도 얹지 못한다."
            )
        ),
    }
