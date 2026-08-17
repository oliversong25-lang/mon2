"""해석 가능한 영역별 합성 경기요인 기준선."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..preprocessing.frequency import held_signal_matrix
from .base import FactorEstimate, FactorModel


class CompositeFactorModel(FactorModel):
    """가용 신호의 가중치를 재정규화하는 투명한 기준 모델."""

    def __init__(
        self,
        indicator_settings: dict[str, Any],
        constraints: dict[str, Any],
        maturity: dict[str, Any] | None = None,
        robust_clip: float | None = None,
    ) -> None:
        self.settings = indicator_settings
        self.constraints = constraints
        self.maturity = maturity or {"enabled": False}
        self.robust_clip = robust_clip

    def _maturity_weights(self, events: pd.DataFrame, week: pd.Timestamp) -> pd.Series:
        """5년 미만 제외, 5~10년 선형 증가, 10년 이후 완전 반영한다."""

        result = pd.Series(1.0, index=events.columns, dtype=float)
        if not bool(self.maturity.get("enabled", False)):
            return result
        first_available = events.attrs.get("first_available", {})
        excluded = float(self.maturity.get("exclude_years", 5.0))
        full = float(self.maturity.get("full_weight_years", 10.0))
        for column in result.index:
            first = first_available.get(str(column))
            if first is None:
                result[column] = 0.0
                continue
            age_years = max(0.0, (week - pd.Timestamp(first)).days / 365.2425)
            if full <= excluded:
                result[column] = float(age_years >= excluded)
            else:
                result[column] = float(
                    np.clip((age_years - excluded) / (full - excluded), 0.0, 1.0)
                )
        return result

    def _base_weights(self, columns: pd.Index) -> pd.Series:
        maximum = float(self.constraints.get("max_indicator_weight", 0.20))
        weights = pd.Series(
            {
                str(column): min(float(self.settings[str(column)]["weight"]), maximum)
                for column in columns
            },
            dtype=float,
        )
        # 영역 상한은 설정 실수로 한 영역이 모델을 지배하지 못하게 한다.
        domain_cap = float(self.constraints.get("max_domain_weight", 0.30))
        for domain in {str(self.settings[column]["domain"]) for column in weights.index}:
            members = [
                column for column in weights.index if self.settings[column]["domain"] == domain
            ]
            total = float(weights[members].sum())
            if total > domain_cap:
                weights[members] *= domain_cap / total
        return weights

    def _bounded_weights(self, raw: pd.Series) -> pd.Series:
        """개별·영역 상한 안에서 합계 1이 되도록 비례 배분한다."""

        indicator_cap = float(self.constraints.get("max_indicator_weight", 0.20))
        domain_cap = float(self.constraints.get("max_domain_weight", 0.30))
        result = pd.Series(0.0, index=raw.index, dtype=float)
        remaining = 1.0
        for _ in range(100):
            if remaining <= 1e-12:
                return result
            domain_totals = {
                domain: float(
                    result[
                        [key for key in result.index if self.settings[key]["domain"] == domain]
                    ].sum()
                )
                for domain in {str(self.settings[key]["domain"]) for key in result.index}
            }
            capacities = pd.Series(
                {
                    key: max(
                        0.0,
                        min(
                            indicator_cap - float(result[key]),
                            domain_cap - domain_totals[str(self.settings[key]["domain"])],
                        ),
                    )
                    for key in result.index
                },
                dtype=float,
            )
            active = (raw > 0) & (capacities > 1e-12)
            if not active.any():
                break
            active_raw = raw[active]
            scale_limits = [
                remaining / float(active_raw.sum()),
                *[float(capacities[key] / active_raw[key]) for key in active_raw.index],
            ]
            # 같은 영역의 여러 지표가 동시에 재분배될 때 영역 잔여용량을 각 지표가
            # 따로 쓰면 합계 상한을 넘는다. 영역별 활성 원가중치 합에 한 번만 제약한다.
            for domain, domain_total in domain_totals.items():
                domain_keys = [
                    key for key in active_raw.index if str(self.settings[key]["domain"]) == domain
                ]
                domain_raw = float(active_raw[domain_keys].sum())
                if domain_raw > 0:
                    scale_limits.append((domain_cap - domain_total) / domain_raw)
            scale = max(0.0, min(scale_limits))
            increment = active_raw * scale
            result.loc[active] += increment
            remaining -= float(increment.sum())
        # 제약 안에서 1을 만들 수 없으면 위반하는 숫자를 내지 않고 해당 주를 결측으로 둔다.
        return pd.Series(0.0, index=raw.index, dtype=float)

    def fit_filter(self, events: pd.DataFrame) -> FactorEstimate:
        ages = {key: int(value.get("max_age_weeks", 8)) for key, value in self.settings.items()}
        held = held_signal_matrix(events, ages)
        freshness = pd.DataFrame(0.0, index=events.index, columns=events.columns)
        for column in events.columns:
            age = ages.get(str(column), 8) + 1
            maximum_age = max(1, ages.get(str(column), 8))
            column_position = list(events.columns).index(column)
            for position, value in enumerate(events[column]):
                age = 0 if pd.notna(value) else age + 1
                if age <= maximum_age:
                    freshness.iloc[position, column_position] = float(np.exp(-age / maximum_age))
        base = self._base_weights(held.columns)
        contributions = pd.DataFrame(0.0, index=held.index, columns=held.columns)
        factor = pd.Series(np.nan, index=held.index, dtype=float, name="composite_factor")
        renormalized = 0
        effective_weights: dict[pd.Timestamp, dict[str, float]] = {}
        maturity_weights: dict[pd.Timestamp, dict[str, float]] = {}
        clipping_events: list[dict[str, Any]] = []
        for position in range(len(held)):
            week = pd.Timestamp(str(held.index[position]))
            row = held.iloc[position]
            available = row.notna()
            maturity = self._maturity_weights(events, week)
            weights = (base * freshness.iloc[position] * maturity).where(available, 0.0)
            total = float(weights.sum())
            if total <= 0:
                continue
            normalized = self._bounded_weights(weights)
            if float(normalized.sum()) <= 0:
                continue
            if abs(total - float(base.sum())) > 1e-12:
                renormalized += 1
            bounded = row.copy()
            if self.robust_clip is not None:
                bounded = bounded.clip(-self.robust_clip, self.robust_clip)
                changed = row.notna() & bounded.ne(row)
                for indicator in row.index[changed]:
                    clipping_events.append(
                        {
                            "date": week.date().isoformat(),
                            "indicator_id": str(indicator),
                            "preclip": float(row[indicator]),
                            "postclip": float(bounded[indicator]),
                            "limited_amount": float(abs(row[indicator]) - abs(bounded[indicator])),
                        }
                    )
            values = bounded.fillna(0.0).to_numpy(dtype=float) * normalized.to_numpy(dtype=float)
            contributions.iloc[position, :] = values
            factor.iloc[position] = float(np.sum(values))
            effective_weights[week] = {
                str(k): float(v) for k, v in normalized[normalized > 0].items()
            }
            maturity_weights[week] = {str(k): float(v) for k, v in maturity.items()}
        correlations = events.corr(min_periods=12)
        duplicate_threshold = float(self.constraints.get("duplicate_correlation", 0.85))
        correlation_values = correlations.to_numpy(dtype=float)
        duplicate_pairs = []
        for left_index, left in enumerate(correlations.columns):
            for right_index in range(left_index + 1, len(correlations.columns)):
                value = float(correlation_values[left_index, right_index])
                if np.isfinite(value) and abs(value) >= duplicate_threshold:
                    duplicate_pairs.append(
                        {
                            "left": str(left),
                            "right": str(correlations.columns[right_index]),
                            "correlation": value,
                        }
                    )
        return FactorEstimate(
            factor=factor,
            contributions=contributions,
            metadata={
                "model": "composite",
                "renormalized_weeks": renormalized,
                "effective_weights": effective_weights,
                "maturity_weights": maturity_weights,
                "robust_clip": self.robust_clip,
                "clipping_events": clipping_events,
                "clipped_observation_count": len(clipping_events),
                "total_limited_amount": float(
                    sum(float(event["limited_amount"]) for event in clipping_events)
                ),
                "duplicate_pairs": duplicate_pairs,
            },
        )
