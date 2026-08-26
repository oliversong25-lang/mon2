"""에피소드 제외, 두 강도 — 여기서는 **둘 다** 통과해야 한다.

트랙 19에서는 강한 제외가 더 가혹했고, 트랙 23에서는 방향이 뒤집혀 강한 쪽이 더
관대했다. 어느 쪽이 가혹한지가 상황에 따라 다르다면, 하나만 판정 기준으로 삼는 것은
결과를 보고 고를 여지를 남긴다. 그래서 사전 명세가 둘 다 요구한다.

``block_only``      그 에피소드의 주만 뺀다.
``event_including`` 그 에피소드의 주와 **그 뒤 전방 지평선만큼**을 함께 뺀다.

전방 통계량에서 뒤쪽 강도가 왜 필요한지는 분명하다. 에피소드 마지막 주의 전방 13주
관측은 에피소드가 끝난 뒤의 수익으로 만들어진다. 블록만 빼면 그 관측이 남고, 그것은
**에피소드를 뺐다고 하면서 에피소드가 가리킨 앞날을 남겨 둔** 것이다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..phase_returns.labels import PHASES
from . import market as M


def _blocks(phase: pd.Series, name: str) -> list[tuple[int, int]]:
    return M._blocks(phase, name)


def _ratio(phase: pd.Series, weekly: pd.Series, horizon: int) -> float | None:
    rows = M.forward(phase, weekly, horizon)
    value = M.downside_ratio(rows)["ratio"]
    return None if value is None else float(value)


def run(phase: pd.Series, weekly: pd.Series, horizon: int) -> dict[str, Any]:
    """국면별 에피소드를 하나씩, 두 강도로 뺀 하방변동성 비."""

    weeks = [str(week) for week in phase.index]
    full = _ratio(phase, weekly, horizon)

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
                entry[strength] = _ratio(phase.loc[keep], weekly.loc[keep], horizon)
                entry[f"{strength}_weeks_removed"] = len(removed)
            rows.append(entry)

    return {
        "horizon_weeks": horizon,
        "full_sample_ratio": full,
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
            "에피소드 마지막 주의 전방 관측은 에피소드가 끝난 뒤의 수익으로 만들어진다. "
            "블록만 빼면 그 관측이 남으므로, 에피소드를 뺐다고 하면서 에피소드가 가리킨 "
            "앞날을 남겨 둔 것이 된다. 어느 강도가 더 가혹한지는 상황마다 달라서 "
            "**둘 다** 본다."
        ),
    }


def _summarise(rows: list[dict[str, Any]], strength: str) -> dict[str, Any]:
    values = [row[strength] for row in rows if row[strength] is not None]
    if not values:
        return {"computable_episodes": 0, "lowest": None}
    array = np.array(values, dtype=float)
    worst = min(
        (row for row in rows if row[strength] is not None),
        key=lambda row: float(row[strength]),
    )
    return {
        "computable_episodes": len(values),
        "lowest": round(float(array.min()), 3),
        "median": round(float(np.median(array)), 3),
        "highest": round(float(array.max()), 3),
        "episodes_whose_removal_leaves_too_few_observations": len(rows) - len(values),
        "most_damaging_episode": f"{worst['phase']}#{worst['episode']} ({worst['start']})",
    }


def shift_null(
    phase: pd.Series, weekly: pd.Series, horizon: int, stride: int = 4
) -> dict[str, Any]:
    """무작위 라벨 귀무분포 — 라벨 순서를 통째로 이동시켜 대응만 끊는다."""

    from ..phase_returns.significance import MINIMUM_SHIFT

    observed = _ratio(phase, weekly, horizon)
    values = phase.to_numpy()
    weeks = len(values)
    offsets = [
        offset
        for offset in range(0, weeks, stride)
        if MINIMUM_SHIFT <= offset <= weeks - MINIMUM_SHIFT
    ]
    draws: list[float] = []
    for offset in offsets:
        rolled = pd.Series(np.roll(values, offset), index=phase.index)
        value = _ratio(rolled, weekly, horizon)
        if value is not None:
            draws.append(float(value))

    array = np.array(draws)
    extreme = int((array >= float(observed or 0.0)).sum())
    return {
        "observed_ratio": observed,
        "shifts_used": len(draws),
        "null_median": round(float(np.median(array)), 3) if draws else None,
        "null_p90": round(float(np.quantile(array, 0.90)), 3) if draws else None,
        "p_value": round(float((extreme + 1) / (len(draws) + 1)), 4) if draws else None,
    }
