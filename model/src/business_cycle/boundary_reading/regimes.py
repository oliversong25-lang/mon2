"""1992년 소비 도메인 단절 양쪽을 갈라 잰다.

확장 역사에서 소비 도메인 구성이 바뀐다 — 1992년 전에는 CMRMTSPL 하나, 그 뒤로는
RRSFS가 더해진다. 그리고 NBER 침체 6회가 그 선 양쪽으로 갈린다.

    1980-01 ~ 1980-07   앞
    1981-07 ~ 1982-11   앞
    1990-07 ~ 1991-03   앞
    2001-03 ~ 2001-11   뒤
    2007-12 ~ 2009-06   뒤
    2020-02 ~ 2020-04   뒤

유의성 개선이 어느 쪽에서 왔는지 갈리지 않으면, 앞쪽이 끌었을 때 그것이 **더 얇은 소비
도메인으로 얻은 결과**라는 것을 알 수 없다. 에피소드 제외와 같은 규율을 구조적 단절에
적용하는 것이다.

## 반쪽은 반쪽이다

가르면 각 반쪽의 후퇴기 블록이 8개 아래로 내려갈 수 있다. 그때는 그 반쪽의 판별력을
"상태의 성질"로 읽으면 안 되고, 그 사실을 숫자와 함께 적는다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..slowdown_boundary.natural import blocks
from .multiplicity import shift_draws

#: 확장 역사가 덮는 NBER 침체 6회. 평가 구간이지 분기 조건이 아니다.
NBER_RECESSIONS: Final[tuple[tuple[str, str], ...]] = (
    ("1980-01-01", "1980-07-31"),
    ("1981-07-01", "1982-11-30"),
    ("1990-07-01", "1991-03-31"),
    ("2001-03-01", "2001-11-30"),
    ("2007-12-01", "2009-06-30"),
    ("2020-02-01", "2020-04-30"),
)

#: RRSFS가 들어오는 시점. 이 앞은 소비 도메인이 CMRMTSPL 하나로 선다.
SPLIT: Final[str] = "1992-01-01"

#: 반쪽이 "상태"를 말할 수 있는 최소 블록 수. 확장 역사 전체에 쓴 것과 같은 기준.
MINIMUM_BLOCKS: Final[int] = 8

#: 판별력이 1.0에서 이만큼 안이면 우연과 구분되지 않는 것으로 적는다. 1.015 같은 값을
#: "우연 위"라고 쓰면 숫자는 맞아도 말이 틀린다.
CHANCE_MARGIN: Final[float] = 0.1


def recessions_in(first: str, last: str) -> list[str]:
    """이 구간에 시작하는 NBER 침체. 어느 반쪽이 몇 번을 갖는지 세려고 쓴다."""

    return [start[:7] for start, _ in NBER_RECESSIONS if first <= start <= last]


def half(
    phase: pd.Series, relative: pd.DataFrame, first: str, last: str, name: str
) -> dict[str, Any]:
    """한 반쪽의 후퇴기 판별력. 이동 검정도 그 반쪽 안에서만 돈다."""

    weeks = [week for week in phase.index if first <= str(week) <= last]
    window = phase.loc[weeks]
    panel = relative.loc[weeks]
    spans = [
        span for span in blocks(window) if span["phase"] == "slowdown" and span["next"] is not None
    ]
    progressed = sum(1 for span in spans if span["next"] == "contraction")
    draws = shift_draws(window, panel)
    count = len(spans)
    return {
        "half": name,
        "first_week": str(weeks[0]) if weeks else None,
        "last_week": str(weeks[-1]) if weeks else None,
        "weeks": len(weeks),
        "consumption_domain": ("CMRMTSPL 단독" if name == "pre_1992" else "CMRMTSPL + RRSFS"),
        "nber_recessions": recessions_in(first, last),
        "slowdown_weeks": int((window == "slowdown").sum()),
        "slowdown_share": round(float((window == "slowdown").mean()), 4),
        "slowdown_blocks": count,
        "progressed_to_contraction": progressed,
        "progression_rate": round(progressed / count, 3) if count else None,
        "discrimination": draws["ratio_to_chance"],
        "p_value": draws["nominal_p"],
        "enough_blocks_to_call_it_a_state": count >= MINIMUM_BLOCKS,
    }


def split(phase: pd.Series, relative: pd.DataFrame) -> dict[str, Any]:
    """양쪽 반쪽과, 어느 쪽이 전체 결과를 끌었는지에 대한 읽기."""

    weeks = [str(week) for week in phase.index]
    pre = half(phase, relative, weeks[0], SPLIT, "pre_1992")
    post = half(phase, relative, SPLIT, weeks[-1], "post_1992")
    whole = shift_draws(phase, relative)

    values = [
        (entry["half"], entry["discrimination"])
        for entry in (pre, post)
        if entry["discrimination"] is not None
    ]
    stronger = max(values, key=lambda item: float(item[1]))[0] if values else None
    weaker = min(values, key=lambda item: float(item[1]))[0] if values else None
    thin = [entry["half"] for entry in (pre, post) if not entry["enough_blocks_to_call_it_a_state"]]

    # 반쪽이 전체보다 강하면 그 반쪽이 결과를 끈 것이고, 다른 반쪽은 희석한 것이다.
    total = float(whole["ratio_to_chance"] or 0.0)
    carrying = [
        entry["half"]
        for entry in (pre, post)
        if entry["discrimination"] is not None and float(entry["discrimination"]) >= total
    ]
    at_chance = [
        entry["half"]
        for entry in (pre, post)
        if entry["discrimination"] is not None
        and abs(float(entry["discrimination"]) - 1.0) <= CHANCE_MARGIN
    ]
    thin_domain_drove_it = "pre_1992" in carrying and "post_1992" not in carrying

    return {
        "split_at": SPLIT,
        "why_here": (
            "RRSFS가 이 시점부터라 소비 도메인 구성이 바뀐다. 앞쪽은 CMRMTSPL 하나로 "
            "서므로 **더 얇은 소비 도메인**으로 얻은 결과다."
        ),
        "whole": {
            "weeks": len(weeks),
            "discrimination": whole["ratio_to_chance"],
            "p_value": whole["nominal_p"],
        },
        "halves": [pre, post],
        "stronger_half": stronger,
        "weaker_half": weaker,
        "carrying_halves": carrying,
        "halves_at_chance": at_chance,
        "thinner_consumption_domain_drove_the_result": thin_domain_drove_it,
        "halves_too_thin_to_call_a_state": thin,
        "reading": (
            (
                "**더 얇은 소비 도메인 쪽이 끈 것이 아니다.** 유의성은 post_1992에 있고, "
                "pre_1992는 우연과 구분되지 않는다."
                if not thin_domain_drove_it and "pre_1992" in at_chance
                else (
                    "**pre_1992가 끌었다.** 더 얇은 소비 도메인(CMRMTSPL 단독)으로 얻은 "
                    "결과이므로 뒤쪽과 같은 물건이라고 말할 수 없다."
                    if thin_domain_drove_it
                    else "양쪽이 같은 방향을 가리킨다. 전체 결과가 한쪽 구성에만 기대고 "
                    "있지는 않다."
                )
            )
            + (
                f" 그런데 그 방향은 확장 역사가 벌어 준 것이 아니다 — post_1992만 따로 "
                f"보면 판별력이 전체({total})보다 높고, pre_1992 {pre['weeks']}주는 "
                "후퇴기 블록을 거의 더하지 못한 채 희석만 했다. "
                "**'50년 역사에서 유의'가 아니라 '1992년 이후 구간에서 유의'다.**"
                if "post_1992" in carrying and "pre_1992" not in carrying
                else ""
            )
            + (
                f" {', '.join(thin)}의 후퇴기 블록이 {MINIMUM_BLOCKS}개 아래라 그 반쪽의 "
                "숫자는 상태의 성질이 아니라 몇몇 구간의 성질이다."
                if thin
                else " 양쪽 다 블록 수가 상태를 말할 만큼 있다."
            )
        ),
    }
