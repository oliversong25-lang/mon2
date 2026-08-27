"""에피소드 제외, 두 강도 — 증분이 부호를 유지하는가.

트랙 24와 같은 두 강도다. 다만 여기서는 통계량이 하방변동성 비가 아니라 **증분
결정계수**이고, 그것이 사전 명세가 고른 판정 통계량이다.

강한 제외가 왜 필요한지는 여기서도 같다. 에피소드 마지막 주의 전방 13주 목표는
에피소드가 끝난 뒤의 수익으로 만들어진다. 블록만 빼면 그 관측이 남는다.

한 가지가 다르다. **대조도 함께 잘린다.** 실현분산은 그 주까지의 과거로 계산되므로,
주를 빼면 남은 주의 대조값은 그대로이되 회귀 표본만 줄어든다. 대조를 다시 계산하지
않는 것이 옳다 — 그때 그 시점에서 실제로 알 수 있었던 값이기 때문이다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..phase_returns.labels import PHASES
from . import control as C


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


def _increment(
    phase: pd.Series, control: pd.DataFrame, target: pd.Series, stride: int
) -> float | None:
    read = C.compare(phase, control, target, "leave-one-out", stride=stride)
    if not read.get("usable"):
        return None
    return float(read["incremental_r_squared_of_phase"])


def run(
    phase: pd.Series,
    control: pd.DataFrame,
    target: pd.Series,
    horizon: int,
    stride: int = 8,
) -> dict[str, Any]:
    """국면별 에피소드를 하나씩, 두 강도로 뺀 증분.

    ``stride``가 본 계산보다 성기다. 에피소드마다 이동 분포를 다시 만들어야 해서
    비용이 곱해지고, 여기서 필요한 것은 p가 아니라 **증분의 부호**이기 때문이다.
    """

    weeks = [str(week) for week in phase.index]
    full = _increment(phase, control, target, stride)

    rows: list[dict[str, Any]] = []
    for name in PHASES:
        for number, (start, end) in enumerate(_blocks(phase, name), start=1):
            block = set(weeks[start : end + 1])
            widened = set(weeks[start : min(end + 1 + horizon, len(weeks))])
            entry: dict[str, Any] = {
                "phase": name,
                "episode": number,
                "start": weeks[start],
                "end": weeks[end],
                "weeks": end - start + 1,
            }
            for strength, removed in (("block_only", block), ("event_including", widened)):
                keep = [week for week in weeks if week not in removed]
                entry[strength] = _increment(
                    phase.loc[keep], control.loc[keep], target.loc[keep], stride
                )
                entry[f"{strength}_weeks_removed"] = len(removed)
            rows.append(entry)

    return {
        "horizon_weeks": horizon,
        "full_sample_increment": full,
        "episodes": len(rows),
        "episodes_by_phase": {
            name: sum(1 for row in rows if row["phase"] == name) for name in PHASES
        },
        "rows": rows,
        **{
            f"{strength}_summary": _summarise(rows, strength)
            for strength in ("block_only", "event_including")
        },
        "both_strengths_must_hold": True,
        "why": (
            "에피소드 마지막 주의 전방 목표는 에피소드가 끝난 뒤의 수익으로 만들어진다. "
            "블록만 빼면 그 관측이 남는다. 어느 강도가 더 가혹한지는 상황마다 달라서 "
            "**둘 다** 본다 — 트랙 19에서는 강한 쪽이, 트랙 23에서는 약한 쪽이 가혹했다."
        ),
    }


def _summarise(rows: list[dict[str, Any]], strength: str) -> dict[str, Any]:
    values = [row[strength] for row in rows if row[strength] is not None]
    if not values:
        return {"computable_episodes": 0, "lowest": None}
    array = np.array(values, dtype=float)
    worst = min(
        (row for row in rows if row[strength] is not None), key=lambda row: float(row[strength])
    )
    return {
        "computable_episodes": len(values),
        "lowest": round(float(array.min()), 6),
        "median": round(float(np.median(array)), 6),
        "highest": round(float(array.max()), 6),
        "stays_positive_everywhere": bool(array.min() > 0.0),
        "most_damaging_episode": f"{worst['phase']}#{worst['episode']} ({worst['start']})",
    }
