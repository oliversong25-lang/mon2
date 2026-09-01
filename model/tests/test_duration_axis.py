"""듀레이션 축의 계약.

첫 계약은 **두 축을 같은 자로 재는가**다. 빈도나 통 수가 다르면 천장 비교가 축의 차이가
아니라 자의 차이를 잰다.

두 번째는 **듀레이션 순서가 뒤집히지 않는가**다. 왼쪽이 긴 쪽이라는 약속이 깨지면
장단 스프레드의 부호가 조용히 반대가 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from business_cycle.duration_axis import axes as AX
from business_cycle.duration_axis import ceiling as CE
from business_cycle.duration_axis import control as C
from business_cycle.duration_axis import prespec

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "famafrench"
OUTPUT = ROOT / "outputs" / "duration_axis"


def _months(count: int) -> pd.Index:
    stamps = pd.date_range("2000-01-31", periods=count, freq="ME")
    return pd.Index([f"{s.year:04d}-{s.month:02d}" for s in stamps], name="month")


def _axis(count: int, buckets: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.normal(0.008, 0.05, (count, buckets)),
        index=_months(count),
        columns=[f"D{i:02d}" for i in range(buckets)],
    )


def _phase(count: int) -> pd.Series:
    names = ["expansion", "slowdown", "contraction", "recovery"]
    return pd.Series([names[(i // 10) % 4] for i in range(count)], index=_months(count))


# ── 같은 자로 재는가 ────────────────────────────────────────────────────────


def test_the_monthly_phase_takes_the_last_week_of_each_month() -> None:
    """달 안의 라벨을 평균 내면 존재하지 않는 상태가 만들어진다."""

    weekly = pd.Series(
        ["expansion", "expansion", "slowdown"],
        index=pd.Index(["2020-03-06", "2020-03-13", "2020-03-27"], name="week"),
    )
    monthly = AX.monthly_phase(weekly)
    assert list(monthly.index) == ["2020-03"]
    assert monthly.iloc[0] == "slowdown"


def test_the_industry_axis_comes_out_monthly() -> None:
    if not CACHE.exists():
        return
    axis, columns = AX.industry_axis(str(CACHE), [])
    assert len(columns) == 12
    assert all(len(str(month)) == 7 for month in axis.index[:5])


def test_the_duration_axis_puts_the_zero_bucket_first() -> None:
    """왼쪽이 긴 쪽이라는 약속이 깨지면 장단 스프레드 부호가 조용히 뒤집힌다."""

    if not CACHE.exists():
        return
    _, columns = AX.duration_axis(prespec.PRIMARY_PROXY, str(CACHE), "deciles")
    assert columns[0].endswith("zero")
    assert len(columns) == 11


def test_the_quintile_version_has_fewer_buckets() -> None:
    if not CACHE.exists():
        return
    _, deciles = AX.duration_axis(prespec.PRIMARY_PROXY, str(CACHE), "deciles")
    _, quintiles = AX.duration_axis(prespec.PRIMARY_PROXY, str(CACHE), "quintiles")
    assert len(quintiles) < len(deciles)


def test_align_drops_months_that_are_missing_anywhere() -> None:
    axis = _axis(20, 5)
    axis.iloc[3, 0] = float("nan")
    market = pd.Series(np.zeros(20), index=_months(20))
    read_axis, read_market, read_phase = AX.align(axis, market, _phase(20))
    assert len(read_axis) == 19
    assert len(read_market) == 19 and len(read_phase) == 19


# ── 천장 ────────────────────────────────────────────────────────────────────


def test_the_oracle_beats_the_phase_ceiling_by_construction() -> None:
    axis, market, phase = (
        _axis(200, 8, seed=2),
        pd.Series(np.zeros(200), index=_months(200)),
        _phase(200),
    )
    read = CE.measure(phase, axis, market)
    assert (
        read["oracle_ceiling"]["annualised_relative_return"]
        > read["ranking_ceiling"]["annualised_relative_return"]
    )
    assert 0.0 < read["phase_share_of_the_oracle"] < 1.0


def test_the_ceiling_annualises_monthly_not_weekly() -> None:
    """주간 52로 연율화하면 월간 자료에서 천장이 네 배 넘게 부풀려진다."""

    assert CE.MONTHS_PER_YEAR == 12.0
    series = np.full(120, 0.01)
    assert abs(CE._annualise(series, 12.0) - (1.01**12 - 1.0)) < 1e-9


def test_the_comparison_reports_both_shares() -> None:
    axis, market, phase = (
        _axis(150, 8, seed=3),
        pd.Series(np.zeros(150), index=_months(150)),
        _phase(150),
    )
    duration = CE.measure(phase, axis, market)
    industry = CE.measure(phase, _axis(150, 12, seed=4), market)
    read = CE.compare(duration, industry)
    assert read["share_organised_duration"] is not None
    assert read["share_organised_industry"] is not None
    assert read["ceiling_ratio"] is not None


# ── 장단 스프레드와 대조 ────────────────────────────────────────────────────


def test_long_minus_short_uses_the_first_and_last_bucket() -> None:
    axis = pd.DataFrame(
        {"D00_zero": [0.10, 0.0, 0.0], "D05_mid": [0.0, 0.0, 0.0], "D10_Hi": [0.02, 0.0, 0.0]},
        index=_months(3),
    )
    spread = C.long_minus_short(axis, 1)
    # 1개월 전방이므로 위치 0의 값은 위치 1의 스프레드다 — 여기서는 0.
    assert abs(float(spread.iloc[0])) < 1e-12


def test_the_control_never_sees_the_future() -> None:
    monthly = pd.Series([0.0] * 6 + [0.4] + [0.0] * 6, index=_months(13))
    variance = C.realised_variance(monthly, 3)
    assert float(variance.iloc[5]) == 0.0
    assert float(variance.iloc[6]) > 0.0


def test_the_control_carries_both_forms_and_the_spread() -> None:
    market = pd.Series(np.zeros(60), index=_months(60))
    frame = C.build_control(market, pd.Series(np.ones(60), index=_months(60)), 13)
    assert "realised_variance_13m" in frame.columns
    assert "realised_volatility_13m" in frame.columns
    assert "term_spread" in frame.columns


def test_the_strongest_lookback_is_chosen() -> None:
    table = [
        {"lookback_weeks": 4, "control_only_r_squared": 0.01},
        {"lookback_weeks": 13, "control_only_r_squared": 0.09},
        {"lookback_weeks": 26, "control_only_r_squared": 0.03},
    ]
    read = C.choose_lookback(table)
    assert read["chosen"] == 13
    assert read["weakest"] == 4


# ── 산출물 ──────────────────────────────────────────────────────────────────


def _summary() -> dict[str, object] | None:
    path = OUTPUT / "validation_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_report_carries_the_prespecified_rule_verbatim() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["prespecified_rule"] == prespec.rule()  # type: ignore[index]


def test_both_axes_are_measured_over_the_same_months() -> None:
    """기간이 다르면 천장 비교가 축이 아니라 표본을 잰다."""

    summary = _summary()
    if summary is None:
        return
    industry = summary["industry_axis"]["periods"]  # type: ignore[index]
    duration = summary["duration_axes"][prespec.PRIMARY_PROXY]["primary"]["periods"]  # type: ignore[index]
    assert abs(int(industry) - int(duration)) <= 1


def test_all_four_proxies_are_reported() -> None:
    summary = _summary()
    if summary is None:
        return
    assert set(summary["duration_axes"]) == set(prespec.PROXIES)  # type: ignore[arg-type]


def test_the_multiplicity_counts_eight() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["multiplicity"]["family_size"] == 8  # type: ignore[index]


def test_a_blocked_ceiling_marks_everything_below_as_record_only() -> None:
    """관문이 막혔는데 아래 숫자에 결론이 달리면 사전 명세가 지켜지지 않은 것이다."""

    summary = _summary()
    if summary is None:
        return
    if not summary["ceiling_gate"]["passes"]:  # type: ignore[index]
        assert summary["record_only_below_the_ceiling"] is True  # type: ignore[index]
        assert summary["leave_one_episode_out"]["record_only"] is True  # type: ignore[index]


def test_the_report_states_the_unfavourable_prior_before_the_conclusion() -> None:
    path = OUTPUT / "duration_axis_report.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prior = text.index("사전 확률은 불리하다")
    conclusion = text.index("## 결론")
    assert prior < conclusion
