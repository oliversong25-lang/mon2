"""국가 등록부. 스키마는 공통, 결과는 모델이 있는 나라만 낸다.

미국·한국·중국이 같은 출력 계약을 쓰지만, 계약이 같다고 결과가 생기는 것은 아니다.
한국·중국은 인과 모델이 없으므로 `not_implemented`이며 현재 국면을 만들지 않는다.
없는 판정을 채워 넣으면 스키마만 채워지고 사실은 사라진다.

국가 간 수혜 판단은 여기서 하지 않는다. 각 나라는 자기 공식 국면만 낸다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CountryRegistration:
    country: str
    status: str
    model_baseline: str | None
    note: str
    required_domains: tuple[str, ...] = ()
    candidate_series: tuple[dict[str, str], ...] = field(default_factory=tuple)


US = CountryRegistration(
    country="US",
    status="implemented",
    model_baseline="candidate_h_breadth_gate",
    note=(
        "동결된 인과 모델이 있고 최신 수정치·엄격 ALFRED 검증을 모두 마쳤다. "
        "단계 A-5의 실시간 침체 탐지 게이트는 통과하지 못했으며 그 판정은 유지된다."
    ),
)

KR = CountryRegistration(
    country="KR",
    status="not_implemented",
    model_baseline=None,
    note="인과 국가 모델이 없다. 현재 국면을 만들지 않는다.",
    required_domains=("employment", "income", "production", "consumption", "claims"),
    candidate_series=(
        {
            "domain": "employment",
            "candidate": "통계청 경제활동인구조사 취업자수",
            "requirement": "월간, 발표지연 확인, 계절조정, 수정 이력(빈티지) 확보",
        },
        {
            "domain": "income",
            "candidate": "가계동향조사 실질소득 또는 국민계정 실질 GNI",
            "requirement": "분기 계열은 주간 가교 규칙을 따로 정해야 한다",
        },
        {
            "domain": "production",
            "candidate": "전산업생산지수 / 광공업생산지수",
            "requirement": "월간, 기준연도 개편 시 소급 수정 처리 필요",
        },
        {
            "domain": "consumption",
            "candidate": "소매판매액지수 / 서비스업생산지수",
            "requirement": "월간, 명목·실질 구분",
        },
        {
            "domain": "claims",
            "candidate": "고용보험 구직급여 신규신청자수",
            "requirement": "주간 가교로 쓰려면 주간 또는 월간 고빈도 확보 필요",
        },
    ),
)

CN = CountryRegistration(
    country="CN",
    status="not_implemented",
    model_baseline=None,
    note="인과 국가 모델이 없다. 현재 국면을 만들지 않는다.",
    required_domains=("employment", "income", "production", "consumption", "claims"),
    candidate_series=(
        {
            "domain": "employment",
            "candidate": "도시조사실업률 / 도시 신규취업자수",
            "requirement": "월간, 계열 정의 변경 이력 확인 필요",
        },
        {
            "domain": "income",
            "candidate": "주민 1인당 가처분소득",
            "requirement": "분기·누계 발표라 주간 가교와 누계 해제 규칙이 필요하다",
        },
        {
            "domain": "production",
            "candidate": "규모이상 공업부가가치 증가율",
            "requirement": "월간, 1~2월 합산 발표 처리 필요",
        },
        {
            "domain": "consumption",
            "candidate": "사회소비재 소매총액",
            "requirement": "월간, 1~2월 합산 발표 처리 필요",
        },
        {
            "domain": "claims",
            "candidate": "(직접 대응 없음)",
            "requirement": "주간 가교 대체 지표를 별도로 정의해야 한다",
        },
    ),
)

REGISTRY: dict[str, CountryRegistration] = {"US": US, "KR": KR, "CN": CN}


def registration(country: str) -> CountryRegistration:
    if country not in REGISTRY:
        raise KeyError(f"등록되지 않은 국가입니다: {country}")
    return REGISTRY[country]


def not_implemented_payload(country: str) -> dict[str, Any]:
    """모델이 없는 나라의 출력. 국면 자리를 비워 두고 이유를 남긴다."""

    entry = registration(country)
    if entry.status == "implemented":
        raise ValueError(f"{country}는 구현돼 있습니다. 이 경로를 쓰지 않습니다")
    return {
        "country": entry.country,
        "status": entry.status,
        "as_of_date": None,
        "official_broad_phase": None,
        "official_detailed_phase": None,
        "reason": entry.note,
        "required_domains": list(entry.required_domains),
        "candidate_series": [dict(item) for item in entry.candidate_series],
    }
