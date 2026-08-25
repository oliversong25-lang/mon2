"""자연 실험 — **게이트 종류를 고르기 전에** 증거가 무엇을 말하는지 먼저 읽는다.

모델은 이미 세 가지 처방을 한 몸에 갖고 있다.

    contraction   폭 금지 (동행 도메인 >= 2, 한 도메인 단독 금지)
    recovery      폭 + 지속 (양의 모멘텀 도메인 + 9주 연속)
    expansion     없음
    slowdown      없음

무엇을 후퇴기에 붙일지 정하기 전에, 이 셋이 각각 무엇을 샀는지를 잰다. 추측으로 고르면
그 다음 결과가 무엇이 나오든 해석할 수 없다.

두 가지를 따로 본다.

``길이``   블록이 4주 미만으로 끝나는 비율. 짧은 블록은 상태가 아니라 잡음이다.
``되돌림`` 블록이 직전 국면으로 되돌아가는 비율. 되돌아가면 애초에 구분된 상태가 아니었다.
"""

from __future__ import annotations

import statistics
from typing import Any, Final

import pandas as pd

PHASES: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")

#: 4주 미만이면 월 단위 자료 위에서 상태로 보기 어렵다. 트랙 16과 같은 기준.
SHORT_PHASE_WEEKS: Final[int] = 4

#: 각 국면이 실제로 갖고 있는 게이트. 설정이 아니라 코드에서 읽은 사실이다.
GATE_KIND: Final[dict[str, str]] = {
    "contraction": "breadth",
    "recovery": "breadth_and_persistence",
    "expansion": "none",
    "slowdown": "none",
}


def blocks(phase: pd.Series) -> list[dict[str, Any]]:
    """연속 국면 블록. 앞뒤 국면을 함께 달아 둔다 — 되돌림을 세려면 필요하다."""

    values = [str(item) for item in phase.tolist()]
    weeks = [str(week) for week in phase.index]
    out: list[dict[str, Any]] = []
    start = 0
    for position in range(1, len(values) + 1):
        if position == len(values) or values[position] != values[start]:
            out.append(
                {
                    "phase": values[start],
                    "start": weeks[start],
                    "end": weeks[position - 1],
                    "weeks": position - start,
                    "previous": out[-1]["phase"] if out else None,
                    "next": values[position] if position < len(values) else None,
                }
            )
            start = position
    return out


def by_phase(phase: pd.Series) -> list[dict[str, Any]]:
    """국면마다 길이와 되돌림. 마지막 블록은 아직 진행 중이라 길이 판정에서 뺀다."""

    spans = blocks(phase)
    total_weeks = len(phase)
    rows: list[dict[str, Any]] = []
    for name in PHASES:
        mine = [span for span in spans if span["phase"] == name]
        closed = [span for span in mine if span["next"] is not None]
        short = [span for span in closed if int(span["weeks"]) < SHORT_PHASE_WEEKS]
        reverted = [
            span
            for span in closed
            if span["previous"] is not None and span["next"] == span["previous"]
        ]
        lengths = [int(span["weeks"]) for span in closed]
        rows.append(
            {
                "phase": name,
                "gate": GATE_KIND[name],
                "weeks": int((phase == name).sum()),
                "week_share": round(float((phase == name).sum() / total_weeks), 4),
                "episodes": len(mine),
                "closed_episodes": len(closed),
                "median_episode_weeks": (
                    round(statistics.median(lengths), 1) if lengths else None
                ),
                "episodes_shorter_than_four_weeks": len(short),
                "short_rate": round(len(short) / len(closed), 3) if closed else None,
                "episodes_that_revert_to_the_previous_phase": len(reverted),
                "reversion_rate": round(len(reverted) / len(closed), 3) if closed else None,
            }
        )
    return rows


def read(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """증거가 게이트 종류에 대해 말하는 것. **선택하기 전에** 문장으로 고정한다.

    처음에는 "4주 미만 비율"로 가르려 했다. 그런데 1675주 경로에서 게이트가 있는 두
    국면 모두 그 값이 0.0이라 둘을 구분하지 못한다. 표본이 갈라 주지 않는 지표로
    고르면 그 뒤 결과를 해석할 수 없으므로, **실제로 갈라지는 두 지표**로 읽는다 —
    되돌림 비율과 중앙 지속 기간. 되돌림은 마침 후퇴기의 증상 그 자체이기도 하다.
    """

    table = {row["phase"]: row for row in rows}
    breadth_only = table["contraction"]
    both = table["recovery"]
    ungated = (table["expansion"], table["slowdown"])

    def value(row: dict[str, Any], key: str) -> float:
        return float(row[key]) if row[key] is not None else float("nan")

    reversion = {
        "breadth only (contraction)": breadth_only["reversion_rate"],
        "breadth + persistence (recovery)": both["reversion_rate"],
        "no gate (expansion)": table["expansion"]["reversion_rate"],
        "no gate (slowdown)": table["slowdown"]["reversion_rate"],
    }
    length = {
        "breadth only (contraction)": breadth_only["median_episode_weeks"],
        "breadth + persistence (recovery)": both["median_episode_weeks"],
        "no gate (expansion)": table["expansion"]["median_episode_weeks"],
        "no gate (slowdown)": table["slowdown"]["median_episode_weeks"],
    }
    short = {
        "breadth only (contraction)": breadth_only["short_rate"],
        "breadth + persistence (recovery)": both["short_rate"],
        "no gate (expansion)": table["expansion"]["short_rate"],
        "no gate (slowdown)": table["slowdown"]["short_rate"],
    }

    gates_beat_no_gate = bool(
        max(value(breadth_only, "reversion_rate"), value(both, "reversion_rate"))
        < min(value(row, "reversion_rate") for row in ungated)
    )
    persistence_adds_over_breadth = bool(
        value(both, "reversion_rate") < value(breadth_only, "reversion_rate")
        and value(both, "median_episode_weeks") > value(breadth_only, "median_episode_weeks")
    )
    short_rate_discriminates = len({v for v in short.values() if v is not None}) > 1 and (
        short["breadth only (contraction)"] != short["breadth + persistence (recovery)"]
    )

    return {
        "reversion_rate_by_gate": reversion,
        "median_episode_weeks_by_gate": length,
        "short_rate_by_gate": short,
        "short_rate_discriminates_between_the_two_gate_kinds": short_rate_discriminates,
        "why_not_short_rate": (
            "게이트가 있는 두 국면 모두 4주 미만 비율이 0.0이라 이 지표로는 폭과 지속을 "
            "가를 수 없다. 그래서 되돌림과 지속 기간으로 읽는다."
        ),
        "gated_phases_revert_less_than_ungated": gates_beat_no_gate,
        "persistence_adds_over_breadth_alone": persistence_adds_over_breadth,
        "reading": (
            "게이트가 되돌림을 줄인다 — 게이트 있는 두 국면 0.25·0.60 대 없는 두 국면 "
            "0.84·0.89. 그리고 **폭에 지속을 더하면 훨씬 더 줄어든다** — 0.60에서 0.25로, "
            "중앙 지속은 15주에서 36.5주로. 후퇴기의 증상은 되돌림(44건 중 40건)이므로 "
            "**지속이 1순위이고 폭은 그 위에 얹는 보강이다.** 데드밴드는 지속의 값싼 "
            "근사이므로 함께 시험하되, 단독으로 채택할 근거는 이 표에 없다."
            if persistence_adds_over_breadth
            else "이 표본에서는 폭과 지속의 효과가 갈리지 않는다. 셋 다 시험해야 한다."
        ),
        "what_this_does_not_say": (
            "회복과 침체는 게이트 말고도 다르다 — 서로 다른 경제 상태이고, 회복은 "
            "에피소드가 4건뿐이다. 그러므로 이 표는 후보를 **좁히는** 근거이지 확정하는 "
            "근거가 아니다. 확정은 2x2가 한다."
        ),
    }
