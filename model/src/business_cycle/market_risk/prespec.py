"""통과 조건을 **결과를 보기 전에** 적는다.

트랙 23과 같은 절차다. 다른 것은 **문턱의 종류**다.

## 왜 순환매 관문을 그대로 가져오면 안 되는가

순환매의 관문은 비용이 정했다. 주간 회전율 0.063이면 연 3.3회전이고, 왕복 20bp를 얹으면
연 1.3%가 비용으로 나간다. 그래서 "천장이 8% 아래면 산술이 남기는 것이 없다"가 성립했다.

**노출 조절은 비용 구조가 다르다.** 국면이 바뀔 때만 움직이고, 이 모델에서 후퇴기는
persist17w 기준 32년에 11에피소드다. 노출을 100%에서 60%로 내렸다 올리면 왕복 회전율이
0.8이고, 지수 선물·ETF에서 한쪽 10bp면 왕복 16bp다. 연 1.2회 발동해도 **연 20bp** 언저리다.

즉 이 질문에서 비용은 구속 조건이 아니다. 구속하는 것은 **신뢰성**이다 — 국면이 실제로
위험 구간을 갈라 주는가, 아니면 갈라 보이는 것이 표본 잡음인가. 그래서 문턱을 수익이
아니라 **분포 분리**에 건다.

## 무엇을 결정에 쓰는가

판정 표본은 `revised_long` 하나, 판정 지평선은 **13주**다. 트랙 17·19가 쓴 것과 같고,
분기 자료 발표 주기에 대응하는 길이다. 4·26·52주는 함께 보고하되 판정하지 않는다.

실시간 창은 침체 에피소드가 2020년 하나뿐이라 판정에서 뺀다 — 트랙 23과 같은 이유다.

## 기간 스프레드가 결정적이다

트랙 19는 국면 모델이 가치 타이밍에서 기간 스프레드를 넘어서지 못한다는 것을 보였다.
같은 물음이 여기서 더 날카롭다 — 기간 스프레드는 그 자체로 잘 알려진 위험 신호이고,
매일 공짜로 받을 수 있는 한 줄짜리 계열이다.

**국면이 스프레드를 넘어서지 못하면 그것이 결론이다.** 다른 조건이 몇 개 통과하든
상관없다. 그때는 이 용도에서도 계열 하나가 모델을 대신한다는 뜻이기 때문이다.
"""

from __future__ import annotations

from typing import Any, Final

# ── 판정 대상 ───────────────────────────────────────────────────────────────

DECISION_SAMPLE: Final[str] = "revised_long"
DECISION_HORIZON_WEEKS: Final[int] = 13

#: 함께 보고하되 판정하지 않는 지평선.
HORIZONS: Final[tuple[int, ...]] = (4, 13, 26, 52)

#: 실시간 창은 침체 에피소드가 하나뿐이라 한 사건이 판정하게 된다.
REPORTED_NOT_DECIDING: Final[tuple[str, ...]] = (
    "revised_overlap",
    "real_time_overlap",
    "revised_long_ex_covid",
    "revised_long_ex_gfc",
)

# ── 1. 분포가 실제로 갈리는가 ───────────────────────────────────────────────

#: 가장 위험한 국면과 가장 안전한 국면의 전방 하방변동성 비. 이 아래면 노출을 바꿀
#: 이유가 없다.
#:
#: 왜 1.5인가. 변동성 추정 자체의 표본 오차가 크다 — 13주 창 100개 남짓이면 표준오차가
#: 대략 7%이고, 두 국면을 견주면 10%다. 20~30% 차이는 그 잡음 안에 있다. 1.5배는 그
#: 잡음의 다섯 배 밖이고, 노출을 40%p 움직일 근거로 삼기에 최소한의 폭이다.
DOWNSIDE_RATIO_FLOOR: Final[float] = 1.5

#: 두 국면 전방 수익 분포의 겹침. 평균이 갈려도 분포가 거의 포개지면 개별 시점의 결정은
#: 신뢰할 수 없다. 0.85면 여섯 번에 한 번꼴로만 구분되는 셈이고, 그것이 최소선이다.
OVERLAP_CEILING: Final[float] = 0.85

#: 무작위 라벨 귀무분포에서의 p. 판정 통계량은 하방변동성 비다.
NULL_P_THRESHOLD: Final[float] = 0.05

# ── 2. 한 구간에 얹혀 있지 않은가 ───────────────────────────────────────────

#: 에피소드를 하나 빼도 비가 이 아래로 내려가면 안 된다. 1.5에서 1.25까지는 허용하되
#: 우연 수준(1.0)에 닿으면 그것은 그 에피소드의 성질이다.
LEAVE_ONE_OUT_FLOOR: Final[float] = 1.25

#: 판정하는 제외 강도. 트랙 19가 두 강도가 반대 답을 준다는 것을 보였고, 트랙 23에서는
#: 그 방향이 또 뒤집혔다. 그래서 **둘 다 보고하고 둘 다 통과해야** 한다.
BOTH_EXCLUSION_STRENGTHS_MUST_HOLD: Final[bool] = True

#: 2020년을 빼도 하방변동성 비의 이만큼은 남아야 한다(1.0 초과분 기준).
EX_COVID_RETENTION: Final[float] = 0.5

# ── 3. 기간 스프레드 — 결정적 ───────────────────────────────────────────────

#: 국면이 스프레드 위에 얹어 주는 증분 결정계수. 이 아래면 넘어서지 못한 것이다.
#: 절대값이 작아 보이지만 주간 수익 회귀에서 R²는 원래 작다 — 스프레드 단독도 1%를
#: 넘기 어렵다. 그래서 문턱은 절대 크기가 아니라 **이동 귀무분포**가 정한다.
INCREMENTAL_R_SQUARED_FLOOR: Final[float] = 0.0

#: 증분이 라벨 이동 귀무분포를 넘어야 한다. 이것이 실제 문턱이다.
INCREMENTAL_NULL_P: Final[float] = 0.05

#: 스프레드를 넘지 못하면 다른 조건과 무관하게 결론은 부정이다.
TERM_SPREAD_IS_DECISIVE: Final[bool] = True

# ── 관측 정의 ───────────────────────────────────────────────────────────────

#: "큰 음의 주"의 정의. 주간 시장 초과수익이 이 아래인 주.
#: 대략 주간 표준편차의 1.5배이고, 사후에 고르지 않도록 여기 못박는다.
LARGE_NEGATIVE_WEEK: Final[float] = -0.03

#: 하방변동성은 0 미만 관측만 쓴다. 시장 초과수익(Mkt-RF)이 이미 무위험 초과라
#: 별도의 기준선을 두지 않는다.
DOWNSIDE_THRESHOLD: Final[float] = 0.0


def separation_gate(
    downside_ratio: float | None,
    overlap: float | None,
    null_p: float | None,
) -> dict[str, Any]:
    """1단계 — 분포가 실제로 갈리는가."""

    separates = downside_ratio is not None and float(downside_ratio) >= DOWNSIDE_RATIO_FLOOR
    distinct = overlap is not None and float(overlap) <= OVERLAP_CEILING
    beats_null = null_p is not None and float(null_p) <= NULL_P_THRESHOLD
    conditions = {
        "downside_volatility_ratio_is_large_enough": separates,
        "the_two_distributions_do_not_simply_overlap": distinct,
        "beats_the_random_label_null": beats_null,
    }
    return {
        "downside_ratio": downside_ratio,
        "downside_ratio_floor": DOWNSIDE_RATIO_FLOOR,
        "overlap": overlap,
        "overlap_ceiling": OVERLAP_CEILING,
        "null_p": null_p,
        "conditions": conditions,
        "passes": all(conditions.values()),
        "failed": [name for name, ok in conditions.items() if not ok],
    }


def robustness_gate(
    block_only_low: float | None,
    event_including_low: float | None,
    ex_covid_ratio: float | None,
    full_ratio: float | None,
) -> dict[str, Any]:
    """2단계 — 한 구간에 얹혀 있지 않은가. 두 강도 모두 통과해야 한다."""

    def _holds(value: float | None) -> bool:
        return value is not None and float(value) >= LEAVE_ONE_OUT_FLOOR

    excess = (float(full_ratio) - 1.0) if full_ratio is not None else None
    kept = (
        ex_covid_ratio is not None
        and excess is not None
        and excess > 0
        and (float(ex_covid_ratio) - 1.0) >= EX_COVID_RETENTION * excess
    )
    conditions = {
        "block_only_stays_above_the_floor": _holds(block_only_low),
        "event_including_stays_above_the_floor": _holds(event_including_low),
        "survives_removing_2020": bool(kept),
    }
    return {
        "floor": LEAVE_ONE_OUT_FLOOR,
        "block_only_lowest": block_only_low,
        "event_including_lowest": event_including_low,
        "ex_covid_ratio": ex_covid_ratio,
        "conditions": conditions,
        "passes": all(conditions.values()),
        "failed": [name for name, ok in conditions.items() if not ok],
    }


def spread_gate(incremental_r_squared: float | None, null_p: float | None) -> dict[str, Any]:
    """3단계 — 기간 스프레드를 넘어서는가. **이것이 결정적이다.**"""

    positive = (
        incremental_r_squared is not None
        and float(incremental_r_squared) > INCREMENTAL_R_SQUARED_FLOOR
    )
    beats_null = null_p is not None and float(null_p) <= INCREMENTAL_NULL_P
    passes = bool(positive and beats_null)
    return {
        "incremental_r_squared": incremental_r_squared,
        "null_p": null_p,
        "null_p_threshold": INCREMENTAL_NULL_P,
        "passes": passes,
        "decisive": TERM_SPREAD_IS_DECISIVE,
        "verdict": (
            "국면이 기간 스프레드 위에 무언가를 얹는다."
            if passes
            else "**국면이 기간 스프레드를 넘어서지 못한다.** 이 용도에서도 매일 공짜로 "
            "받는 계열 하나가 모델을 대신한다는 뜻이고, 다른 조건이 몇 개 통과하든 "
            "결론은 여기서 정해진다."
        ),
    }


def rule() -> dict[str, Any]:
    """규칙 전체. 산출물에 그대로 실어 나중에 대조할 수 있게 한다."""

    return {
        "question": (
            "국면이 **시장 수준의 수익과 위험**을 가르는가. 무엇을 들지가 아니라 얼마나 "
            "들지에 대한 물음이며, 횡단면 질문(트랙 17·23)과 다른 질문이다."
        ),
        "decision_sample": DECISION_SAMPLE,
        "decision_horizon_weeks": DECISION_HORIZON_WEEKS,
        "reported_but_not_deciding": list(REPORTED_NOT_DECIDING),
        "why_the_threshold_differs_from_the_rotation_gate": (
            "순환매 관문은 비용이 정했다 — 주간 회전율 0.063에 왕복 20bp면 연 1.3%가 "
            "나간다. 노출 조절은 국면이 바뀔 때만 움직이고 32년에 11에피소드다. 40%p를 "
            "왕복해도 연 20bp 언저리라 **비용은 구속 조건이 아니다.** 구속하는 것은 "
            "신뢰성이므로, 문턱을 수익이 아니라 분포 분리에 건다."
        ),
        "stage_one_separation": {
            "downside_volatility_ratio_floor": DOWNSIDE_RATIO_FLOOR,
            "overlap_ceiling": OVERLAP_CEILING,
            "null_p_threshold": NULL_P_THRESHOLD,
        },
        "stage_two_robustness": {
            "leave_one_out_floor": LEAVE_ONE_OUT_FLOOR,
            "both_exclusion_strengths_must_hold": BOTH_EXCLUSION_STRENGTHS_MUST_HOLD,
            "ex_covid_retention": EX_COVID_RETENTION,
        },
        "stage_three_term_spread": {
            "incremental_null_p": INCREMENTAL_NULL_P,
            "decisive": TERM_SPREAD_IS_DECISIVE,
        },
        "observation_definitions": {
            "large_negative_week": LARGE_NEGATIVE_WEEK,
            "downside_threshold": DOWNSIDE_THRESHOLD,
            "market_excess_return": "Fama-French Mkt-RF. 이미 무위험 초과라 따로 빼지 않는다.",
        },
        "what_counts_as_usable": (
            f"판정 표본·판정 지평선({DECISION_HORIZON_WEEKS}주)에서 가장 위험한 국면과 "
            f"가장 안전한 국면의 전방 하방변동성 비가 {DOWNSIDE_RATIO_FLOOR} 이상이고, "
            f"두 분포의 겹침이 {OVERLAP_CEILING} 이하이고, 무작위 라벨 귀무분포에서 "
            f"p<={NULL_P_THRESHOLD}이고, 두 제외 강도 모두에서 비가 "
            f"{LEAVE_ONE_OUT_FLOOR} 위에 남고, 2020년을 빼도 초과분의 절반이 남고, "
            "**그리고 국면이 기간 스프레드 위에 이동 귀무분포를 넘는 증분을 얹을 때.**"
        ),
        "what_counts_as_failure": (
            "위 중 하나라도 어긋날 때. 특히 기간 스프레드를 넘지 못하면 다른 조건과 "
            "무관하게 부정이다. 그때의 결론은 '이 모델의 쓸모는 서술과 상태 인식으로 "
            "확정된다'이며, 그것은 물러선 것이 아니라 **정해진 것**이다."
        ),
    }
