"""전환 감시. 경계 표시와 다른 질문에 답한다.

경계 표시는 "지금 갈라져 있는가"를 묻는다. 전환 감시는 "옆 국면 쪽으로 밀리고
있는가"를 묻는다. 한 주가 애매한 것과, 여러 주에 걸쳐 한 방향으로 밀리는 것은
다른 사실이다.

계산은 그 시점까지의 자료만 쓴다. 앞을 보지 않는다. 그리고 한 주만 보고 전환을
말하지 않는다 — 다만 그 한 주의 움직임이 이력 전체에서 상위 1%에 해당할 만큼
압도적이면 예외로 인정한다. 두 임계값 모두 이력 분포에서 나왔다.

이 진단은 공식 국면을 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .boundary import probability_frame
from .contract import adjacent_phases

#: 인접 국면 확률의 4주 변화 분포에서 p90(1995~2026 최신 수정치, 같은 국면 유지 구간).
SUSTAINED_FOUR_WEEK_RISE: float = 0.1667

#: 같은 분포의 1주 변화 p99. 한 주만으로 인정할 수 있는 "압도적" 기준이다.
OVERWHELMING_ONE_WEEK_RISE: float = 0.2315

#: 지속으로 인정할 최소 상승 주 수(최근 4주 중).
MINIMUM_RISING_WEEKS: int = 3

LOOKBACK_WEEKS: int = 4


@dataclass(frozen=True)
class TransitionView:
    """표시 전용. 공식 국면과 확률에 되먹임하지 않는다."""

    transition_watch: bool
    transition_direction: str
    transition_probability: float
    probability_change_1w: float
    probability_change_4w: float
    rising_weeks_of_four: int
    trigger: str
    supporting_domain_changes: dict[str, float] = field(default_factory=dict)


def _candidate(
    frame: pd.DataFrame, history: pd.DataFrame, as_of: pd.Timestamp, phase: str
) -> tuple[float, float, float, int]:
    """한 인접 국면의 확률과 변화량. 국면이 유지된 구간에서만 차분한다.

    국면이 바뀌면 "인접"의 대상도 바뀐다. 그 경계를 넘어 차분하면 서로 다른 두
    계열을 이어 붙인 값이 된다.
    """

    series = frame[phase]
    index = pd.DatetimeIndex(history.index)
    position = int(index.get_indexer(pd.DatetimeIndex([as_of]))[0])
    current = float(series.iloc[position])
    codes = history["phase_code"].astype(str)
    window_start = position
    while window_start > 0 and codes.iloc[window_start - 1] == codes.iloc[position]:
        window_start -= 1
        if position - window_start >= LOOKBACK_WEEKS:
            break
    available = position - window_start
    change_1w = current - float(series.iloc[position - 1]) if available >= 1 else float("nan")
    change_4w = (
        current - float(series.iloc[position - LOOKBACK_WEEKS])
        if available >= LOOKBACK_WEEKS
        else float("nan")
    )
    rising = 0
    for step in range(1, min(available, LOOKBACK_WEEKS) + 1):
        if float(series.iloc[position - step + 1]) > float(series.iloc[position - step]):
            rising += 1
    return current, change_1w, change_4w, rising


def transition_view(
    history: pd.DataFrame,
    as_of: pd.Timestamp,
    official_code: str,
    domain_changes: dict[str, float] | None = None,
) -> TransitionView:
    """한 주의 전환 감시 상태. 앞뒤 인접 국면을 모두 보고 강한 쪽을 고른다."""

    frame = probability_frame(history)
    forward, backward = adjacent_phases(official_code)
    best: tuple[float, float, float, int, str] | None = None
    for phase in (forward, backward):
        probability, change_1w, change_4w, rising = _candidate(frame, history, as_of, phase)
        score = change_4w if pd.notna(change_4w) else (change_1w if pd.notna(change_1w) else 0.0)
        if best is None or score > best[0]:
            best = (score, probability, change_1w, change_4w, rising, phase)  # type: ignore[assignment]
    assert best is not None
    _, probability, change_1w, change_4w, rising, phase = best  # type: ignore[misc]

    sustained = bool(
        rising >= MINIMUM_RISING_WEEKS
        and pd.notna(change_4w)
        and float(change_4w) >= SUSTAINED_FOUR_WEEK_RISE
    )
    overwhelming = bool(pd.notna(change_1w) and float(change_1w) >= OVERWHELMING_ONE_WEEK_RISE)
    if sustained:
        trigger = "sustained_rise"
    elif overwhelming:
        trigger = "overwhelming_single_observation"
    else:
        trigger = "none"
    return TransitionView(
        transition_watch=bool(sustained or overwhelming),
        transition_direction=str(phase),
        transition_probability=float(probability),
        probability_change_1w=float(change_1w) if pd.notna(change_1w) else float("nan"),
        probability_change_4w=float(change_4w) if pd.notna(change_4w) else float("nan"),
        rising_weeks_of_four=int(rising),
        trigger=trigger,
        supporting_domain_changes=dict(domain_changes or {}),
    )
