"""유계 소프트 안정화. 잡음은 줄이되 현재 증거를 이기지 못한다.

후보 H의 안정화는 반지름이 작을 때 **인접하지 않은 국면의 확률을 정확히 0으로** 만들었다.
0은 되돌릴 수 없다. 폭 게이트가 침체 쪽을 매주 0으로 만드는 것과 겹치면서 나갈 문이 사라졌고,
모델은 133주 동안 같은 국면에 갇혔다. 관측확률 1위가 다른 국면(0.607)이어도 소용없었다.

여기서는 **유한한 여유(margin)** 하나만 쓴다.

    official(t) = argmax_p [ score_t(p) + margin · 1{p = official(t-1)} ]

성질은 전부 margin이 유한하다는 사실에서 바로 나온다.

* 어떤 국면도 0이 되지 않는다. 모든 국면이 매주 후보로 남는다.
* 직전 국면은 유리할 뿐 거부권이 없다. 다른 국면의 점수가 margin을 넘으면 그 주에 바뀐다.
* 먼 과거는 직전 라벨 하나로만 들어온다. 국면이 한 번 바뀌면 그 이전 이력은 사라진다.
* 분리도는 **보너스를 넣지 않은 점수**로 재므로, 약한 증거가 강한 확신으로 바뀌지 않는다.
* 강제 이탈 타이머가 없다. 싱크를 만드는 구조를 없앴을 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class StabilizerResult:
    official: pd.Series
    raw: pd.Series
    changed_by_filter: pd.Series
    margin: float


def stabilize(scores: pd.DataFrame, margin: float) -> StabilizerResult:
    """주 단위로 여유를 준 argmax. 그 주까지의 정보만 쓴다."""

    if margin < 0:
        raise ValueError("안정화 여유는 음수일 수 없습니다")
    official: list[str] = []
    raw: list[str] = []
    previous: str | None = None
    for _, row in scores.iterrows():
        adjusted = row.copy()
        if previous is not None:
            adjusted[previous] = adjusted[previous] + margin
        winner = str(adjusted.idxmax())
        raw.append(str(row.idxmax()))
        official.append(winner)
        previous = winner
    official_series = pd.Series(official, index=scores.index, name="official_phase")
    raw_series = pd.Series(raw, index=scores.index, name="raw_phase")
    return StabilizerResult(
        official=official_series,
        raw=raw_series,
        changed_by_filter=official_series.ne(raw_series),
        margin=margin,
    )


def escape_weeks(margin: float, score_gap: float) -> float:
    """점수 차이가 일정할 때 국면이 바뀌기까지 걸리는 주. 유한성을 수치로 보인다.

    여유가 유한하므로 답도 유한하다. 차이가 여유보다 크면 **그 주에** 바뀐다.
    작으면 영영 바뀌지 않지만, 그건 증거가 실제로 그만큼 약하다는 뜻이다.
    """

    if score_gap > margin:
        return 1.0
    return float("inf")


def amplification(scores: pd.DataFrame, result: StabilizerResult) -> pd.Series:
    """안정화가 만든 점수 이득. 유계임을 확인할 수 있어야 한다."""

    official = result.official
    gains = [
        float(scores.loc[[week]].to_numpy(dtype=float).max()) - float(str(scores.at[week, name]))
        for week, name in official.items()
    ]
    return pd.Series(gains, index=scores.index, name="filter_gain")


def summary(scores: pd.DataFrame, result: StabilizerResult) -> dict[str, Any]:
    gain = amplification(scores, result)
    return {
        "margin": result.margin,
        "weeks": int(len(scores)),
        "filter_changed_weeks": int(result.changed_by_filter.sum()),
        "filter_changed_share": round(float(result.changed_by_filter.mean()), 6),
        "max_filter_gain": round(float(gain.max()), 6),
        "max_gain_within_margin": bool(gain.max() <= result.margin + 1e-9),
        "minimum_phase_score": round(float(scores.min().min()), 12),
        "any_zero_score": bool((scores <= 0).any().any()),
    }
