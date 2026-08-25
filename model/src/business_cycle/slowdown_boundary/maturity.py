"""성숙도 결함 — "약한 확장"이 "확장기 초반"으로 읽히는 문제.

트랙 18의 확장기 후반 조건 하나가 `level > 0`이다. 그런데 수준이 음인 확장기가 존재하고
(v1.1에서 144주), 그 구간에서는 이 조건이 **구조적으로 켜질 수 없다.** 그래서 성숙도가
0.25~0.33에 묶이고 서술이 "확장기 초반"이 된다. 실제로는 초반이 아니라 약한 확장이다.

경계 수정이 이 결함을 **키운다.** 후퇴기로 흡수되던 애매한 주가 확장기로 돌아오면서
수준이 음인 확장기 주가 늘기 때문이다. 그래서 경계 작업 뒤에 고치라는 순서가 맞다.

## 고치는 방향

절대 부호를 국면 내 상대 위치로 바꾼다. "0보다 큰가"가 아니라 **"이번 확장기가 지나온
수준들에 견주어 높은가"**를 묻는다. 그것이 "아직 이르다"와 "원래 약하다"를 가른다.

기준선은 **인과적 확장 중앙값**이다. 전체 표본 중앙값을 쓰면 그 주가 미래를 보고
자기 위치를 정하게 된다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

#: 상대 기준선을 믿기 전에 필요한 최소 관측. 이보다 적으면 절대 부호로 되돌아간다.
#: 블록 **안에서** 세므로 짧게 잡는다 — v1.1의 확장기 중앙 길이가 10.5주다.
MINIMUM_PRIOR_WEEKS: Final[int] = 8


def causal_block_median(level: pd.Series, phase: pd.Series, name: str) -> pd.Series:
    """그 주 **이전까지, 지금 블록 안에서만** 본 수준의 중앙값.

    국면 전체 역사가 아니라 현재 블록으로 한정하는 것이 핵심이다. 2025~2026년의 약한
    확장기를 2004년이나 2017년의 강한 확장기와 견주면, 약한 확장기의 모든 주가 기준선
    아래가 되어 조건이 다시 구조적으로 막힌다 — 처음 고쳤을 때 실제로 그렇게 됐고,
    144주 중 **0주**가 풀렸다.

    자기 자신은 포함하지 않는다. 포함하면 한 주짜리 블록에서 기준선이 곧 그 주가 되어
    조건이 항상 거짓이 된다.
    """

    values = level.astype(float).tolist()
    names = [str(item) for item in phase.tolist()]
    seen: list[float] = []
    previous = ""
    out: list[float] = []
    for value, current in zip(values, names, strict=True):
        if current != previous:
            seen = []
        if len(seen) >= MINIMUM_PRIOR_WEEKS:
            ordered = sorted(seen)
            middle = len(ordered) // 2
            out.append(
                float(ordered[middle])
                if len(ordered) % 2
                else float((ordered[middle - 1] + ordered[middle]) / 2)
            )
        else:
            out.append(float("nan"))
        if current == name:
            seen.append(float(value))
        previous = current
    return pd.Series(out, index=level.index)


def level_still_high(level: pd.Series, phase: pd.Series) -> pd.Series:
    """확장기 후반의 수준 조건, 고친 판.

    **이번 블록이** 지나온 수준들의 중앙값보다 높으면 참이다. 아직 기준선을 세울 만큼
    보지 못했으면 v1.1과 같은 절대 부호로 돌아간다 — 없는 기준으로 판단하지 않는다.
    """

    reference = causal_block_median(level, phase, "expansion")
    absolute = level.astype(float) > 0
    relative = level.astype(float) > reference
    return relative.where(reference.notna(), absolute)


def compare(level: pd.Series, phase: pd.Series) -> dict[str, Any]:
    """고치기 전과 후. 몇 주가 구조적으로 막혀 있었고 몇 주가 풀리는가."""

    expansion = phase.eq("expansion")
    negative = expansion & level.astype(float).lt(0)
    before = expansion & level.astype(float).gt(0)
    after = expansion & level_still_high(level, phase)
    freed = negative & after
    return {
        "expansion_weeks": int(expansion.sum()),
        "negative_level_expansion_weeks": int(negative.sum()),
        "weeks_where_the_level_condition_fired_before": int(before.sum()),
        "weeks_where_the_level_condition_fires_after": int(after.sum()),
        "negative_level_weeks_the_fix_unblocks": int(freed.sum()),
        "share_of_negative_level_weeks_unblocked": (
            round(float(freed.sum() / negative.sum()), 4) if int(negative.sum()) else None
        ),
        "positive_level_weeks_the_fix_removes": int((before & ~after).sum()),
    }


def patched_conditions(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """트랙 18의 확장기 조건 셋 중 수준 조건만 갈아 끼운 것.

    나머지 둘은 그대로다. 한 번에 하나만 바꿔야 그 하나가 무엇을 했는지 보인다.
    """

    return {
        "level_still_high_for_this_expansion": level_still_high(frame["level"], frame["phase"]),
        "momentum_decelerating": frame["d_momentum"] < 0,
        "breadth_narrowing": frame["d_breadth"] < 0,
    }


#: 서술에서 "약한 확장"을 따로 부르는 이름. 성숙도 단계가 아니라 그 옆에 붙는 표시다.
SUB_NORMAL_LABEL: Final[str] = "정상 이하"


def stage_with_strength(maturity: float, level: float, late_threshold: float) -> dict[str, Any]:
    """성숙도 단계와 **세기 표시**를 따로 낸다.

    상대 수준 조건으로 갈아 끼우는 쪽을 먼저 시험했고, 서술은 고쳐졌지만 트랙 18의
    검증이 무너졌다 — 확장기 후반 적중률이 0.641에서 0.581로 내려가 경과 기간 대조군에
    졌다. `level > 0`이 실제로 예측 일을 하고 있었던 것이다. 강한 확장기를 골라내고,
    후퇴기로 넘어가는 것은 주로 그 강한 확장기이기 때문이다.

    그래서 점수는 그대로 두고 **읽는 층**을 고친다. 결함이 애초에 "약한 확장이 초반으로
    읽힌다"는 서술 문제였으므로, 서술에서 그 둘을 갈라 주면 정확히 해결된다. 예측력을
    깎아 서술을 고치는 것은 교환이 나쁘다.
    """

    stage = "후반" if maturity >= late_threshold else ("중반" if maturity >= 1 / 3 else "초반")
    sub_normal = level < 0
    return {
        "stage": stage,
        "sub_normal": sub_normal,
        "strength": SUB_NORMAL_LABEL if sub_normal else "정상",
        "reads_as_early_but_is_weak": bool(sub_normal and stage == "초반"),
        "wording_prefix": (
            "활동 수준이 정상 범위 아래에 머무는 확장기입니다. " if sub_normal else ""
        ),
    }
