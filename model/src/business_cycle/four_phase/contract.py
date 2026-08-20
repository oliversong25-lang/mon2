"""4국면 출력 계약. 국가가 바뀌어도 의미는 같아야 한다.

이 층은 경기국면 진단에서 끝난다. 섹터·자산군·비중·목표가·매매 문구·종목을 만들지
않으며, 그런 필드가 출력에 들어오면 검증이 거부한다. 사용자의 투자 판단은 이 출력
뒤에서 사용자가 수행한다.
"""

from __future__ import annotations

from typing import Any, Final

PHASES: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")

PHASE_STATUS: Final[tuple[str, ...]] = ("official", "preliminary", "withheld")

EVIDENCE_QUALITY: Final[tuple[str, ...]] = ("high", "medium", "low")

TRANSITION_WATCH: Final[tuple[str, ...]] = (
    "none",
    "toward_recovery",
    "toward_expansion",
    "toward_slowdown",
    "toward_contraction",
)

RECESSION_ALERT: Final[tuple[str, ...]] = ("none", "watch", "elevated", "active")

#: §3이 요구한 필드. 순서가 곧 사용자가 읽는 순서다.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "official_current_phase",
    "phase_status",
    "phase_separation",
    "evidence_quality",
    "activity_level",
    "activity_momentum",
    "domain_breadth",
    "contribution_concentration",
    "supporting_domains",
    "opposing_domains",
    "mixed_domains",
    "transition_watch",
    "recession_alert",
    "as_of_date",
    "latest_observation_by_domain",
    "known_limitations",
)

#: 이 모델이 절대 만들지 않는 것. 이름이 들어오기만 해도 막는다.
FORBIDDEN_TOKENS: Final[tuple[str, ...]] = (
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
    "stock",
    "sector_pick",
    "asset_class",
    "trade",
    "order",
)

#: 애매한 공식 라벨. 경계에서도 공식 국면은 하나다.
AMBIGUOUS_MARKERS: Final[tuple[str, ...]] = (" or ", "/", "near ", "between", "mixed")


class ContractViolation(ValueError):
    """계약을 어긴 출력. 조용히 고치지 않고 거부한다."""


def _walk_keys(payload: Any, seen: list[str]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            seen.append(str(key))
            _walk_keys(value, seen)
    elif isinstance(payload, list):
        for item in payload:
            _walk_keys(item, seen)


def validate(payload: dict[str, Any]) -> None:
    """출력이 계약을 지키는지 검사한다. 어기면 예외를 던진다."""

    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise ContractViolation(f"필수 필드가 없습니다: {missing}")

    keys: list[str] = []
    _walk_keys(payload, keys)
    lowered = [key.lower() for key in keys]
    banned = sorted({token for token in FORBIDDEN_TOKENS for key in lowered if token in key})
    if banned:
        raise ContractViolation(f"투자 판단에 해당하는 필드는 둘 수 없습니다: {banned}")

    status = str(payload["phase_status"])
    if status not in PHASE_STATUS:
        raise ContractViolation(f"국면 상태가 어휘 밖입니다: {status}")
    phase = payload["official_current_phase"]
    if status == "withheld":
        if phase is not None:
            raise ContractViolation("판정 보류일 때는 공식 국면을 내지 않습니다")
    else:
        if phase not in PHASES:
            raise ContractViolation(f"공식 국면이 어휘 밖입니다: {phase}")
        for marker in AMBIGUOUS_MARKERS:
            if marker in str(phase).lower():
                raise ContractViolation("공식 국면은 하나여야 합니다")
    if payload["evidence_quality"] not in EVIDENCE_QUALITY:
        raise ContractViolation("증거 품질이 어휘 밖입니다")
    if payload["transition_watch"] not in TRANSITION_WATCH:
        raise ContractViolation("전환 감시가 어휘 밖입니다")
    if payload["recession_alert"] not in RECESSION_ALERT:
        raise ContractViolation("침체 경보가 어휘 밖입니다")
    if payload["recession_alert"] in PHASES:
        raise ContractViolation("침체 경보를 다섯 번째 공식 국면처럼 쓸 수 없습니다")


def country_schema() -> dict[str, Any]:
    """국가 확장용 공통 계약. 지표와 임계값은 달라도 의미는 같아야 한다."""

    return {
        "phases": list(PHASES),
        "phase_status": list(PHASE_STATUS),
        "evidence_quality": list(EVIDENCE_QUALITY),
        "transition_watch": list(TRANSITION_WATCH),
        "recession_alert": list(RECESSION_ALERT),
        "required_fields": list(REQUIRED_FIELDS),
        "forbidden_tokens": list(FORBIDDEN_TOKENS),
        "semantics": {
            "recovery": "현재 활동이 정상 아래로 약하지만, 개선이 넓고 지금 관측된다",
            "expansion": "현재 활동이 넓게 양호하고 모멘텀이 안정적이거나 개선 중이다",
            "slowdown": "광범위한 침체는 아니지만 성장 모멘텀이 경제의 상당 부분에서 약해지고 있다",
            "contraction": "현재 활동이 여러 동행 도메인에 걸쳐 넓고 상당하게 하락 중이다",
            "evidence_quality": "판정의 근거가 얼마나 든든한가. 보정된 확률이 아니다",
            "transition_watch": "인접 국면 쪽으로의 이동 조짐. 공식 국면을 바꾸지 않는다",
            "recession_alert": "침체 스트레스 경보. 공식 국면과 별개이며 다섯 번째 국면이 아니다",
        },
        "notes": [
            "official_current_phase는 현재상태 측정이며 예측이 아니다.",
            "이 모델은 투자 판단을 만들지 않는다. 그 해석은 사용자가 수행한다.",
            "국가별 지표와 임계값은 달라도 위 의미는 같아야 한다.",
        ],
    }
