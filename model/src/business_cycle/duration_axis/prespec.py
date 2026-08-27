"""듀레이션 축 사전 명세. **결과를 보기 전에 커밋한다.**

## 제안된 메커니즘

성장주는 현금흐름이 길다 — 가치의 대부분이 먼 미래 이익에 있다. 할인율이 오르거나 성장
기대가 꺾이면 현재가치가 불균형하게 떨어진다. **이익 효과가 아니라 밸류에이션 효과다.**
방어주는 짧고 확실한 현금흐름을 갖고 있어 덜 민감하다.

2022년이 가장 뚜렷하다. 연준이 긴축했고 NBER은 침체를 선언하지 않았으며 장기 듀레이션
주식이 크게 빠졌고 필수소비재와 에너지는 버텼다. 총이익은 줄지 않았다. 순환은 **할인율을
통해** 일어났다.

그것이 메커니즘이라면 **업종은 잘못된 축**이다. FF12는 각 통에 듀레이션을 섞는다 —
기술 통에 고배수 성장주와 성숙한 현금창출기업이 함께 들어 있다. 업종으로 자르면 찾으려는
효과가 흐려지고, 그것이 3%밖에 조직되지 않은 이유일 수 있다.

## 사전 확률은 불리하다. 그렇게 적는다

듀레이션과 가치는 **같은 정렬을 반대 방향으로 읽은 것**이다 — 장기 듀레이션이 비싼 쪽이다.
가치 대 성장의 타이밍은 금융에서 가장 많이 연구됐고 가장 일관되게 실패한 전략에 든다.
여기서의 주장은 더 좁고 기계적이지만(장기 듀레이션이 **금리 충격과 고분산 국면에서**
특히 부진하다), 넓은 문헌은 강한 반대 사전 확률이다.

트랙 20이 판정 창에서 이 정렬들에 무조건부 프리미엄이 없다는 것을 이미 찾았다. 그것이
이 검정을 막지는 않는다 — 평균 차이가 0인 것은 강한 조건부 차이와 온전히 양립한다.
트랙 19가 실패한 A 위에서 B를 돌렸을 때 그은 것과 같은 구분이다.

## 두 축을 같은 자로 재야 한다

D/P·E/P·CF/P 정렬은 Fama-French가 **월간**으로만 준다. 업종은 일간이다. 빈도가 다르면
천장이 달라지므로, **업종 축도 월간으로 다시 계산해** 같은 자로 견준다. 트랙 23의 주간
숫자는 연속성을 위해 함께 싣되 비교의 근거로 쓰지 않는다.

통 수도 맞춘다. FF12는 12개다. D/P는 무배당군과 10분위를 함께 주므로 **11통**이 되고,
그것이 가능한 가장 가까운 대응이다. 통이 적으면 천장이 기계적으로 낮아지므로 5분위
판본(6통)은 더 거친 보조로만 낸다.
"""

from __future__ import annotations

from typing import Any, Final

# ── 축과 통 ────────────────────────────────────────────────────────────────

#: 기본 듀레이션 대리변수. 무배당군을 포함한 배당수익률이 긴 쪽 끝을 가장 깨끗하게
#: 자른다 — 배당을 내지 않는 기업이 곧 현금흐름이 먼 기업이다.
PRIMARY_PROXY: Final[str] = "dividend_yield"

#: 함께 검정하는 대리변수. B/M은 더 시끄럽다 — 높은 쪽이 짧은 듀레이션과 부실기업을
#: 섞고, 그 둘은 다른 물건이다. 그 사실을 결과와 함께 적는다.
PROXIES: Final[tuple[str, ...]] = (
    "dividend_yield",
    "earnings_to_price",
    "cashflow_to_price",
    "book_to_market",
)

#: 기본 통 나누기. 무배당군 + 10분위 = 11통이며 FF12의 12통에 가장 가깝다.
PRIMARY_BUCKETS: Final[str] = "zero group + deciles"

#: 더 거친 보조. 통이 적으면 천장이 기계적으로 낮아지므로 판정에 쓰지 않는다.
SECONDARY_BUCKETS: Final[str] = "zero group + quintiles"

#: 비교 축. 같은 월간 격자에서 다시 계산한다.
COMPARISON_AXIS: Final[str] = "Fama-French 12 industries, recomputed monthly"

#: 몇 통을 드는가. 트랙 23과 같은 3이다.
TOP_K: Final[int] = 3

# ── 1단계: 천장 관문 — 다른 무엇보다 먼저 ──────────────────────────────────

#: 트랙 23이 업종 축(주간)에서 잰 천장. 비교 기준으로만 쓴다.
TRACK23_INDUSTRY_CEILING_WEEKLY: Final[float] = 0.0506
TRACK23_INDUSTRY_IR_WEEKLY: Final[float] = 0.682

#: 듀레이션 축 천장이 **같은 월간 격자의 업종 축 천장**보다 이 배수 이상이어야 한다.
#:
#: 왜 1.5인가. 축이 틀렸다는 주장이 맞다면 축을 고칠 때 큰 변화가 있어야 한다. 통 수와
#: 빈도만 바꿔도 10~20%는 움직이므로, 그 정도 차이는 "축이 틀렸다"의 증거가 아니다.
CEILING_MUST_EXCEED_INDUSTRY_BY: Final[float] = 1.5

#: 그리고 절대 수준도 넘어야 한다. 축이 바뀌었다고 쓸 만함의 기준이 바뀌지는 않는다 —
#: 트랙 23이 세운 것과 같은 문턱이다.
CEILING_FLOOR_ANNUAL: Final[float] = 0.08
CEILING_FLOOR_INFORMATION_RATIO: Final[float] = 1.0

# ── 2단계: 대조 — 메커니즘이 대조를 정한다 ─────────────────────────────────

#: 제안된 것이 금리 메커니즘이므로 대조는 기간 스프레드와 실현변동성이다. 트랙 25가
#: 정착시킨 그 쌍이며, 만드는 방법도 같다.
CONTROL_LOOKBACKS: Final[tuple[int, ...]] = (4, 13, 26)
CONTROL_SELECTION: Final[str] = "highest control-only R-squared, chosen by rule not by result"
CONTROL_KEEPS_THE_TERM_SPREAD: Final[bool] = True

#: 판정 지평선. 월간 격자라 3개월이 트랙 24·25의 13주에 대응한다.
DECISION_HORIZON_MONTHS: Final[int] = 3

#: 국면 증분이 라벨 이동 귀무분포를 넘어야 한다.
NULL_P_THRESHOLD: Final[float] = 0.05

# ── 3단계: 강건성 ──────────────────────────────────────────────────────────

BOTH_EXCLUSION_STRENGTHS_MUST_HOLD: Final[bool] = True

#: 2022년이 이 트랙의 명백한 단일 에피소드 의존 후보다. 따로, 눈에 띄게 보고한다.
#: 트랙 17은 2020년을 빼자 무너졌고 트랙 25는 GFC 없이 효과의 3분의 2를 잃었다.
EPISODE_EXCLUSIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("ex_2022", "2022-01", "2022-12"),
    ("ex_covid", "2020-01", "2020-12"),
    ("ex_gfc", "2008-01", "2009-12"),
)

# ── 다중비교 — 프로젝트 전체를 센다 ────────────────────────────────────────

#: 트랙 20이 가치 대리변수로 검정한 수. 같은 정렬을 반대 방향으로 읽는 것이므로
#: 족보에 함께 넣는다 — 트랙 20이 세운 규율이다.
TRACK20_PROXIES_TESTED: Final[int] = 4

#: 이 트랙이 검정하는 수.
THIS_TRACK_PROXIES: Final[int] = len(PROXIES)

#: 프로젝트 전체 족보 크기.
PROJECT_WIDE_FAMILY: Final[int] = TRACK20_PROXIES_TESTED + THIS_TRACK_PROXIES

#: 본페로니 문턱. 프로젝트 전체 족보에 대해 건다.
BONFERRONI_ALPHA: Final[float] = 0.05 / PROJECT_WIDE_FAMILY

#: 이 트랙 안의 네 대리변수에 대해서는 BH도 함께 낸다.
FDR_ALPHA: Final[float] = 0.05

# ── 이 계열이 끝나는 지점 ──────────────────────────────────────────────────

#: 천장이 움직이지 않으면 순환매 질문은 **어떤 축으로 잘라도** 닫힌다.
CEILING_CLOSES_THE_LINE: Final[bool] = True


def ceiling_gate(
    duration_annual: float | None,
    duration_ir: float | None,
    industry_annual: float | None,
) -> dict[str, Any]:
    """1단계 — 축을 바꾸면 천장이 움직이는가. 이것이 아래 전부를 막는다."""

    exceeds = (
        duration_annual is not None
        and industry_annual is not None
        and industry_annual > 0
        and float(duration_annual) >= CEILING_MUST_EXCEED_INDUSTRY_BY * float(industry_annual)
    )
    enough_return = duration_annual is not None and float(duration_annual) >= CEILING_FLOOR_ANNUAL
    enough_ratio = duration_ir is not None and float(duration_ir) >= CEILING_FLOOR_INFORMATION_RATIO
    conditions = {
        "materially_higher_than_the_industry_axis": exceeds,
        "clears_the_absolute_floor": enough_return,
        "clears_the_information_ratio_floor": enough_ratio,
    }
    passes = all(conditions.values())
    return {
        "duration_ceiling_annual": duration_annual,
        "duration_ceiling_information_ratio": duration_ir,
        "industry_ceiling_annual": industry_annual,
        "ratio_to_industry": (
            round(float(duration_annual) / float(industry_annual), 3)
            if duration_annual is not None and industry_annual
            else None
        ),
        "conditions": conditions,
        "passes": passes,
        "failed": [name for name, ok in conditions.items() if not ok],
        "verdict": (
            "듀레이션 축에서 천장이 크게 오른다. 축이 틀렸다는 주장에 근거가 있고, 아래 "
            "단계를 볼 이유가 있다."
            if passes
            else "**축을 바꿔도 천장이 오르지 않는다.** 국면을 완전히 알고 국면별 최고 "
            "통까지 알아도 이만큼이므로, **순환매 질문은 어떤 축으로 잘라도 닫힌다.** "
            "업종이 잘못된 축이어서 3%였던 것이 아니다."
        ),
    }


def multiplicity(nominal_p: float | None) -> dict[str, Any]:
    """프로젝트 전체 대리변수 수를 세고 보정한다. 트랙 20이 세운 규율이다."""

    if nominal_p is None:
        return {"nominal_p": None, "family_size": PROJECT_WIDE_FAMILY}
    adjusted = min(1.0, float(nominal_p) * PROJECT_WIDE_FAMILY)
    return {
        "nominal_p": nominal_p,
        "track20_proxies": TRACK20_PROXIES_TESTED,
        "this_track_proxies": THIS_TRACK_PROXIES,
        "family_size": PROJECT_WIDE_FAMILY,
        "bonferroni_alpha": round(BONFERRONI_ALPHA, 5),
        "bonferroni_p": round(adjusted, 4),
        "survives": bool(adjusted <= 0.05),
        "note": (
            "트랙 20이 가치 대리변수로 검정한 넷과 여기서 검정하는 넷은 **같은 정렬을 "
            "반대 방향으로 읽은 것**이므로 한 족보로 센다. 다른 질문이라는 이유로 족보를 "
            "나누면 여덟 번 시도한 사실이 사라진다."
        ),
    }


def rule() -> dict[str, Any]:
    """규칙 전체. 산출물에 그대로 실어 나중에 대조할 수 있게 한다."""

    return {
        "question": (
            "업종이 잘못된 축이어서 국면이 3%밖에 조직하지 못한 것인가. 듀레이션으로 "
            "자르면 달라지는가."
        ),
        "mechanism": (
            "성장주는 현금흐름이 길어 할인율 상승과 성장 기대 하락에 불균형하게 반응한다. "
            "이익 효과가 아니라 밸류에이션 효과다. FF12는 각 통에 듀레이션을 섞으므로 "
            "찾으려는 효과를 흐린다."
        ),
        "prior_is_unfavourable": (
            "듀레이션과 가치는 같은 정렬을 반대 방향으로 읽은 것이고, 가치 대 성장의 "
            "타이밍은 금융에서 가장 많이 연구됐고 가장 일관되게 실패한 전략에 든다. "
            "여기서의 주장은 더 좁고 기계적이지만 넓은 문헌은 강한 반대 사전 확률이다. "
            "트랙 20이 이 정렬들에 무조건부 프리미엄이 없다는 것도 이미 찾았다 — 다만 "
            "평균 0은 강한 조건부 차이와 양립하므로 이 검정을 막지는 않는다."
        ),
        "primary_proxy": PRIMARY_PROXY,
        "proxies": list(PROXIES),
        "primary_buckets": PRIMARY_BUCKETS,
        "secondary_buckets": SECONDARY_BUCKETS,
        "comparison_axis": COMPARISON_AXIS,
        "top_k": TOP_K,
        "why_the_same_ruler": (
            "정렬 자료가 월간뿐이므로 업종 축도 월간으로 다시 계산한다. 통 수도 맞춰 "
            "무배당군+10분위 11통을 기본으로 쓴다 — 통이 적으면 천장이 기계적으로 낮아져 "
            "듀레이션 축이 불리해지고, 그것은 이 검정에서 잘못된 방향이다."
        ),
        "stage_one_ceiling": {
            "must_exceed_industry_by": CEILING_MUST_EXCEED_INDUSTRY_BY,
            "floor_annual": CEILING_FLOOR_ANNUAL,
            "floor_information_ratio": CEILING_FLOOR_INFORMATION_RATIO,
            "gates_everything_below": True,
            "closes_the_line_if_it_fails": CEILING_CLOSES_THE_LINE,
        },
        "stage_two_control": {
            "lookbacks": list(CONTROL_LOOKBACKS),
            "selection": CONTROL_SELECTION,
            "keeps_the_term_spread": CONTROL_KEEPS_THE_TERM_SPREAD,
            "horizon_months": DECISION_HORIZON_MONTHS,
            "null_p_threshold": NULL_P_THRESHOLD,
            "why_these_controls": (
                "제안된 것이 금리 메커니즘이므로 대조도 금리와 변동성이어야 한다. 듀레이션 "
                "반응이 금리·변동성 변화만으로 설명되면 그것이 결론이고, 트랙 19·24·25가 "
                "각자의 축에서 찾은 것을 되풀이하는 셈이다."
            ),
        },
        "stage_three_robustness": {
            "both_exclusion_strengths_must_hold": BOTH_EXCLUSION_STRENGTHS_MUST_HOLD,
            "episode_exclusions": [name for name, _, _ in EPISODE_EXCLUSIONS],
            "why_2022": (
                "2022년이 이 트랙의 명백한 단일 에피소드 의존 후보다. 트랙 17은 2020년을 "
                "빼자 무너졌고 트랙 25는 GFC 없이 효과의 3분의 2를 잃었다."
            ),
        },
        "multiplicity": {
            "track20_proxies": TRACK20_PROXIES_TESTED,
            "this_track_proxies": THIS_TRACK_PROXIES,
            "project_wide_family": PROJECT_WIDE_FAMILY,
            "bonferroni_alpha": round(BONFERRONI_ALPHA, 5),
            "fdr_alpha": FDR_ALPHA,
        },
        "what_counts_as_failure": (
            "천장이 같은 자로 잰 업종 축의 1.5배에 못 미치거나 절대 문턱을 넘지 못하면, "
            "**순환매 질문은 어떤 축으로 잘라도 닫히고 이 계열은 끝난다.** 업종이 잘못된 "
            "축이어서 3%였던 것이 아니라는 뜻이기 때문이다."
        ),
    }
