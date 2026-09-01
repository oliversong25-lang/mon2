"""청구건수를 뺀 경기 심각도와 한 항목 제거 강건성.

영역 폭 게이트는 "지금 침체인가"를 넓이로 묻는다. 그런데 넓이는 그 시점에 무엇이
발표돼 있었는지에 좌우된다. 2020년 실시간에서는 붕괴가 실업수당 한 영역에만 보였고
핵심 동행지표는 아직 아무것도 말하지 않았다.

그래서 폭이 모자란 주를 판단하려면 **넓이와 별개로** 얼마나 심각한지를 재야 하고,
그 심각도는 두 조건을 만족해야 한다.

1. 실업수당에 기대지 않아야 한다 → 핵심 동행지표만으로 다시 계산한다.
2. 한 항목이 혼자 만든 값이 아니어야 한다 → 가장 큰 지표·영역을 하나 빼고도
   같은 값이 남는지 본다.

여기서 나오는 값은 모두 Y와 같은 단위다. 합성요인을 같은 좌표 척도로 나누기 때문이다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: 실업수당 두 계열이 속한 영역. 핵심 동행 영역이 아니라 주간 가교다.
BRIDGE_DOMAIN = "weekly_bridge"


def _level(total: float, weight: float, scale: float) -> float:
    """가중치를 다시 1로 맞춘 합성값을 좌표 척도로 나눈다."""

    if weight <= 0 or not np.isfinite(scale) or scale <= 0:
        return float("nan")
    return float((total / weight) / scale)


def severity_details(
    contributions: pd.DataFrame,
    effective_weights: dict[pd.Timestamp, dict[str, float]],
    domain_of: dict[str, str],
    scale: pd.Series,
    index: pd.Index,
) -> pd.DataFrame:
    """주별 핵심 심각도와 한 항목 제거 심각도를 계산한다.

    ``effective_weights``는 그 주에 실제로 쓰인 가중치다. 결측 지표가 있으면 가중치가
    재정규화되므로, 기여도만으로는 원래 가중치를 되돌릴 수 없다. 그래서 함께 받는다.
    """

    rows: list[dict[str, Any]] = []
    aligned = contributions.reindex(index, method="ffill")
    for week in index:
        timestamp = pd.Timestamp(str(week))
        weights = effective_weights.get(timestamp, {})
        row = aligned.loc[[timestamp]]
        blank = {
            "core_level": float("nan"),
            "core_negative_domains": 0,
            "leave_one_indicator_level": float("nan"),
            "leave_one_domain_level": float("nan"),
            "core_indicator_count": 0,
        }
        if not weights or row.empty:
            rows.append(blank)
            continue
        values = row.iloc[0]
        core: dict[str, float] = {}
        core_weight: dict[str, float] = {}
        for indicator, weight in weights.items():
            name = str(indicator)
            if domain_of.get(name) == BRIDGE_DOMAIN or name not in values.index:
                continue
            value = values[name]
            if pd.isna(value):
                continue
            core[name] = float(value)
            core_weight[name] = float(weight)
        total_weight = sum(core_weight.values())
        if not core or total_weight <= 0:
            rows.append(blank)
            continue
        total = sum(core.values())
        current_scale = float(scale.get(timestamp, float("nan")))

        domain_total: dict[str, float] = {}
        domain_weight: dict[str, float] = {}
        for name, value in core.items():
            domain = domain_of.get(name, name)
            domain_total[domain] = domain_total.get(domain, 0.0) + value
            domain_weight[domain] = domain_weight.get(domain, 0.0) + core_weight[name]

        largest_indicator = max(core, key=lambda key: abs(core[key]))
        largest_domain = max(domain_total, key=lambda key: abs(domain_total[key]))
        rows.append(
            {
                "core_level": _level(total, total_weight, current_scale),
                "core_negative_domains": int(
                    sum(1 for value in domain_total.values() if value < 0)
                ),
                "leave_one_indicator_level": _level(
                    total - core[largest_indicator],
                    total_weight - core_weight[largest_indicator],
                    current_scale,
                ),
                "leave_one_domain_level": _level(
                    total - domain_total[largest_domain],
                    total_weight - domain_weight[largest_domain],
                    current_scale,
                ),
                "core_indicator_count": len(core),
            }
        )
    return pd.DataFrame(rows, index=pd.Index(index))


def systemic_override(
    severity: pd.DataFrame,
    negative_domains: pd.Series,
    ungated_contraction: pd.Series,
    dynamic_factor: pd.Series,
    minimum_domains: float,
    config: dict[str, Any],
) -> pd.Series:
    """폭이 한 단계 모자란 주에 한해 침체 판정을 허용할지 결정한다.

    조건은 모두 1995~2012 개발구간에서만 정했다. 팬데믹 날짜·분기·상수는 없다.
    """

    required = int(minimum_domains) - 1
    active = negative_domains.eq(required)
    active &= severity["core_negative_domains"].ge(int(config["minimum_core_negative_domains"]))
    active &= severity["core_level"].le(float(config["core_level"]))
    active &= severity["leave_one_indicator_level"].le(float(config["leave_one_indicator_level"]))
    active &= severity["leave_one_domain_level"].le(float(config["leave_one_domain_level"]))
    active &= ungated_contraction.ge(float(config["minimum_ungated_contraction_probability"]))
    if bool(config.get("require_dynamic_agreement", True)):
        active &= dynamic_factor.lt(0.0)
    return active.fillna(False).astype(bool)
