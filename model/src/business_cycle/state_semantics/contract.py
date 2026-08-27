"""§3. 동결 분류기가 이미 갖고 있는 경제적 의미를 형식 계약으로 옮겨 적는다.

새 사분면 정의를 만들지 않는다. 여기 있는 모든 문장은 ``four_phase/evidence.py``와
``four_phase/filter.py``의 실제 구성에서 읽어 온 것이며, 그 구성을 부호만 보는 새 분류기로
갈아치우지 않는다.

동결 구성의 요지는 이렇다.

``strong = sigmoid(level / neutral_level)``   수준이 0보다 크면 0.5를 넘는다.
``rising = sigmoid(momentum / neutral_momentum)``  모멘텀이 0보다 크면 0.5를 넘는다.

네 사분면은 두 값의 곱이다. 그러므로 **부호 경계는 0이고, ``neutral_*``은 문턱이 아니라
전이의 부드러움을 정하는 척도다.** 이 구분을 놓치면 "중립대 밖"과 "부호가 양수"를 혼동하게
된다.

그 위에 두 개의 감쇠가 순서대로 걸린다.

1. 침체 몫은 ``contraction_evidence``가 ``contraction_entry``를 넘은 만큼만 살아남고,
   남는 몫은 나머지 셋에 **비례 배분**된다. 침체 증거 자체가 §2의 하드 폭 게이트를
   통과해야 하므로, 동행 도메인 둘의 확인 없이는 침체 몫이 0으로 눌린다.
2. 회복 몫은 무정보 기준선 1/4을 **넘는 부분만** ``recovery_evidence``만큼 남고, 깎인
   몫은 확장·후퇴·침체에 비례 배분된다.

두 감쇠의 결과로 **수준이 정상 아래인데도 확장기가 이길 수 있다.** 침체 증거가 문턱에
못 미쳐 침체 몫이 사라지고, 회복 증거가 없어 회복 몫이 기준선까지 깎이면, 남는 몫이
확장·후퇴로 흘러간다. 이것은 결함이 아니라 구성의 성질이며, 2001년 경로를 설명하는
열쇠다. 감사는 이 사실을 감추지 않고 별도 진단으로 센다.
"""

from __future__ import annotations

from typing import Any, Final

from ..four_phase.engine import FourPhaseConfig
from ..four_phase.evidence import PHASES

#: 부호 사분면. 경계는 0이다 — ``neutral_*``이 아니다.
SIGN_QUADRANT: Final[dict[str, tuple[str, str]]] = {
    "expansion": ("level > 0", "momentum > 0"),
    "slowdown": ("level > 0", "momentum <= 0"),
    "recovery": ("level <= 0", "momentum > 0"),
    "contraction": ("level <= 0", "momentum <= 0"),
}


def sign_quadrant(level: float, momentum: float) -> str:
    """(수준, 모멘텀) 부호가 가리키는 사분면. 동결 구성의 시그모이드 중심과 같다."""

    if level > 0.0:
        return "expansion" if momentum > 0.0 else "slowdown"
    return "recovery" if momentum > 0.0 else "contraction"


def opposite(phase: str) -> str:
    """시계 반대편. 두 부호가 모두 어긋난 경우를 가리킨다."""

    return {
        "expansion": "contraction",
        "contraction": "expansion",
        "slowdown": "recovery",
        "recovery": "slowdown",
    }[phase]


def build(config: FourPhaseConfig) -> dict[str, Any]:
    """§3의 의미 계약. 값은 전부 동결 설정에서 읽는다."""

    thresholds = config.thresholds
    common = {
        "score_construction": (
            "quadrant = sigmoid(level / neutral_level) × sigmoid(momentum / neutral_momentum) "
            "의 네 조합. 부호 경계는 0이고 neutral_*은 전이의 부드러움을 정하는 척도다."
        ),
        "neutral_band_treatment": (
            f"|level| ≤ {thresholds.neutral_level} 이고 |momentum| ≤ {thresholds.neutral_momentum} "
            "이면 `neutral_both`다. 이 조건은 **라벨을 정하지 않는다** — 증거 품질을 낮추어 "
            "확인 규칙이 즉시 전환을 쓰지 못하게 만든다."
        ),
        "confirmation_role": (
            f"도전자가 필터 승자로 {config.confirmation_weeks}주 연속 버티면 전환한다. "
            f"증거 품질이 높고 원시 마진이 {config.immediate_margin} 이상이면 즉시 전환한다. "
            "상태는 (현재 국면, 도전자, 연속 주 수)뿐이고 어떤 국면도 흡수하지 않는다."
        ),
        "raw_versus_filtered": (
            "원시 점수는 그 주의 관측만으로 만든다. 필터 점수는 앞선 사후확률을 순환 거리 "
            "커널로 예측해 곱한 값이므로 **역사를 담는다**. 그래서 '그 시점 증거'의 기준은 "
            "원시 국면이고, 필터 승자는 원시와 공식 사이의 중간 층이다."
        ),
        "low_quality_retention": (
            "증거 품질이 낮으면(중립대 안, 신선도 미달, 집중도 과다, 분리도 "
            f"{config.separation_floor} 미만) 즉시 전환 경로가 닫힌다. 그 결과 직전 공식 "
            "국면이 확인 기간 동안 유지될 수 있다. 유지는 흡수가 아니다 — 도전자가 확인 "
            "기간만 버티면 증거가 약해도 반드시 이긴다."
        ),
        "labor_stress_role": (
            "노동시장 스트레스는 동행 활동지표가 아니라 가교다. 침체 폭과 회복 폭 계산에 "
            "들어가지 않으며, 침체 증거에는 corroboration_share "
            f"{thresholds.corroboration_share}로 묶인 뒷받침 항으로만 기여한다. 단독으로 "
            "국면을 결정할 수 없다."
        ),
    }

    return {
        "source": "business_cycle.four_phase.evidence / .filter / .engine",
        "frozen_config_sha256": config.sha256,
        "derived_not_invented": True,
        "new_classifier_introduced": False,
        "phase_order_is_not_compulsory": True,
        "phase_order_note": (
            "필터의 전이 행렬은 순환 거리로 감쇠하되 모든 성분이 양수다. 인접 강제도, "
            "단방향 회전 강제도 없다. 후퇴기→확장기 재가속, 회복기→침체 되돌림, 급격한 "
            "충격에서의 국면 건너뛰기가 모두 구조적으로 허용된다."
        ),
        "common": common,
        "phases": {
            "recovery": {
                "level_condition": "level ≤ 0 (수준이 정상 아래)",
                "momentum_condition": "momentum > 0 (개선 중)",
                "breadth_requirement": (
                    f"회복 증거는 양수 모멘텀 동행 도메인 {thresholds.recovery_breadth}개 이상을 "
                    "요구한다. 노동시장은 이 셈에 들어가지 않는다."
                ),
                "severity_requirement": (
                    f"모멘텀이 recovery_momentum {thresholds.recovery_momentum}에 대해 재고, "
                    f"총량 모멘텀이 중립대를 넘어 연속 {thresholds.recovery_persistence_weeks}주 "
                    "양수여야 지속 항이 1에 닿는다."
                ),
                "damping": (
                    "회복 몫 중 무정보 기준선 1/4을 넘는 부분만 recovery_evidence만큼 남는다. "
                    "깎인 몫은 확장·후퇴·침체에 비례 배분된다."
                ),
            },
            "expansion": {
                "level_condition": "level > 0",
                "momentum_condition": "momentum > 0",
                "breadth_requirement": "고유 폭 요건 없음. breadth_support가 가볍게 기울일 뿐이다.",
                "severity_requirement": "없음",
                "damping": (
                    "고유 감쇠 없음. 다만 침체·회복 감쇠의 **잔여 몫을 받는 쪽**이므로, "
                    "수준이 0보다 작아도 확장기가 이길 수 있다."
                ),
            },
            "slowdown": {
                "level_condition": "level > 0",
                "momentum_condition": "momentum ≤ 0",
                "breadth_requirement": "고유 폭 요건 없음.",
                "severity_requirement": "없음",
                "damping": "고유 감쇠 없음. 잔여 몫을 받는 쪽이다.",
            },
            "contraction": {
                "level_condition": "level ≤ 0",
                "momentum_condition": "momentum ≤ 0",
                "breadth_requirement": (
                    f"§2의 하드 게이트. 공식 침체는 독립적인 동행 도메인 "
                    f"{thresholds.minimum_coincident_domains}개의 확인을 요구하며, 넓은 하락 "
                    "경로와 급속 악화 경로에 각각 마스크로 걸린다."
                ),
                "severity_requirement": (
                    f"침체 증거가 contraction_entry {thresholds.contraction_entry}를 넘은 만큼만 "
                    "침체 몫이 살아남는다. 못 넘으면 몫이 0으로 눌리고 나머지 셋에 비례 "
                    "배분된다."
                ),
                "damping": "위 진입 문턱이 곧 감쇠다.",
            },
        },
        "thresholds": thresholds.to_dict(),
        "soft_filter": {
            "lambda": config.lam,
            "epsilon": config.epsilon,
            "confirmation_weeks": config.confirmation_weeks,
            "immediate_margin": config.immediate_margin,
            "separation_floor": config.separation_floor,
        },
        "sign_quadrant": {name: list(value) for name, value in SIGN_QUADRANT.items()},
        "phases_listed": list(PHASES),
        "sub_normal_expansion_is_possible": True,
        "sub_normal_expansion_mechanism": (
            "침체 증거가 진입 문턱에 못 미치고 회복 증거가 없으면, 두 감쇠의 잔여 몫이 "
            "확장·후퇴로 흘러간다. 그래서 수준이 정상 아래인 주에도 확장기가 이길 수 있다. "
            "이것은 구성의 성질이므로 별도 진단으로 세되 국면 순서 위반으로 취급하지 않는다."
        ),
    }
