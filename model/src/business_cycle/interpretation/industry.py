"""산업 폭·집중도 어댑터. 자료가 없으면 없다고 말한다.

경제영역 폭과 산업 폭은 다른 것이다. 지금 모델이 쓰는 7개 지표는 전부 **총량**
계열이고, 거기서 산업별 상태를 추론할 수는 없다. 총량으로 산업 폭을 흉내 내면
근거 없는 숫자가 하나 더 생길 뿐이다.

그래서 이 모듈은 두 가지만 한다. 필요한 계열이 실제로 있는지 확인하고, 없으면
`not_available`을 낸다. 있으면 별도 진단으로만 보고하며 **공식 국면에 되먹임하지
않는다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

#: 산업 폭을 재려면 있어야 하는 자료. 지금은 어느 것도 저장소에 없다.
#: `series_id`는 FRED 기준 후보이며, 실제로 붙이려면 각각 원빈도·발표지연·수정
#: 이력까지 확인해야 한다.
REQUIRED_INDUSTRY_SERIES: tuple[dict[str, str], ...] = (
    {
        "dimension": "payroll_employment_breadth",
        "series_id": "CES_DIFFUSION_1MO",
        "description": "민간 고용 확산지수(업종별 증가 비중)",
        "why": "고용 증가가 몇 개 업종에 걸쳐 있는지",
    },
    {
        "dimension": "manufacturing_vs_services",
        "series_id": "MANEMP / SRVPRD",
        "description": "제조업 고용과 서비스생산업 고용",
        "why": "제조와 서비스가 같은 방향인지",
    },
    {
        "dimension": "industrial_production_breadth",
        "series_id": "IPMAN / IPMINE / IPUTIL",
        "description": "산업생산 시장군별 분해",
        "why": "생산 약세가 한 군에 몰려 있는지",
    },
    {
        "dimension": "consumption_and_sales_breadth",
        "series_id": "RSXFS 업종별 분해",
        "description": "소매판매 업종별 계열",
        "why": "소비 둔화가 광범위한지",
    },
    {
        "dimension": "construction_property",
        "series_id": "TTLCONS / HOUST",
        "description": "건설지출·주택착공",
        "why": "부동산·건설이 총량과 다른 신호를 내는지",
    },
    {
        "dimension": "export_vs_domestic",
        "series_id": "EXPGS / 국내최종수요",
        "description": "수출 대 국내 수요",
        "why": "대외·대내 수요가 갈리는지",
    },
    {
        "dimension": "contribution_concentration",
        "series_id": "(파생)",
        "description": "산업별 기여도 집중도",
        "why": "총량 신호가 소수 산업에서 나온 것인지",
    },
)


@dataclass(frozen=True)
class IndustryView:
    """공식 국면에 영향을 줄 수 없는 별도 진단."""

    industry_breadth_status: str
    industry_concentration_status: str
    basis: str
    available_dimensions: int
    required_dimensions: int


def availability_audit(cache_dir: Path, configured_series: list[str]) -> pd.DataFrame:
    """필요한 산업 계열이 실제로 있는지 확인한다. 추정하지 않는다."""

    cached = {path.stem for path in cache_dir.glob("*.csv")} if cache_dir.exists() else set()
    rows: list[dict[str, Any]] = []
    for entry in REQUIRED_INDUSTRY_SERIES:
        identifiers = [token.strip() for token in entry["series_id"].replace("/", " ").split()]
        present = any(token in cached or token in configured_series for token in identifiers)
        rows.append(
            {
                "dimension": entry["dimension"],
                "candidate_series": entry["series_id"],
                "description": entry["description"],
                "diagnostic_purpose": entry["why"],
                "present_in_repository": bool(present),
                "configured_in_model": any(token in configured_series for token in identifiers),
                "cached_locally": any(token in cached for token in identifiers),
                "status": "available" if present else "not_available",
            }
        )
    return pd.DataFrame(rows)


def industry_view(audit: pd.DataFrame) -> IndustryView:
    """자료가 없으면 없다고 낸다. 총량 지표로 대신 계산하지 않는다."""

    available = int((audit["status"] == "available").sum())
    if available == 0:
        return IndustryView(
            industry_breadth_status="not_available",
            industry_concentration_status="not_measured",
            basis=("산업 단위 계열이 저장소에 없다. 총량 지표에서 산업 상태를 추론하지 않는다."),
            available_dimensions=0,
            required_dimensions=int(len(audit)),
        )
    # 자료가 생기면 여기서 별도 진단을 만든다. 그 결과도 공식 국면에 되먹임하지 않는다.
    return IndustryView(
        industry_breadth_status="mixed",
        industry_concentration_status="mixed",
        basis=f"{available}/{len(audit)}개 차원에서 산업 계열 확인. 별도 진단으로만 보고한다.",
        available_dimensions=available,
        required_dimensions=int(len(audit)),
    )
