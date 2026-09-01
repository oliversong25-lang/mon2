"""에피소드를 하나씩 빼 본다. **사후 점검이 아니라 기본 절차다.**

Track 17에서는 모든 결과가 2020년 한 해를 빼자 무너졌다. 그 뒤로 머릿수치를 하나
적을 때마다 "이 수치가 어느 한 구간에 얹혀 있는가"를 같이 적는다.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..phase_returns.labels import PHASES
from .conditional import by_phase, episodes_of


def _without(index: pd.Index, span: tuple[int, int]) -> list[str]:
    """구간을 뺀 나머지 주 이름. 위치가 아니라 이름으로 다뤄야 세 계열이 같이 잘린다."""

    lower, upper = span
    return [
        str(index[position]) for position in range(len(index)) if not lower <= position <= upper
    ]


def phase_means(phase: pd.Series, forward: pd.Series) -> dict[str, Any]:
    """국면 평균의 에피소드 제외 범위."""

    blocks = episodes_of(phase)
    full = {row["phase"]: row["mean_forward_value_return"] for row in by_phase(phase, forward)}

    out: dict[str, Any] = {}
    for name in PHASES:
        values: list[dict[str, Any]] = []
        for number, span in enumerate(blocks[name], start=1):
            keep = _without(phase.index, span)
            trimmed_phase = phase.loc[keep]
            trimmed_forward = forward.loc[keep]
            recomputed = {
                row["phase"]: row["mean_forward_value_return"]
                for row in by_phase(trimmed_phase, trimmed_forward)
            }
            values.append(
                {
                    "episode": number,
                    "start": str(phase.index[span[0]]),
                    "end": str(phase.index[span[1]]),
                    "weeks": span[1] - span[0] + 1,
                    "mean_without_it": recomputed[name],
                }
            )
        present = [row["mean_without_it"] for row in values if row["mean_without_it"] is not None]
        uncomputable = [row for row in values if row["mean_without_it"] is None]
        full_value = full[name]
        out[name] = {
            "episodes": len(blocks[name]),
            "full_sample_mean": full_value,
            "range_low": min(present) if present else None,
            "range_high": max(present) if present else None,
            "episodes_whose_removal_leaves_too_few_observations": len(uncomputable),
            "removing_one_episode_makes_this_phase_uncomputable": bool(uncomputable),
            "sign_flips_when_any_single_episode_is_removed": bool(
                full_value is not None
                and present
                and ((full_value > 0 and min(present) < 0) or (full_value < 0 and max(present) > 0))
            ),
            "rows": values,
        }
    return out


def incremental_r_squared(
    phase: pd.Series, forward: pd.Series, rates: pd.DataFrame, runner: Any
) -> dict[str, Any]:
    """국면의 증분 결정계수가 어느 에피소드에 얹혀 있는가."""

    blocks = episodes_of(phase)
    full = runner(phase, forward, rates)
    if not full.get("usable"):
        return {"usable": False}

    rows: list[dict[str, Any]] = []
    for name in PHASES:
        for number, span in enumerate(blocks[name], start=1):
            keep = _without(phase.index, span)
            result = runner(phase.loc[keep], forward.loc[keep], rates.loc[keep])
            if not result.get("usable"):
                continue
            rows.append(
                {
                    "phase": name,
                    "episode": number,
                    "start": str(phase.index[span[0]]),
                    "end": str(phase.index[span[1]]),
                    "weeks": span[1] - span[0] + 1,
                    "incremental_r_squared_without_it": result[
                        "incremental_r_squared_of_phase_over_the_term_spread"
                    ],
                    "p_value_without_it": result["incremental_r_squared_p_value"],
                    "still_adds_something": result["phase_adds_something_beyond_the_term_spread"],
                }
            )

    values = [row["incremental_r_squared_without_it"] for row in rows]
    survives = [row["still_adds_something"] for row in rows]
    return {
        "usable": True,
        "full_sample": full["incremental_r_squared_of_phase_over_the_term_spread"],
        "full_sample_p": full["incremental_r_squared_p_value"],
        "range_low": round(min(values), 5) if values else None,
        "range_high": round(max(values), 5) if values else None,
        "episodes_tested": len(rows),
        "episodes_where_it_still_adds_something": int(sum(survives)),
        "collapses_when_some_single_episode_is_removed": bool(
            full["phase_adds_something_beyond_the_term_spread"] and not all(survives)
        ),
        "rows": rows,
    }


#: 거시 사건 창. 국면 블록 하나를 빼는 것만으로는 부족하다 — 이웃 주의 **전방창**이
#: 여전히 같은 사건을 덮기 때문이다. 그래서 사건 전체를 덮는 주를 통째로 뺀다.
MACRO_WINDOWS: dict[str, tuple[str, str]] = {
    "dot-com bust (2000-03 ~ 2002-12)": ("2000-03-01", "2002-12-31"),
    "global financial crisis (2007-07 ~ 2009-12)": ("2007-07-01", "2009-12-31"),
    "covid (2020-01 ~ 2021-12)": ("2020-01-01", "2021-12-31"),
}


def _weeks_touching(index: pd.Index, start: str, end: str, horizon: int) -> list[str]:
    """라벨 주 자신이든 그 전방창이든 사건 창에 닿는 주."""

    stamps = pd.to_datetime(pd.Series([str(week) for week in index]))
    lower = pd.Timestamp(start)
    upper = pd.Timestamp(end)
    span = pd.Timedelta(weeks=horizon)
    touching = (stamps <= upper) & (stamps + span >= lower)
    return [str(index[position]) for position in range(len(index)) if bool(touching.iloc[position])]


def leave_one_macro_window_out(
    phase: pd.Series,
    forward: pd.Series,
    rates: pd.DataFrame,
    runner: Any,
    horizon: int,
) -> list[dict[str, Any]]:
    """사건 하나를 **전방창까지 포함해** 통째로 빼면 무엇이 남는가."""

    rows: list[dict[str, Any]] = []
    for label, (start, end) in MACRO_WINDOWS.items():
        dropped = set(_weeks_touching(phase.index, start, end, horizon))
        kept = [week for week in phase.index if str(week) not in dropped]
        if len(kept) < 60:
            rows.append(
                {
                    "window_removed": label,
                    "weeks_removed": len(dropped),
                    "weeks_left": len(kept),
                    "usable": False,
                    "reason": "남는 주가 너무 적다",
                }
            )
            continue
        trimmed_phase = phase.loc[kept]
        trimmed_forward = forward.loc[kept]
        counts = {name: int(trimmed_phase.eq(name).sum()) for name in PHASES}
        result = runner(trimmed_phase, trimmed_forward, rates.loc[kept])
        means = {
            row["phase"]: row["mean_forward_value_return"]
            for row in by_phase(trimmed_phase, trimmed_forward)
        }
        rows.append(
            {
                "window_removed": label,
                "weeks_removed": len(dropped),
                "weeks_left": len(kept),
                "usable": bool(result.get("usable")),
                "phase_weeks_left": counts,
                "phases_left_with_no_weeks": [name for name, value in counts.items() if value == 0],
                "phase_means_without_it": means,
                "incremental_r_squared_without_it": result.get(
                    "incremental_r_squared_of_phase_over_the_term_spread"
                ),
                "p_value_without_it": result.get("incremental_r_squared_p_value"),
                "still_adds_something": result.get("phase_adds_something_beyond_the_term_spread"),
            }
        )
    return rows
