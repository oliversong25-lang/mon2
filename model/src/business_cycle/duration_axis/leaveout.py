"""에피소드 제외, 두 강도 — 그리고 **2022년을 따로.**

트랙 17은 2020년을 빼자 무너졌고 트랙 25는 GFC 없이 효과의 3분의 2를 잃었다. 이 트랙의
명백한 후보는 2022년이다 — 제안된 메커니즘이 바로 그 해를 가리켰기 때문에, 그 해가
결과를 혼자 만들고 있는지가 특히 중요하다.

통계량은 국면 증분이고, 천장 관문이 막혔으므로 **기록 전용**이다.
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
    read = C.compare(phase, control, target, "leave-one-out", stride)
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
    """국면별 에피소드를 하나씩, 두 강도로."""

    months = [str(month) for month in phase.index]
    full = _increment(phase, control, target, stride)

    rows: list[dict[str, Any]] = []
    for name in PHASES:
        for number, (start, end) in enumerate(_blocks(phase, name), start=1):
            block = set(months[start : end + 1])
            widened = set(months[start : min(end + 1 + horizon, len(months))])
            entry: dict[str, Any] = {
                "phase": name,
                "episode": number,
                "start": months[start],
                "end": months[end],
                "months": end - start + 1,
            }
            for strength, removed in (("block_only", block), ("event_including", widened)):
                keep = [month for month in months if month not in removed]
                entry[strength] = _increment(
                    phase.loc[keep], control.loc[keep], target.loc[keep], stride
                )
                entry[f"{strength}_months_removed"] = len(removed)
            rows.append(entry)

    return {
        "horizon_months": horizon,
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
        "record_only": True,
        "why_record_only": (
            "천장 관문이 막혔으므로 이 숫자들은 판정하지 않는다. 천장 아래에서 무엇이 "
            "나오든 그것은 천장을 넘을 수 없다."
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
