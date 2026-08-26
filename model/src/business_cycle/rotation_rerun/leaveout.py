"""에피소드를 하나씩 뺀다 — **두 강도로.**

트랙 19가 보인 것: 블록만 빼는 것과 전방 창까지 함께 빼는 것이 긴 지평선에서 반대 답을
준다. 순환매는 주간 복리라 전방 창이 없는 대신, 다른 자리에서 같은 문제가 생긴다.

``block_only``
    그 에피소드의 주만 뺀다. 확장 창 추정에는 남아 있던 다른 국면 주들이 그대로 쓰이고,
    비중 산정 이력이 이어진다. **약한 제외**다.

``event_including``
    그 에피소드의 주를 빼고, **그 뒤 전방 지평선만큼도 함께 뺀다.** 에피소드가 만든
    비중이 그 다음 몇 주의 수익을 받는 자리까지 지우는 것이다. 트랙 19의 판정 기준이
    이쪽이었고 여기서도 이쪽이 판정한다.

## 왜 약한 쪽이 낙관적인가

에피소드 안에서 정해진 비중이 에피소드 밖 첫 몇 주의 수익을 받는다. 블록만 빼면 그
수익이 남는다. 즉 **에피소드를 뺐다고 하면서 에피소드가 번 것을 남겨 둔** 셈이다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns import rotation as R
from ..phase_returns.labels import PHASES

#: 사건 포함 제외에서 에피소드 뒤로 함께 지우는 주 수. 트랙 17의 가장 긴 지평선이다.
FORWARD_WINDOW_WEEKS: Final[int] = 26


def _blocks(phase: pd.Series, name: str) -> list[tuple[int, int]]:
    values = [str(item) for item in phase.tolist()]
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for position, value in enumerate(values):
        if value == name and start is None:
            start = position
        elif value != name and start is not None:
            spans.append((start, position - 1))
            start = None
    if start is not None:
        spans.append((start, len(values) - 1))
    return spans


def _excess(phase: pd.Series, weekly: pd.DataFrame, minimum: int) -> float | None:
    """이 표본에서 순환매가 동일가중을 얼마나 이기는가. 연율."""

    relative = R.weekly_relative(weekly)
    usable = relative.dropna(how="any")
    if len(usable) < minimum + 2:
        return None
    aligned = phase.reindex(usable.index).fillna("").astype(str).to_numpy()
    values = usable.to_numpy(dtype=float)
    realised = R._realise(R._expanding_weights(aligned, values, R.TOP_K, minimum), values)
    equal = values.mean(axis=1)
    return round(R._annualise(realised) - R._annualise(equal), 4)


def run(
    phase: pd.Series, weekly: pd.DataFrame, minimum: int, forward: int = FORWARD_WINDOW_WEEKS
) -> dict[str, Any]:
    """국면별 에피소드를 하나씩, 두 강도로 뺀 초과수익."""

    weeks = [str(week) for week in phase.index]
    full = _excess(phase, weekly, minimum)

    rows: list[dict[str, Any]] = []
    for name in PHASES:
        for number, (start, end) in enumerate(_blocks(phase, name), start=1):
            block = set(weeks[start : end + 1])
            widened = set(weeks[start : min(end + 1 + forward, len(weeks))])
            entry: dict[str, Any] = {
                "phase": name,
                "episode": number,
                "start": weeks[start],
                "end": weeks[end],
                "weeks": end - start + 1,
            }
            for strength, removed in (("block_only", block), ("event_including", widened)):
                keep = [week for week in weeks if week not in removed]
                entry[strength] = _excess(phase.loc[keep], weekly.reindex(keep), minimum)
                entry[f"{strength}_weeks_removed"] = len(removed)
            rows.append(entry)

    return {
        "full_sample_excess": full,
        "forward_window_weeks": forward,
        "episodes": len(rows),
        "episodes_by_phase": {
            name: sum(1 for row in rows if row["phase"] == name) for name in PHASES
        },
        "rows": rows,
        **{
            f"{strength}_summary": _summarise(rows, strength, full)
            for strength in ("block_only", "event_including")
        },
        "deciding_strength": "event_including",
        "why": (
            "에피소드 안에서 정해진 비중이 에피소드 밖 첫 몇 주의 수익을 받는다. 블록만 "
            "빼면 그 수익이 남으므로, 에피소드를 뺐다고 하면서 에피소드가 번 것을 남겨 "
            "둔 셈이 된다. 트랙 19가 같은 이유로 사건 포함 쪽을 판정 기준으로 삼았다."
        ),
    }


def _summarise(rows: list[dict[str, Any]], strength: str, full: float | None) -> dict[str, Any]:
    values = [row[strength] for row in rows if row[strength] is not None]
    if not values:
        return {"computable_episodes": 0}
    array = np.array(values, dtype=float)
    flips = [
        row
        for row in rows
        if row[strength] is not None and full is not None and float(row[strength]) * float(full) < 0
    ]
    worst = min(rows, key=lambda row: float(row[strength] or np.inf))
    return {
        "computable_episodes": len(values),
        "range_low": round(float(array.min()), 4),
        "range_high": round(float(array.max()), 4),
        "median": round(float(np.median(array)), 4),
        "episodes_that_flip_the_sign": len(flips),
        "which_flip": [f"{row['phase']}#{row['episode']} ({row['start']})" for row in flips],
        "stays_positive_everywhere": bool(array.min() > 0),
        "most_damaging_episode": f"{worst['phase']}#{worst['episode']} ({worst['start']})",
    }
