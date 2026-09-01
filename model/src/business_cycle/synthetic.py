"""네트워크 없이 전체 파이프라인을 검증하는 재현 가능한 합성 데이터."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd


def generate_synthetic_observations(
    start: str = "1985-01-01", end: str = "2026-08-14", seed: int = 42
) -> pd.DataFrame:
    """장기 추세와 반복 순환을 가진 월간·주간 핵심지표를 생성한다."""

    rng = np.random.default_rng(seed)
    fetched_at = datetime.now(UTC).isoformat()
    rows: list[dict[str, object]] = []
    monthly = pd.date_range(start, end, freq="ME")
    month_t = np.arange(len(monthly), dtype=float)
    cycle = 0.045 * np.sin(2 * np.pi * month_t / 84.0) + 0.018 * np.sin(2 * np.pi * month_t / 36.0)
    monthly_specs = {
        "PAYEMS": (80000.0, 0.0020, 7),
        "W875RX1": (6000.0, 0.0018, 28),
        "INDPRO": (70.0, 0.0010, 16),
        "CMRMTSPL": (700000.0, 0.0017, 45),
        "RRSFS": (150000.0, 0.0022, 16),
    }
    for indicator_id, (base, trend, lag) in monthly_specs.items():
        noise = rng.normal(0.0, 0.004, len(monthly))
        values = base * np.exp(trend * month_t + cycle + noise)
        for period, value in zip(monthly, values, strict=True):
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "observation_period": period,
                    "value": value,
                    "release_date": period + pd.Timedelta(days=lag),
                    "vintage_date": pd.NaT,
                    "fetched_at": fetched_at,
                    "source": "synthetic",
                    "revision_status": "synthetic_final",
                    "freshness_score": 1.0,
                }
            )
    weekly = pd.date_range(start, end, freq="W-SAT")
    week_t = np.arange(len(weekly), dtype=float)
    weekly_cycle = np.interp(
        weekly.view("i8"), monthly.view("i8"), cycle, left=cycle[0], right=cycle[-1]
    )
    for indicator_id, base, lag in (("ICSA", 260000.0, 5), ("CCSA", 2100000.0, 12)):
        noise = rng.normal(0.0, 0.025, len(weekly))
        # 청구건수는 경기와 역방향이다. 전처리의 direction=-1을 검증할 수 있다.
        values = base * np.exp(-2.4 * weekly_cycle + noise + 0.00002 * week_t)
        for period, value in zip(weekly, values, strict=True):
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "observation_period": period,
                    "value": value,
                    "release_date": period + pd.Timedelta(days=lag),
                    "vintage_date": pd.NaT,
                    "fetched_at": fetched_at,
                    "source": "synthetic",
                    "revision_status": "synthetic_final",
                    "freshness_score": 1.0,
                }
            )
    return pd.DataFrame(rows).sort_values(["release_date", "indicator_id"]).reset_index(drop=True)
