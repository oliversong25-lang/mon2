"""확신도 진단. 국면을 바꾸지 않고, 그 판정을 얼마나 믿을 수 있는지만 말한다.

세 단계뿐이고 이유 코드를 항상 함께 낸다. 점수를 합성해 소수점을 붙이면 정밀해
보이지만 근거가 없다. **이것은 보정된 확률이 아니다.** 보정을 실제로 시연한 적이
없으므로 확률이라고 부르지 않는다.

위험 조건은 각각 하나의 사실이고, 몇 개가 성립하는지로 단계를 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .boundary import BoundaryView
from .domains import DomainReading

#: 합성요인과 동적요인 일치도 exp(-|차이|)의 이력 p25(1995~2026 최신 수정치).
WEAK_AGREEMENT: float = 0.6516

#: 자료가 오래됐다고 볼 기준. 지표별 `max_age_weeks`를 넘긴 영역이 있으면 위험 조건이다.
STALE_MULTIPLIER: float = 1.0


@dataclass(frozen=True)
class ConfidenceView:
    confidence_level: str
    confidence_reasons: list[str] = field(default_factory=list)
    calibrated: bool = False
    note: str = "확신도는 보정된 확률이 아니다. 보정을 시연한 적이 없다."


def confidence_view(
    boundary: BoundaryView,
    readings: list[DomainReading],
    data_status: str,
    composite_dynamic_agreement: float,
    stale_domains: list[str],
) -> ConfidenceView:
    """위험 조건의 개수로 단계를 정한다. 성립한 조건은 전부 이유로 남긴다."""

    reasons: list[str] = []
    if boundary.boundary_flag:
        reasons.append("small winner-runner-up gap")
    if data_status != "official":
        reasons.append(f"data status {data_status}")
    if np.isfinite(composite_dynamic_agreement) and composite_dynamic_agreement < WEAK_AGREEMENT:
        reasons.append("weak composite-dynamic agreement")
    supporting = sum(1 for reading in readings if reading.supports_official_phase)
    opposing = sum(1 for reading in readings if reading.opposes_official_phase)
    if opposing >= supporting:
        names = [reading.domain for reading in readings if reading.opposes_official_phase]
        reasons.append(
            f"domains disagree ({', '.join(names)} oppose)" if names else "no domain supports"
        )
    if stale_domains:
        reasons.append(f"stale or missing data ({', '.join(sorted(stale_domains))})")

    if data_status == "withheld":
        level = "low"
    elif len(reasons) == 0:
        level = "high"
    elif len(reasons) == 1:
        level = "medium"
    else:
        level = "low"
    return ConfidenceView(confidence_level=level, confidence_reasons=reasons)
