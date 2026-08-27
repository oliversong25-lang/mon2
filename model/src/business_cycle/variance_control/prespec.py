"""분산을 판정 기반으로 두는 사전 명세. **결과를 보기 전에 커밋한다.**

트랙 24가 하나를 남겨 두었다. 전방 13주 수익 **분산**에 대해 스프레드 단독 0.0035,
국면 단독 0.1236, 국면 증분 0.1201(p=0.0013), 그리고 역방향 증분은 0.000014였다.
2020년을 빼도 p=0.0239, 2008~09년을 빼도 p=0.0014로 살아남았다.

트랙 24는 결과를 본 뒤에 판정 통계량을 바꾸지 않았고, 분산을 기준으로 삼으려면 새
사전 명세가 먼저 있어야 한다고 적었다. 이것이 그 사전 명세다.

## 이것을 무너뜨릴 수 있는 대조

시장 변동성은 강하게 군집한다. **과거 실현변동성만으로도 미래 분산이 잘 예측된다.**

국면은 거시계열의 수준과 모멘텀으로 정의되고, 침체·후퇴 구간은 고변동성 구간과 거의
정의상 겹친다. 그래서 "국면이 분산을 예측한다"는 **침체기는 변동성이 높고 변동성은
지속된다**를 다시 발견한 것일 수 있다. 참이지만 쓸모없다 — 가격이 그것을 더 싸게, 그리고
발표 지연 없이 준다.

기간 스프레드는 **수익** 예측의 옳은 대조였다. **분산 예측의 옳은 대조는 과거 실현
변동성이고, 그것을 돌리지 않았다.**

## 대조를 약하게 만들지 않는다

되돌아보기 창을 하나만 고르면 불리한 창을 골라 대조를 약화시킬 수 있다. 그래서 4·13·26주를
모두 계산하고, **단독으로 가장 강한 것**을 대조로 쓴다. 가장 약한 것이 아니다.

함수 형태로도 약화시키지 않는다. 대조는 실현분산과 그 제곱근을 **함께** 받는다 — 한
형태가 틀려도 다른 형태가 받쳐 주므로, 형태를 잘못 골라 대조가 지는 일이 없다.

기간 스프레드도 대조에 남겨 둔다. 물음은 "국면이 **둘 다** 넘어서는가"다.

## 이것이 이 계열의 마지막 검정이다

여덟 트랙을 검정해 왔고, 통과할 때까지 계속하는 위험이 실재한다. 그래서 미리 못박는다 —
**실패하면 다른 대조도, 다른 지평선도, 다른 분산 정의도, 다른 통계량도 찾지 않는다.**
여기서 질문이 닫힌다.
"""

from __future__ import annotations

from typing import Any, Final

# ── 판정 대상 ───────────────────────────────────────────────────────────────

DECISION_SAMPLE: Final[str] = "revised_long"
DECISION_HORIZON_WEEKS: Final[int] = 13

#: 판정 목표. **트랙 24가 쓴 것과 정확히 같다** — 심문 대상이 그 결과이므로 정의를
#: 바꾸면 같은 것을 재는지 알 수 없다.
DECISION_TARGET: Final[str] = "squared forward 13-week return"

#: 함께 보고하되 **판정을 뒤집을 수 없는** 두 번째 정의. 전방 13주 동안의 주간 제곱합이며
#: 분산의 더 직접적인 측정이다. 여기 미리 적어 두는 이유는, 결과를 본 뒤에 꺼내면
#: 그것이 곧 정의 탐색이기 때문이다.
SECONDARY_TARGET: Final[str] = "forward realised variance (sum of squared weekly returns)"
SECONDARY_CANNOT_OVERTURN: Final[bool] = True

# ── 대조 ────────────────────────────────────────────────────────────────────

#: 실현변동성 되돌아보기 창. 셋 다 계산하고 단독으로 가장 강한 것을 대조로 쓴다.
LOOKBACKS: Final[tuple[int, ...]] = (4, 13, 26)

#: 대조는 실현분산과 그 제곱근을 함께 받는다. 함수 형태를 잘못 골라 대조가 지는 일을
#: 막는다 — 대조를 약하게 만들면 이 검정이 무의미해진다.
CONTROL_TAKES_BOTH_LEVEL_AND_ROOT: Final[bool] = True

#: 기간 스프레드도 대조에 남긴다. 물음은 "국면이 둘 다 넘어서는가"다.
CONTROL_KEEPS_THE_TERM_SPREAD: Final[bool] = True

#: 되돌아보기 창을 고르는 규칙. **단독 설명력이 가장 큰 것**이며, 결과를 본 뒤 고르는
#: 것이 아니라 이 규칙이 자동으로 고른다.
LOOKBACK_SELECTION: Final[str] = "highest control-only R-squared on the decision target"

# ── 판정 ────────────────────────────────────────────────────────────────────

#: 판정 통계량 — 국면이 변동성 대조 위에 얹는 증분 결정계수.
#: 최대/최소 비가 아니다. 트랙 24가 그 통계량의 약함을 기록했다.
DECISION_STATISTIC: Final[str] = "incremental R-squared of phase over the volatility control"

#: 증분이 라벨 이동 귀무분포를 넘어야 한다. 절대 크기가 아니라 이것이 문턱이다.
NULL_P_THRESHOLD: Final[float] = 0.05

#: 에피소드를 하나 빼도 증분이 양수로 남아야 하고, **두 강도 모두**에서 그래야 한다.
LEAVE_ONE_OUT_MUST_STAY_POSITIVE: Final[bool] = True
BOTH_EXCLUSION_STRENGTHS_MUST_HOLD: Final[bool] = True

#: 2020년을 뺀 표본과 2008~09년을 뺀 표본에서도 각각 귀무를 넘어야 한다. 따로 보고한다.
EPISODE_EXCLUSIONS_MUST_EACH_HOLD: Final[bool] = True

#: 실시간 창은 침체 에피소드가 2020년 하나뿐이라 판정에서 뺀다. 보고는 한다.
REPORTED_NOT_DECIDING: Final[tuple[str, ...]] = ("real_time_overlap", "v1_1_revised")

# ── 통과했을 때의 제품 형태 — 미리 묶어 둔다 ────────────────────────────────

#: 통과해도 만들 수 있는 것은 **역사적 분포에 대한 사실 진술**뿐이다.
PERMITTED_WORDING: Final[str] = "이 국면에서는 역사적으로 전방 13주 음수 주 비율이 52%였습니다"

#: 통과해도 만들 수 없는 것. 결과가 좋았다는 이유로 넓히지 않는다.
FORBIDDEN_WORDING: Final[tuple[str, ...]] = (
    "노출을 바꾸라는 어떤 지시도",
    "읽는 사람의 보유 자산에 대한 어떤 언급도",
    "앞날에 대한 어떤 확률 주장도",
)

# ── 실패했을 때 ─────────────────────────────────────────────────────────────

THIS_IS_THE_LAST_TEST_IN_THIS_LINE: Final[bool] = True


def lookback_choice(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """단독 설명력이 가장 큰 되돌아보기 창. 규칙이 고르지 내가 고르지 않는다."""

    usable = [row for row in candidates if row.get("control_only_r_squared") is not None]
    if not usable:
        return {"chosen": None, "candidates": candidates}
    best = max(usable, key=lambda row: float(row["control_only_r_squared"]))
    weakest = min(usable, key=lambda row: float(row["control_only_r_squared"]))
    return {
        "chosen": best["lookback_weeks"],
        "chosen_control_only_r_squared": best["control_only_r_squared"],
        "weakest": weakest["lookback_weeks"],
        "weakest_control_only_r_squared": weakest["control_only_r_squared"],
        "candidates": candidates,
        "rule": LOOKBACK_SELECTION,
        "note": (
            "가장 강한 창을 대조로 쓴다. 가장 약한 창을 골랐다면 국면 증분이 "
            f"{'더 커 보였을 것이다' if best != weakest else '같았을 것이다'} — 그렇게 "
            "고르는 것은 대조를 약화시켜 결과를 만드는 일이다."
        ),
    }


def decision_gate(
    increment: float | None,
    null_p: float | None,
    leave_one_out_block_only_lowest: float | None,
    leave_one_out_event_including_lowest: float | None,
    ex_covid_null_p: float | None,
    ex_gfc_null_p: float | None,
) -> dict[str, Any]:
    """판정. 다섯 조건 전부를 만족해야 통과다."""

    def _p_holds(value: float | None) -> bool:
        return value is not None and float(value) <= NULL_P_THRESHOLD

    def _positive(value: float | None) -> bool:
        return value is not None and float(value) > 0.0

    conditions = {
        "phase_adds_over_the_volatility_control": (_positive(increment) and _p_holds(null_p)),
        "block_only_exclusion_keeps_it_positive": _positive(leave_one_out_block_only_lowest),
        "event_including_exclusion_keeps_it_positive": _positive(
            leave_one_out_event_including_lowest
        ),
        "holds_without_2020": _p_holds(ex_covid_null_p),
        "holds_without_the_gfc": _p_holds(ex_gfc_null_p),
    }
    passes = all(conditions.values())
    return {
        "increment": increment,
        "null_p": null_p,
        "conditions": conditions,
        "passes": passes,
        "failed": [name for name, ok in conditions.items() if not ok],
        "verdict": (
            "국면이 과거 실현변동성 위에 분산 설명력을 얹는다. 변동성 군집만으로 "
            "설명되지 않는 것이 남아 있다."
            if passes
            else "**국면이 과거 실현변동성을 넘어서지 못한다.** 트랙 24가 남긴 분산 결과는 "
            "**거시 렌즈를 통해 본 변동성 군집**이었다 — 침체기는 변동성이 높고 변동성은 "
            "지속된다는 사실이며, 가격이 그것을 더 싸게, 발표 지연 없이 준다."
        ),
    }


def rule() -> dict[str, Any]:
    """규칙 전체. 산출물에 그대로 실어 나중에 대조할 수 있게 한다."""

    return {
        "question": (
            "트랙 24가 남긴 분산 결과가 국면의 정보인가, 아니면 **변동성 군집을 거시 "
            "렌즈로 다시 본 것**인가."
        ),
        "decision_sample": DECISION_SAMPLE,
        "decision_horizon_weeks": DECISION_HORIZON_WEEKS,
        "decision_target": DECISION_TARGET,
        "decision_statistic": DECISION_STATISTIC,
        "secondary_target": SECONDARY_TARGET,
        "secondary_cannot_overturn": SECONDARY_CANNOT_OVERTURN,
        "control": {
            "lookbacks": list(LOOKBACKS),
            "selection": LOOKBACK_SELECTION,
            "takes_both_level_and_root": CONTROL_TAKES_BOTH_LEVEL_AND_ROOT,
            "keeps_the_term_spread": CONTROL_KEEPS_THE_TERM_SPREAD,
            "why_not_weaken_it": (
                "창 하나만 고르거나 함수 형태를 하나만 주면 대조를 약화시켜 결과를 만들 수 "
                "있다. 셋을 다 계산해 **가장 강한** 것을 쓰고, 수준과 제곱근을 함께 준다."
            ),
        },
        "reported_but_not_deciding": list(REPORTED_NOT_DECIDING),
        "what_counts_as_usable": (
            f"판정 표본·{DECISION_HORIZON_WEEKS}주 지평선에서 국면이 변동성 대조 위에 "
            f"양의 증분을 얹고 라벨 이동 귀무분포에서 p<={NULL_P_THRESHOLD}이며, 두 제외 "
            "강도 모두에서 증분이 양수로 남고, 2020년을 뺀 표본과 2008~09년을 뺀 표본 "
            "각각에서도 귀무를 넘을 때."
        ),
        "what_counts_as_failure": (
            "위 중 하나라도 어긋날 때. 그때의 결론은 '트랙 24의 분산 결과는 거시 렌즈를 "
            "통해 본 변동성 군집이며, 이 모델의 쓸모는 서술과 상태 인식으로 확정된다'이고, "
            "**이 계열의 검정은 여기서 끝난다.**"
        ),
        "this_is_the_last_test_in_this_line": THIS_IS_THE_LAST_TEST_IN_THIS_LINE,
        "if_it_fails_do_not": [
            "다른 대조를 찾지 않는다",
            "다른 지평선을 찾지 않는다",
            "다른 분산 정의를 찾지 않는다",
            "다른 통계량을 찾지 않는다",
        ],
        "product_form_if_it_passes": {
            "permitted": PERMITTED_WORDING,
            "forbidden": list(FORBIDDEN_WORDING),
            "note": (
                "통과해도 제품 형태는 이미 정해져 있고 좁다. 결과가 좋았다는 이유로 넓히지 않는다."
            ),
        },
    }
