"""해석층 출력 계약. 국면을 다시 계산하지 않고, 이미 정해진 것을 설명한다.

이 층은 경기 모델을 건드리지 않는다. 모델이 고른 공식 국면을 그대로 받아, 사람이
읽고 판단할 수 있는 형태로 옮긴다. 그래서 여기에 있는 규칙은 전부 **표시 전용**이다.
경계 표시도, 전환 감시도, 확신도도 국면을 바꾸지 않는다.

투자 판단은 이 층의 책임이 아니다. 종목·섹터·비중·목표가·매매 판단을 만들지 않으며,
그런 필드가 스키마에 들어오면 검증에서 거부한다.
"""

from __future__ import annotations

from typing import Any, Final

#: 모델이 쓰는 세부국면 코드와, 계약에서 쓰는 표기. 철자만 다르고 순서·의미가 같다.
#: 모델 코드를 계약 표기로 바꾸는 것은 이름표 교체일 뿐이며 승자를 재계산하지 않는다.
PHASE_LABEL_MAP: Final[dict[str, str]] = {
    "recovery_early": "recovery_early",
    "recovery_mid": "recovery_middle",
    "recovery_late": "recovery_late",
    "expansion_early": "expansion_early",
    "expansion_mid": "expansion_middle",
    "expansion_late": "expansion_late",
    "slowdown_early": "slowdown_early",
    "slowdown_mid": "slowdown_middle",
    "slowdown_late": "slowdown_late",
    "contraction_early": "contraction_early",
    "contraction_mid": "contraction_middle",
    "contraction_late": "contraction_late",
}

#: 순환 순서. 인접 국면을 정할 때 쓴다. 모델의 전이행렬 순서와 같다.
PHASE_ORDER: Final[tuple[str, ...]] = tuple(PHASE_LABEL_MAP)

BROAD_PHASES: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")

DETAILED_PHASES: Final[tuple[str, ...]] = tuple(PHASE_LABEL_MAP.values())

#: 경기 본체의 경제영역. 산업이 아니다. 둘을 같은 이름으로 부르지 않는다.
ECONOMIC_DOMAINS: Final[tuple[str, ...]] = (
    "employment",
    "income",
    "production",
    "consumption",
    "claims",
)

#: 설정의 영역 이름을 계약 이름으로 옮긴다. `weekly_bridge`는 실업수당 청구건수다.
DOMAIN_LABEL_MAP: Final[dict[str, str]] = {
    "employment": "employment",
    "income": "income",
    "production": "production",
    "consumption": "consumption",
    "weekly_bridge": "claims",
}

CONFIDENCE_LEVELS: Final[tuple[str, ...]] = ("high", "medium", "low")

RECESSION_STATUS: Final[tuple[str, ...]] = ("yes", "no", "withheld")

INDUSTRY_BREADTH_STATUS: Final[tuple[str, ...]] = (
    "broad",
    "mixed",
    "concentrated",
    "not_available",
)

INDUSTRY_CONCENTRATION_STATUS: Final[tuple[str, ...]] = (
    "broad",
    "mixed",
    "concentrated",
    "not_measured",
)

COUNTRY_STATUS: Final[tuple[str, ...]] = ("implemented", "not_implemented")

#: §3이 요구한 필드. 순서가 곧 사용자가 읽는 순서다.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "country",
    "as_of_date",
    "official_broad_phase",
    "official_detailed_phase",
    "recession_status",
    "official_phase_probability",
    "runner_up_phase",
    "runner_up_probability",
    "winner_runner_up_gap",
    "confidence_level",
    "boundary_flag",
    "boundary_reason",
    "transition_watch",
    "transition_direction",
    "data_status",
    "supporting_domains",
    "opposing_domains",
    "domain_breadth",
    "industry_breadth_status",
    "industry_concentration_status",
    "short_explanation",
    "limitations",
)

#: 이 층이 절대 만들지 않는 것. 이름이 들어오기만 해도 검증에서 막는다.
#: 사용자의 투자 과정은 모델 밖에 있고, 모델의 책임은 경기 진단에서 끝난다.
FORBIDDEN_FIELD_TOKENS: Final[tuple[str, ...]] = (
    "recommend",
    "recommendation",
    "buy",
    "sell",
    "hold_rating",
    "target_price",
    "price_target",
    "valuation",
    "intrinsic_value",
    "fair_value",
    "allocation",
    "portfolio",
    "weight_change",
    "overweight",
    "underweight",
    "position_size",
    "ticker",
    "security",
    "stock_pick",
    "trade",
    "order",
)


class SchemaViolation(ValueError):
    """계약을 어긴 출력. 조용히 고치지 않고 거부한다."""


def label_detailed_phase(model_code: str) -> str:
    """모델 세부국면 코드를 계약 표기로 옮긴다.

    승자를 다시 고르지 않는다. 모르는 코드는 지어내지 않고 거부한다.
    """

    if model_code not in PHASE_LABEL_MAP:
        raise SchemaViolation(f"모델이 내지 않는 세부국면 코드입니다: {model_code}")
    return PHASE_LABEL_MAP[model_code]


def label_domain(configured_domain: str) -> str:
    """설정의 영역 이름을 계약 이름으로 옮긴다."""

    if configured_domain not in DOMAIN_LABEL_MAP:
        raise SchemaViolation(f"설정에 없는 경제영역입니다: {configured_domain}")
    return DOMAIN_LABEL_MAP[configured_domain]


def adjacent_phases(model_code: str) -> tuple[str, str]:
    """순환 순서에서 한 칸 앞뒤의 국면. (다음, 이전)."""

    if model_code not in PHASE_ORDER:
        raise SchemaViolation(f"모델이 내지 않는 세부국면 코드입니다: {model_code}")
    position = PHASE_ORDER.index(model_code)
    size = len(PHASE_ORDER)
    return PHASE_ORDER[(position + 1) % size], PHASE_ORDER[(position - 1) % size]


def _walk_keys(payload: Any, seen: list[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            seen.append(str(key))
            _walk_keys(value, seen)
    elif isinstance(payload, list):
        for item in payload:
            _walk_keys(item, seen)


def validate_output(payload: dict[str, Any]) -> None:
    """출력이 계약을 지키는지 검사한다. 어기면 예외를 던진다.

    두 가지를 본다. 요구된 필드가 모두 있고 값이 허용된 어휘 안에 있는가, 그리고
    투자 판단에 해당하는 필드가 어디에도 없는가. 후자는 중첩된 곳까지 훑는다.
    """

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise SchemaViolation(f"필수 필드가 없습니다: {missing}")

    keys: list[str] = []
    _walk_keys(payload, keys)
    lowered = [key.lower() for key in keys]
    banned = sorted({token for token in FORBIDDEN_FIELD_TOKENS for key in lowered if token in key})
    if banned:
        raise SchemaViolation(f"투자 판단에 해당하는 필드는 둘 수 없습니다: {banned}")

    if payload["official_broad_phase"] not in BROAD_PHASES:
        raise SchemaViolation(f"대국면이 어휘 밖입니다: {payload['official_broad_phase']}")
    if payload["official_detailed_phase"] not in DETAILED_PHASES:
        raise SchemaViolation(f"세부국면이 어휘 밖입니다: {payload['official_detailed_phase']}")
    if not payload["official_detailed_phase"].startswith(payload["official_broad_phase"]):
        raise SchemaViolation("세부국면과 대국면이 어긋납니다")
    if payload["recession_status"] not in RECESSION_STATUS:
        raise SchemaViolation(f"침체 상태가 어휘 밖입니다: {payload['recession_status']}")
    if payload["confidence_level"] not in CONFIDENCE_LEVELS:
        raise SchemaViolation(f"확신도가 어휘 밖입니다: {payload['confidence_level']}")
    if payload["industry_breadth_status"] not in INDUSTRY_BREADTH_STATUS:
        raise SchemaViolation("산업 폭 상태가 어휘 밖입니다")
    if payload["industry_concentration_status"] not in INDUSTRY_CONCENTRATION_STATUS:
        raise SchemaViolation("산업 집중도 상태가 어휘 밖입니다")
    if payload["runner_up_phase"] == payload["official_detailed_phase"]:
        raise SchemaViolation("2순위가 공식 국면과 같을 수 없습니다")
    # 애매한 공식 라벨을 막는다. 경계에서도 공식 국면은 하나다.
    for forbidden in (" or ", "between", "undetermined", "mixed"):
        if forbidden in str(payload["official_detailed_phase"]).lower():
            raise SchemaViolation("공식 국면은 하나여야 합니다")
