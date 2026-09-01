"""§10·§11. 잠정 채택일 때만 만드는 산출물.

현재상태 계약은 국면을 **정확히 하나** 낸다. `expansion or slowdown`, `near recovery`,
`between phases` 같은 모호한 라벨을 내지 않는다. 전환 위험은 부차적으로만 적는다.

투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다. 이 단계에서도, 산출물에서도.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..current_state.domains import COINCIDENT_DOMAINS, DOMAIN_MEMBERS, DOMAINS
from ..four_phase.contract import FORBIDDEN_TOKENS, PHASES
from ..operational_review.review import transition_watch

#: §10이 요구한 필드. 하나라도 빠지면 계약 위반이다.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "as_of_date",
    "official_current_phase",
    "raw_current_phase",
    "phase_status",
    "evidence_quality",
    "phase_separation",
    "activity_level",
    "activity_momentum",
    "transition_watch",
    "recession_alert",
    "domain_evidence",
    "domain_freshness",
    "breadth",
    "concentration",
    "model_status",
    "recovery_latency_warning",
    "known_limitations",
    "interpretation_boundaries",
)

#: 해석 경계 둘. 모델의 계산 결함이 아니라 **산출물이 무엇을 뜻할 수 있는지의 한계**다.
#:
#: 평평한 문자열 목록에도 넣고 구조로도 싣는다. 목록에 넣는 이유는 한계가 빠지면 빌드가
#: 멈추는 검사가 이미 그 목록에 걸려 있기 때문이고, 구조로도 싣는 이유는 화면이 어느
#: 것을 국면 판독 옆에 붙여야 하는지 골라야 하기 때문이다.
#:
#: ``surface``가 그 선택을 정한다. B는 "추세 위"를 읽는 사람이 그 자리에서 알아야 하므로
#: 화면에 뜨고, A는 밸류에이션을 다루는 사람을 위한 해석 노트라 문서에 남는다.
INTERPRETATION_BOUNDARIES: Final[tuple[dict[str, str], ...]] = (
    {
        "id": "A",
        "title": "총생산은 기업 이익이 아니다",
        "surface": "documentation",
        "text": (
            "성장순환에서 '절대적 감소는 드물다'는 진술은 **총생산**에 대한 것이며 기업 "
            "이익에 그대로 옮겨지지 않는다. 단기에 비용이 대체로 고정돼 있어 매출이 조금 "
            "줄어도 이익은 훨씬 크게 줄고, 재무 레버리지가 그것을 더 키운다. 총생산이 "
            "줄지 않은 기간에도 총이익이 줄어든 적이 있고 상당수 개별 기업이 적자로 "
            "넘어갔다. 이익 경로와 할인율 경로가 함께 작동하며 어느 쪽이 지배적인지는 "
            "에피소드마다 다르다 — 2008~09년은 이익 주도, 2022년은 할인율 주도였다."
        ),
    },
    {
        "id": "B",
        "title": "순환과 구조를 실시간으로 가르지 못한다",
        "surface": "app_phase_reading",
        "text": (
            "이 모델은 추세 위·아래를 읽지만 그 강약이 순환적인지 구조적인지 실시간으로 "
            "가르지 못한다. 추세는 이동평균이라 성장의 구조적 변화는 결국 추세에 흡수되고 "
            "'추세 위' 판독은 사라지지만, **흡수되기 전의 전환기가 바로 판독이 가장 "
            "어긋나는 때**다. 구조적 상향은 추세가 따라잡을 때까지 순환적 강세로 읽히고, "
            "현재 끝점의 추세는 외삽된 값이라 그 자리의 전환 판정은 특히 잠정적이다. "
            "이것은 이 모델만의 결함이 아니다 — 실시간 산출갭 오측정은 널리 기록된 정책 "
            "오류의 원인이며, 1970년대 생산성 둔화는 당시 순환적 약세로 읽혔다."
        ),
    },
)

#: 모호 라벨 금지. 공식 국면은 이 넷 중 하나여야 한다.
AMBIGUOUS_LABELS: Final[tuple[str, ...]] = (
    "expansion or slowdown",
    "near recovery",
    "between phases",
    "transitioning",
    "mixed",
)


def current_state(
    path: pd.DataFrame,
    decomposition: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """마지막 인과 as-of 주의 현재상태. 판정 보류 주에서는 공식 국면을 내지 않는다."""

    moment = path.index[-1]
    row = path.loc[moment]
    status = str(row["phase_status"])
    official = "" if status == "withheld" else str(row["official_phase"])
    if status != "withheld" and official not in PHASES:
        raise ValueError(f"공식 국면이 정확히 하나가 아닙니다: {official!r}")
    scores = {name: float(str(row[f"filtered_{name}"])) for name in PHASES}

    domain_evidence: dict[str, Any] = {}
    domain_freshness: dict[str, Any] = {}
    for domain in DOMAINS:
        age_column = f"age_{domain}"
        domain_evidence[domain] = {
            "role": "coincident" if domain in COINCIDENT_DOMAINS else "bridge_only",
            "series": list(DOMAIN_MEMBERS[domain]),
        }
        domain_freshness[domain] = {
            "weeks_since_release": float(str(row[age_column]))
            if age_column in path.columns
            else None,
            "new_observation_arrived_this_week": bool(int(str(row[f"arrived_{domain}"])))
            if f"arrived_{domain}" in path.columns
            else None,
        }
    domain_evidence["labor_stress"]["level"] = float(str(row["labor_stress_level"]))
    domain_evidence["labor_stress"]["momentum"] = float(str(row["labor_stress_momentum"]))

    band = decomposition["calendar_band"]
    latency = decomposition["calendar_recovery_latency_weeks"]
    return {
        "as_of_date": str(pd.Timestamp(moment).date()),
        "official_current_phase": official,
        "raw_current_phase": str(row["raw_phase"]),
        "phase_status": status,
        "evidence_quality": "high" if bool(row["evidence_quality_high"]) else "low",
        "phase_separation": round(float(str(row["phase_separation"])), 6),
        "activity_level": round(float(str(row["activity_level"])), 6),
        "activity_momentum": round(float(str(row["activity_momentum"])), 6),
        "transition_watch": transition_watch(scores, official),
        "recession_alert": {
            "level": str(row["recession_alert"]),
            "character": str(row["recession_alert_character"]),
        },
        "domain_evidence": domain_evidence,
        "domain_freshness": domain_freshness,
        "breadth": {
            "confirming_coincident_domains": int(str(row["confirming_domains"])),
            "negative_level_domains": int(str(row["negative_level_domains"])),
            "positive_momentum_domains": int(str(row["positive_momentum_domains"])),
            "minimum_required_for_official_contraction": 2,
            # 폭은 집중도의 **부분적** 화면이다. 진짜 광범위한 확장은 다섯 도메인을 모두
            # 들어올리고, 한 산업의 호황은 일부만 들어올린다. 자동 분류로 만들지 않는다 —
            # 판정이 아니라 주의 표시다.
            "partial_concentration_screen": (
                "추세 위 판정과 함께 confirming_coincident_domains가 낮거나 폭이 좁게 "
                "나오면, 강세가 경제 전반이 아니라 일부에 몰려 있을 가능성을 함께 본다. "
                "이것은 자동 분류가 아니라 주의 표시이며, 좁은 폭이 곧 구조적 강세라는 "
                "뜻은 아니다."
            ),
        },
        "concentration": round(float(str(row["concentration"])), 6),
        "model_status": "provisional",
        "recovery_latency_warning": {
            "band": band,
            "calendar_recovery_latency_weeks": latency,
            "evidence_availability_adjusted_latency_weeks": decomposition[
                "evidence_availability_adjusted_latency_weeks"
            ],
            "limitation": decomposition["limitation_label"],
            "meaning": (
                "저점 이후 회복 인식이 최대 "
                f"{latency}주까지 늦을 수 있다. 이 모델은 저점을 실시간으로 짚지 못한다."
            ),
        },
        "known_limitations": [
            "엄격 실시간 침체 에피소드가 하나뿐이다. 실시간 침체 성능을 일반화할 수 없다.",
            "2020년 결과는 이미 들여다봤으므로 손대지 않은 홀드아웃이 아니다.",
            "2013-06-14 이전에는 진짜 빈티지가 없어 실시간 경로가 존재하지 않는다.",
            "v1.1은 최신 수정치 규약 아래에서 기각된 상태로 남아 있다.",
            "잠정 운영이며 최종 검증이 아니다.",
            *[entry["text"] for entry in INTERPRETATION_BOUNDARIES],
            "이 산출물은 투자 판단·섹터·비중·종목·매매 지시를 담지 않는다.",
        ],
        # 같은 두 항목을 구조로도 싣는다. 평평한 문자열 목록만으로는 화면이 어느 것을
        # 국면 판독 옆에 붙여야 하는지 고를 수 없다.
        "interpretation_boundaries": [dict(entry) for entry in INTERPRETATION_BOUNDARIES],
        "provenance": {
            "frozen_config_sha256": provenance["hashes"]["v1_1_config"],
            "source_commit": provenance["expected_source_commit"],
            "evidence_source": "strict_alfred_real_time_cache_only",
        },
    }


def _walk(payload: Any, seen: list[str]) -> None:
    """키와 **문자열 값**을 모두 재귀로 훑는다. 필드 이름만 보면 문장으로 새어 나간다."""

    if isinstance(payload, dict):
        for key, value in payload.items():
            seen.append(str(key))
            _walk(value, seen)
    elif isinstance(payload, list):
        for item in payload:
            _walk(item, seen)
    elif isinstance(payload, str):
        seen.append(payload)


def validate_contract(payload: dict[str, Any]) -> None:
    """계약 검사. 필드 누락, 모호 라벨, 투자 판단 어휘를 모두 막는다."""

    missing = [name for name in REQUIRED_FIELDS if name not in payload]
    if missing:
        raise ValueError(f"현재상태 계약에 빠진 필드가 있습니다: {missing}")

    seen: list[str] = []
    _walk(payload, seen)
    lowered = [item.lower() for item in seen]
    banned = sorted({token for token in FORBIDDEN_TOKENS for item in lowered if token in item})
    if banned:
        raise ValueError(f"투자 판단에 해당하는 필드나 문장은 둘 수 없습니다: {banned}")

    phase = str(payload["official_current_phase"])
    if payload["phase_status"] == "withheld":
        if phase:
            raise ValueError("판정 보류 주에는 공식 국면을 낼 수 없습니다")
        return
    if phase.lower() in AMBIGUOUS_LABELS or phase not in PHASES:
        raise ValueError(f"공식 국면이 모호하거나 허용되지 않습니다: {phase!r}")


def report(payload: dict[str, Any]) -> str:
    """사람이 읽는 보고서. 첫 줄이 반드시 국면 하나에 답한다."""

    phase = payload["official_current_phase"] or "withheld"
    warning = payload["recovery_latency_warning"]
    lines = [
        f"Current official U.S. phase: {phase}",
        "",
        f"- as-of: {payload['as_of_date']} · 상태 {payload['phase_status']} · "
        f"증거 품질 {payload['evidence_quality']}",
        f"- 원시 국면 {payload['raw_current_phase']} · 분리도 {payload['phase_separation']}",
        f"- 활동 수준 {payload['activity_level']} · 모멘텀 {payload['activity_momentum']}",
        f"- 침체 경보 {payload['recession_alert']['level']} "
        f"({payload['recession_alert']['character']})",
        f"- 전환 감시(부차적) {payload['transition_watch']}",
        f"- 확증 동행 도메인 {payload['breadth']['confirming_coincident_domains']} · "
        f"집중도 {payload['concentration']}",
        "",
        f"모델 상태: {payload['model_status']}. 최종 검증이 아니다.",
        f"회복 인식 지연 경고: {warning['band']} · 달력 "
        f"{warning['calendar_recovery_latency_weeks']}주 · 조정 "
        f"{warning['evidence_availability_adjusted_latency_weeks']}주 "
        f"({warning['limitation']})",
        "",
        "알려진 한계",
        "",
        *[f"- {item}" for item in payload["known_limitations"]],
        "",
    ]
    return "\n".join(lines)


def operational_manifest(
    provenance: dict[str, Any], decision: dict[str, Any], decomposition: dict[str, Any]
) -> dict[str, Any]:
    """§9-A의 운영 등록부. 바뀌지 않은 모델을 그대로 가리킨다."""

    return {
        "model": "us_four_phase_v1",
        "configuration": "configs/four_phase_v1_1.yaml",
        "frozen_config_sha256": provenance["hashes"]["v1_1_config"],
        "source_commit": provenance["expected_source_commit"],
        "model_status": "provisional",
        "adoption_basis": "operational_real_time_first",
        "not_a_retrospective_latest_vintage_validation": True,
        "historical_v1_1_status_under_the_latest_vintage_protocol": "rejected",
        "new_model_logic_created": False,
        "parameters_changed": False,
        "strict_real_time_recession_episodes": 1,
        "strict_real_time_recession_episode_names": ["recession_2020"],
        "recovery_latency_band": decomposition["calendar_band"],
        "recovery_latency_weeks": decomposition["calendar_recovery_latency_weeks"],
        "limitation_label": decomposition["limitation_label"],
        "decision": decision["classification"],
        "is_final_validation": False,
        "is_fully_validated": False,
        "produces_investment_recommendations": False,
    }
