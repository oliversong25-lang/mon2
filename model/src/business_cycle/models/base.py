"""경기 수준 모델의 공통 반환 형식."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class FactorEstimate:
    """주간 잠재 경기요인과 모델 메타데이터."""

    factor: pd.Series
    contributions: pd.DataFrame
    metadata: dict[str, object] = field(default_factory=dict)


class FactorModel:
    """합성·동적 모델이 공유하는 최소 인터페이스."""

    def fit_filter(self, events: pd.DataFrame) -> FactorEstimate:
        raise NotImplementedError
