"""**결과를 보기 전에** 적는 판정 규칙.

이 파일은 자료를 내려받기 전에 먼저 쓰였다. 이유는 하나다 — 이 프로젝트는 이제 두
번째 대리 변수 가족을 검정하고 있고, 계속 바꿔 가며 양수가 나올 때까지 시도할 여지가
구조적으로 열려 있다. 그 여지를 닫는 방법은 문턱을 미리 못 박고 그 사실을 산출물에
함께 남기는 것뿐이다.

## 무엇을 프리미엄이 "있다"고 부르는가

세 가지를 모두 미리 정한다.

``DECISION_WINDOW``      판정은 **수정치 라벨 창**에서만 내린다.
``NOMINAL_T``            단일 검정 문턱. HAC t >= 2.0 이고 부호가 양.
``FAMILY_CORRECTED_T``   다중비교 보정 후 문턱. 아래 계산에서 나온다.

판정 창을 수정치 라벨 창으로 잡는 이유:

- **전체 Fama-French 역사에서 프리미엄이 있어도 그것으로는 검정 B를 열 수 없다.**
  우리 국면 라벨이 그 구간에 존재하지 않기 때문이다. 1940년대에 프리미엄이 있었다는
  사실은 우리 모델이 쓸 수 있는 것이 아니다.
- **실시간 창(2013~)은 판정하기에 너무 짧다.** 681주, 그리고 그 안의 침체·회복이
  코로나 한 에피소드다. 참고로만 싣는다.

## 다중비교

가치 정의를 하나 더 시도할 때마다 우연히 유의할 확률이 쌓인다. 그래서 **프로젝트
전체에서 검정한 가치 정의의 수**로 보정한다. 트랙 19의 장부가/시가, 이번의 이익/가격,
현금흐름/가격, 배당수익률 — 넷이다.

영업이익률(OP)은 **가치 대리 변수가 아니다.** 수익성 요인이며, 가치 결론에 접어 넣지
않고 따로 적는다. 그래서 보정 가족에도 들어가지 않는다.

## 검정 B는 언제 여는가

``FAMILY_CORRECTED_T``를 넘긴 대리 변수가 있을 때만. 하나도 없으면 B를 돌리지 않는다.
트랙 19의 B는 애초에 존재하지 않는 효과의 국면 조건부성을 물은 것이었고, 그 실수를
반복하지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

#: 판정 창. 전체 역사도 실시간 창도 아니다.
DECISION_WINDOW: Final[str] = "revised label window"

#: 단일 검정 문턱. 부호가 양이고 HAC t가 이 값 이상.
NOMINAL_T: Final[float] = 2.0

#: 프로젝트 전체에서 검정한 **가치 정의**. OP는 여기 들어가지 않는다.
VALUE_DEFINITIONS_TESTED: Final[tuple[str, ...]] = (
    "book-to-market (Track 19)",
    "earnings-to-price (Track 20)",
    "cashflow-to-price (Track 20)",
    "dividend yield (Track 20)",
)

#: Bonferroni 수준. 0.05를 가치 정의 수로 나눈다.
FAMILY_ALPHA: Final[float] = 0.05 / len(VALUE_DEFINITIONS_TESTED)

#: 그 수준에 해당하는 양측 t 문턱(정규 근사). 0.0125 -> 약 2.50.
FAMILY_CORRECTED_T: Final[float] = 2.50

#: 고-저 스프레드의 기본 구성. 트랙 19와 같아야 비교가 된다.
PRIMARY_SORT: Final[tuple[str, str]] = ("Hi 30", "Lo 30")

#: 보조로 함께 싣는 구성. 판정에는 쓰지 않는다.
SECONDARY_SORT: Final[tuple[str, str]] = ("Hi 10", "Lo 10")


def rule() -> dict[str, Any]:
    """보고서와 산출물에 그대로 실리는 판정 규칙."""

    return {
        "written_before_running": True,
        "decision_window": DECISION_WINDOW,
        "why_this_window": (
            "전체 Fama-French 역사에 프리미엄이 있어도 그 구간에는 우리 국면 라벨이 "
            "없어서 검정 B를 열 수 없다. 실시간 창은 681주에 침체·회복이 코로나 한 "
            "에피소드뿐이라 판정하기에 너무 짧다."
        ),
        "primary_sort": f"{PRIMARY_SORT[0]} - {PRIMARY_SORT[1]} (가치가중 3분위)",
        "secondary_sort": f"{SECONDARY_SORT[0]} - {SECONDARY_SORT[1]} (가치가중 10분위, 참고)",
        "passes_nominally_if": (
            f"판정 창에서 연율 스프레드가 양이고 HAC t >= {NOMINAL_T}"
        ),
        "value_definitions_tested_project_wide": list(VALUE_DEFINITIONS_TESTED),
        "family_size": len(VALUE_DEFINITIONS_TESTED),
        "family_alpha_bonferroni": round(FAMILY_ALPHA, 4),
        "passes_after_multiplicity_if": (
            f"판정 창에서 연율 스프레드가 양이고 HAC t >= {FAMILY_CORRECTED_T}"
        ),
        "operating_profitability_excluded_from_the_family": (
            "영업이익률은 수익성 요인이지 가치 대리 변수가 아니다. 따로 싣고 가치 "
            "결론에 접어 넣지 않으며, 보정 가족에도 넣지 않는다."
        ),
        "test_b_opens_only_if": (
            "다중비교 보정을 통과한 대리 변수가 하나 이상 있을 때만. 없으면 B를 "
            "돌리지 않는다."
        ),
    }


def passes_nominally(annualised: float | None, hac_t: float | None) -> bool:
    if annualised is None or hac_t is None:
        return False
    return annualised > 0 and hac_t >= NOMINAL_T


def passes_after_multiplicity(annualised: float | None, hac_t: float | None) -> bool:
    if annualised is None or hac_t is None:
        return False
    return annualised > 0 and hac_t >= FAMILY_CORRECTED_T
