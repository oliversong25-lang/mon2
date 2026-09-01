"""통과 조건을 **결과를 보기 전에** 적는다.

트랙 20에서 자료를 내려받기 전에 판정 규칙을 커밋했던 것과 같은 절차다. 여기서는 그
절차가 그때보다 더 필요하다.

## 왜 더 필요한가 — 구조적 위험

우리는 검증에 실패했고, 모델을 바꿨고, 이제 **같은 검증을 다시 돌린다.** 이번에 통과하면
"모델이 좋아졌다"와 "통과할 때까지 맞췄다"가 결과만으로는 구분되지 않는다.

누출은 가정이 아니라 실재한다. 경계 모수(지속 17주)는 트랙 17의 **판별력 기계 자체로**
골랐다. 판별력 비율과 순환매 수익성은 다른 양이지만 독립이 아니다.

그래서 세 가지를 미리 못박는다.

1. 통과 조건과 실패 조건, 어떤 통계량이 어느 지평선에서 결정하는지.
2. 천장이 먼저다 — 완전예지 상한이 낮으면 그 아래 어떤 정확도 개선도 뜻이 없다.
3. 결과가 어느 쪽이든 **선택 의존성**을 결과 안에 적는다.

## 천장이 왜 먼저인가

`rotation_full_sample_ceiling`은 국면을 **이미 알고** 국면별 최고 산업까지 **이미 아는**
전략의 수익이다. 실현 가능한 어떤 전략도 이 위로 갈 수 없다.

트랙 17에서 이 천장은 연 +4.93%였고 실현치는 +1.09%였다 — 천장의 22%다. 경계를 바꾸면
어느 주가 어느 라벨을 다는지가 바뀌므로 **천장 자체가 움직였을 수 있다.** 그것을 먼저
잰다. 천장이 그대로 낮으면 재실행은 거기서 끝난다.

## 무엇을 결정에 쓰지 않는가

실시간 창(2013~)은 침체 에피소드가 2020년 하나뿐이다. 그 창의 수익률을 판정에 쓰면
한 에피소드가 결정하는 셈이 된다. 실시간 경로는 **보고하되 판정하지 않는다** — 트랙 17이
"유효한가"와 "쓸 수 있는가"를 갈라 둔 것과 같다.
"""

from __future__ import annotations

from typing import Any, Final

# ── 판정 표본 ───────────────────────────────────────────────────────────────

#: 판정은 이 표본 하나에서 한다. 최종 수정치, 1994년부터, 연속 구간.
DECISION_SAMPLE: Final[str] = "revised_long"

#: 보고는 하되 판정에 쓰지 않는 표본들. 실시간 창은 침체 에피소드가 하나뿐이다.
REPORTED_NOT_DECIDING: Final[tuple[str, ...]] = (
    "revised_overlap",
    "real_time_overlap",
    "revised_long_ex_covid",
    "revised_long_ex_gfc",
    "revised_long_ex_both",
    "real_time_ex_covid",
)

# ── 1단계: 천장 관문 ────────────────────────────────────────────────────────

#: 완전예지 천장이 이 아래면 재실행은 순환매 질문에서 끝난다.
#:
#: 왜 8%인가. 실현치는 천장의 일부일 수밖에 없고, 트랙 17에서 그 비율은 22%였다.
#: 주간 단방향 회전율 0.063이면 연 3.3회전이고, 왕복 20bp를 얹으면 연 1.3%가 비용으로
#: 나간다. 천장 8%에 22%를 곱하면 1.8%이고 비용을 빼면 0.5%가 남는다 — **여기가 이미
#: 바닥**이다. 천장이 8% 아래면 산술이 남기는 것이 없다.
CEILING_FLOOR_ANNUAL: Final[float] = 0.08

#: 천장의 정보비율도 함께 본다. 수익만 크고 변동이 더 크면 쓸 자리가 아니다.
CEILING_FLOOR_INFORMATION_RATIO: Final[float] = 1.0

#: 트랙 17이 v1.1 라벨에서 잰 천장. 비교 기준으로만 쓴다.
TRACK17_CEILING_ANNUAL: Final[float] = 0.0493
TRACK17_CEILING_INFORMATION_RATIO: Final[float] = 0.774

#: 트랙 17의 실현치. 천장 대비 실현 비율을 다시 잴 때 쓴다.
TRACK17_REALISED_ANNUAL: Final[float] = 0.0109

# ── 2단계: 순환매 통과 조건 ─────────────────────────────────────────────────

#: 실현 가능한 순환매가 12산업 동일가중을 이 폭 이상 이겨야 한다. 연율.
#: 트랙 17은 +1.40%p였고 그것을 "이기지 못했다"로 읽었다. 그 판단을 유지하려면
#: 문턱은 그보다 위여야 한다.
EXCESS_OVER_EQUAL_WEIGHT: Final[float] = 0.02

#: 무작위 라벨 귀무분포에서의 p. 트랙 17은 0.1501이었고 90분위 아래였다.
NULL_P_THRESHOLD: Final[float] = 0.05

#: 2020년을 뺐을 때 초과수익이 최소한 이 비율만큼은 남아야 한다. 트랙 17의 핵심
#: 발견이 "판별력이 2020년 하나에 얹혀 있다"였으므로, 이번에도 같은 검사를 건다.
EX_COVID_RETENTION: Final[float] = 0.5

#: 에피소드 하나를 뺐을 때 초과수익의 부호가 뒤집히면 그것은 에피소드의 성질이지
#: 국면의 성질이 아니다. 트랙 19가 보인 대로 **사건 포함 제외**가 판정 기준이다.
LEAVE_ONE_OUT_MUST_STAY_POSITIVE: Final[bool] = True

# ── 3단계: 판별력 비교 ──────────────────────────────────────────────────────

#: 판별력은 이 지평선에서 본다. 트랙 17과 같다.
HORIZONS: Final[tuple[int, ...]] = (4, 13, 26)

#: 4국면 x 12산업 격자. 다중비교 보정의 족보 크기다.
GRID_ROWS: Final[int] = 4
GRID_COLUMNS: Final[int] = 12

#: BH 보정 뒤 유의로 부를 문턱.
FDR_ALPHA: Final[float] = 0.05

#: 판별력은 **판정하지 않는다.** "분류에 뜻이 있는가"와 "순환매에 쓸 수 있는가"는 다른
#: 질문이고, 트랙 22가 이미 앞의 것에 답했다. 여기서는 트랙 17 숫자와 나란히 놓기만 한다.
DISCRIMINATION_DECIDES: Final[bool] = False


def ceiling_gate(annual: float | None, information_ratio: float | None) -> dict[str, Any]:
    """천장 관문. 이것을 통과하지 못하면 아래 단계는 뜻이 없다."""

    enough_return = annual is not None and float(annual) >= CEILING_FLOOR_ANNUAL
    enough_ratio = (
        information_ratio is not None
        and float(information_ratio) >= CEILING_FLOOR_INFORMATION_RATIO
    )
    passes = bool(enough_return and enough_ratio)
    return {
        "ceiling_annual": annual,
        "ceiling_information_ratio": information_ratio,
        "floor_annual": CEILING_FLOOR_ANNUAL,
        "floor_information_ratio": CEILING_FLOOR_INFORMATION_RATIO,
        "track17_ceiling_annual": TRACK17_CEILING_ANNUAL,
        "moved_versus_track17": (
            round(float(annual) - TRACK17_CEILING_ANNUAL, 4) if annual is not None else None
        ),
        "passes": passes,
        "verdict": (
            "천장이 관문을 넘는다. 아래 단계를 볼 이유가 있다."
            if passes
            else "**천장이 관문을 넘지 못한다.** 국면을 완전히 알고 국면별 최고 산업까지 "
            "알아도 이만큼밖에 나오지 않으므로, 국면 정확도를 아무리 올려도 이 위로 갈 수 "
            "없다. 순환매 질문은 여기서 끝난다."
        ),
    }


def rotation_gate(
    excess_over_equal_weight: float | None,
    null_p: float | None,
    ex_covid_excess: float | None,
    leave_one_out_stays_positive: bool | None,
) -> dict[str, Any]:
    """순환매 통과 조건. 넷 다 만족해야 통과다."""

    beats = (
        excess_over_equal_weight is not None
        and float(excess_over_equal_weight) >= EXCESS_OVER_EQUAL_WEIGHT
    )
    significant = null_p is not None and float(null_p) <= NULL_P_THRESHOLD
    survives_covid = (
        beats
        and ex_covid_excess is not None
        and float(ex_covid_excess) >= EX_COVID_RETENTION * float(excess_over_equal_weight or 0.0)
    )
    survives_leaveout = bool(leave_one_out_stays_positive)
    conditions = {
        "beats_equal_weight_by_the_margin": beats,
        "beats_the_random_label_null": significant,
        "survives_removing_2020": survives_covid,
        "survives_leave_one_episode_out": survives_leaveout,
    }
    return {
        "conditions": conditions,
        "passes": all(conditions.values()),
        "failed": [name for name, ok in conditions.items() if not ok],
    }


def rule() -> dict[str, Any]:
    """규칙 전체를 한 덩어리로. 산출물에 그대로 실어 나중에 대조할 수 있게 한다."""

    return {
        "decision_sample": DECISION_SAMPLE,
        "reported_but_not_deciding": list(REPORTED_NOT_DECIDING),
        "stage_one_ceiling": {
            "floor_annual": CEILING_FLOOR_ANNUAL,
            "floor_information_ratio": CEILING_FLOOR_INFORMATION_RATIO,
            "gates_everything_below": True,
        },
        "stage_two_rotation": {
            "excess_over_equal_weight": EXCESS_OVER_EQUAL_WEIGHT,
            "null_p_threshold": NULL_P_THRESHOLD,
            "ex_covid_retention": EX_COVID_RETENTION,
            "leave_one_out_must_stay_positive": LEAVE_ONE_OUT_MUST_STAY_POSITIVE,
            "leave_one_out_strength_that_decides": "event_including_forward_windows",
        },
        "stage_three_discrimination": {
            "horizons": list(HORIZONS),
            "grid": [GRID_ROWS, GRID_COLUMNS],
            "fdr_alpha": FDR_ALPHA,
            "decides": DISCRIMINATION_DECIDES,
        },
        "what_counts_as_usable": (
            "천장이 연 8% 이상이고 정보비율 1.0 이상이며, 그 아래에서 실현 가능한 "
            "순환매가 동일가중을 연 2%p 이상 이기고, 무작위 라벨 귀무분포에서 p<=0.05이고, "
            "2020년을 빼도 초과수익의 절반이 남고, 어떤 에피소드를 빼도 부호가 유지될 때."
        ),
        "what_counts_as_failure": (
            "천장이 관문을 넘지 못하거나, 넘더라도 위 네 조건 중 하나라도 어긋날 때. "
            "그때의 결론은 '경계를 바르게 잡아도 국면 모델은 순환매를 받쳐 주지 않는다'이며, "
            "그것은 깨끗한 발견이지 미완의 결과가 아니다."
        ),
        "selection_dependence": (
            "경계 모수 17주는 트랙 17·22의 판별력 기계로 골랐다. 판별력과 순환매 수익성은 "
            "다른 양이지만 독립이 아니다. 결과가 긍정이면 그중 얼마가 개선이고 얼마가 "
            "선택인지 이 자료만으로는 가를 수 없으며, 가르려면 **표본 밖 구간, 다른 시장, "
            "또는 사전 등록된 전향 검정** 중 하나가 필요하다."
        ),
    }
