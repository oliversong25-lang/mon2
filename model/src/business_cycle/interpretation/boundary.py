"""경계 표시 규칙. 공식 국면은 하나로 두고 불확실성만 보이게 한다.

경계에서 국면을 둘로 쪼개면 "둘 중 하나"라는 답이 나오고, 그건 답이 아니다. 그래서
공식 국면은 언제나 모델이 고른 하나이고, 이 모듈은 그 하나가 **얼마나 확실한지**만
따로 말한다.

임계값은 사례를 보고 고르지 않았다. 1995~2026 최신 수정치 실행에서 **실제로 국면이
바뀐 주**의 1·2순위 확률 차이 중앙값을 기준으로 삼는다. 그 값 이하로 벌어져 있으면
"전형적인 전환 주만큼 덜 갈라져 있다"는 뜻이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

#: 실제 국면 전환 주의 1·2순위 확률 차이 중앙값(1995-01-01~2026-08-14, 전환 211주).
#: `boundary_audit`가 같은 값을 다시 계산해 이 상수가 자료와 맞는지 확인한다.
BOUNDARY_GAP: float = 0.1124

#: 임계값을 정한 근거 구간. 문서와 코드가 같은 값을 가리키게 둔다.
CALIBRATION_WINDOW: tuple[str, str] = ("1995-01-01", "2026-08-14")


@dataclass(frozen=True)
class BoundaryView:
    """표시 전용. 어떤 필드도 모델로 되돌아가지 않는다."""

    boundary_flag: bool
    boundary_reason: str
    winner_probability: float
    runner_up_phase: str
    runner_up_probability: float
    gap: float
    entropy: float


def _probability_columns(history: pd.DataFrame) -> list[str]:
    return [str(column) for column in history.columns if str(column).startswith("p_")]


def probability_frame(history: pd.DataFrame) -> pd.DataFrame:
    """확률 열만 뽑는다. 값은 그대로 두고 이름만 국면 코드로 바꾼다."""

    columns = _probability_columns(history)
    frame = history[columns].copy()
    frame.columns = pd.Index([column[2:] for column in columns])
    return frame


def phase_entropy(probabilities: np.ndarray) -> np.ndarray:
    """확률이 얼마나 퍼져 있는지. 낮을수록 한 국면에 몰려 있다."""

    safe = np.where(probabilities > 0, probabilities, 1.0)
    entropy = -np.sum(np.where(probabilities > 0, probabilities * np.log(safe), 0.0), axis=1)
    return np.asarray(entropy, dtype=float)


def boundary_audit(history: pd.DataFrame) -> pd.DataFrame:
    """경계 규칙을 정할 때 본 분포를 그대로 남긴다.

    1·2순위 확률, 차이, 엔트로피, 주간 확률 이동, 국면 전환 여부를 주 단위로 남기고
    전환 주와 나머지를 나눠 요약한다. 규칙은 이 표에서만 나온다.
    """

    frame = probability_frame(history)
    values = frame.to_numpy(dtype=float)
    ordered = np.sort(values, axis=1)
    codes = history["phase_code"].astype(str)
    switch = codes.ne(codes.shift(1))
    audit = pd.DataFrame(
        {
            "winner_probability": ordered[:, -1],
            "runner_up_probability": ordered[:, -2],
            "gap": ordered[:, -1] - ordered[:, -2],
            "entropy": phase_entropy(values),
            "weekly_probability_movement": frame.diff().abs().sum(axis=1).to_numpy(),
            "phase_switched": switch.to_numpy(),
            "phase_code": codes.to_numpy(),
        },
        index=history.index,
    )
    return audit


def boundary_summary(audit: pd.DataFrame) -> dict[str, Any]:
    """규칙 문서에 넣을 요약. 임계값이 자료와 맞는지 여기서 확인한다."""

    switched = audit[audit["phase_switched"].astype(bool)]
    steady = audit[~audit["phase_switched"].astype(bool)]
    flagged = audit["gap"].le(BOUNDARY_GAP)
    return {
        "calibration_window": list(CALIBRATION_WINDOW),
        "weeks": int(len(audit)),
        "phase_switch_weeks": int(len(switched)),
        "gap_median_on_switch_weeks": round(float(switched["gap"].median()), 6),
        "gap_median_on_steady_weeks": round(float(steady["gap"].median()), 6),
        "selected_threshold": BOUNDARY_GAP,
        "threshold_matches_measurement": bool(
            abs(float(switched["gap"].median()) - BOUNDARY_GAP) < 5e-4
        ),
        "flagged_weeks": int(flagged.sum()),
        "flagged_share": round(float(flagged.mean()), 6),
        "gap_quantiles": {
            str(q): round(float(audit["gap"].quantile(q)), 6)
            for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9)
        },
        "entropy_median": round(float(audit["entropy"].median()), 6),
        "weekly_movement_median": round(float(audit["weekly_probability_movement"].median()), 6),
    }


def boundary_view(history: pd.DataFrame, as_of: pd.Timestamp) -> BoundaryView:
    """한 주의 경계 상태. 공식 국면은 인자로 받지 않고 건드리지도 않는다."""

    frame = probability_frame(history)
    # 스텁이 .loc 결과를 좁히지 못하므로 값을 명시적으로 Series로 다시 담는다.
    row = pd.Series(
        frame.loc[[as_of]].to_numpy(dtype=float)[0], index=list(frame.columns), dtype=float
    )
    ordered = row.sort_values(ascending=False)
    winner = float(ordered.iloc[0])
    runner_up = float(ordered.iloc[1])
    gap = winner - runner_up
    flagged = bool(gap <= BOUNDARY_GAP)
    reason = (
        f"1·2순위 확률 차이 {gap:.3f}가 전환 주 중앙값 {BOUNDARY_GAP:.4f} 이하다. "
        f"2순위 {ordered.index[1]}와(과) 뚜렷이 갈라져 있지 않다."
        if flagged
        else f"1·2순위 확률 차이 {gap:.3f}로 전환 주 중앙값 {BOUNDARY_GAP:.4f}보다 크다."
    )
    return BoundaryView(
        boundary_flag=flagged,
        boundary_reason=reason,
        winner_probability=winner,
        runner_up_phase=str(ordered.index[1]),
        runner_up_probability=runner_up,
        gap=gap,
        entropy=float(phase_entropy(row.to_numpy(dtype=float).reshape(1, -1))[0]),
    )
