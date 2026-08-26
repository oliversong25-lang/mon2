"""변동성 대조의 계약.

가장 중요한 계약은 **대조가 진짜 대조인가**다. 실현분산이 미래를 보거나, 창을 유리하게
고르거나, 함수 형태를 하나만 받으면 국면이 이기는 것은 당연하고 검정은 연출이 된다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from business_cycle.variance_control import control as C
from business_cycle.variance_control import leaveout as LO
from business_cycle.variance_control import prespec

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "variance_control"


def _index(weeks: int) -> pd.Index:
    return pd.Index(
        [str(day.date()) for day in pd.date_range("2000-01-07", periods=weeks, freq="W-FRI")],
        name="week",
    )


def _phase(weeks: int) -> pd.Series:
    names = ["expansion", "slowdown", "contraction", "recovery"]
    return pd.Series(
        [names[(position // 40) % 4] for position in range(weeks)], index=_index(weeks)
    )


def _market(weeks: int, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.001, 0.02, weeks), index=_index(weeks))


def _spread(weeks: int) -> pd.Series:
    return pd.Series(np.linspace(-0.5, 3.0, weeks), index=_index(weeks))


# ── 대조가 진짜 대조인가 ────────────────────────────────────────────────────


def test_the_control_never_sees_the_future() -> None:
    """t주 실현분산이 t+1주 수익을 담으면 대조가 이기는 것이 당연해진다."""

    weekly = pd.Series([0.0] * 10 + [0.5] + [0.0] * 10, index=_index(21))
    variance = C.realised_variance(weekly, 4)
    # 0.5는 위치 10에 있다. 위치 9까지는 그것을 몰라야 한다.
    assert float(variance.iloc[9]) == 0.0
    assert float(variance.iloc[10]) > 0.0


def test_the_control_includes_the_current_week() -> None:
    """가격은 그 주 금요일에 이미 알려져 있다. 지연을 주면 대조를 인위적으로 깎는 것이다."""

    weekly = pd.Series([0.0] * 5 + [0.4] + [0.0] * 5, index=_index(11))
    variance = C.realised_variance(weekly, 2)
    assert float(variance.iloc[5]) > 0.0


def test_the_control_carries_both_level_and_root() -> None:
    weekly, spread = _market(200), _spread(200)
    frame = C.build_control(weekly, spread, 13)
    assert "realised_variance_13w" in frame.columns
    assert "realised_volatility_13w" in frame.columns
    assert "term_spread" in frame.columns


def test_the_term_spread_can_be_dropped_for_the_volatility_only_view() -> None:
    frame = C.build_control(_market(200), _spread(200), 13, keep_spread=False)
    assert "term_spread" not in frame.columns


def test_the_strongest_lookback_wins_even_when_it_is_not_the_first() -> None:
    """규칙이 고르지 내가 고른다면 그것은 사전 명세가 아니다."""

    table = [
        {"lookback_weeks": 4, "control_only_r_squared": 0.01},
        {"lookback_weeks": 13, "control_only_r_squared": 0.02},
        {"lookback_weeks": 26, "control_only_r_squared": 0.30},
    ]
    assert prespec.lookback_choice(table)["chosen"] == 26


# ── 회귀가 회귀인가 ─────────────────────────────────────────────────────────


def test_adding_phase_can_only_raise_r_squared() -> None:
    weeks = 400
    weekly, spread = _market(weeks, seed=3), _spread(weeks)
    target = pd.Series(_market(weeks, seed=4).to_numpy() ** 2, index=_index(weeks))
    read = C.compare(_phase(weeks), C.build_control(weekly, spread, 13), target, "t", stride=16)
    assert read["incremental_r_squared_of_phase"] >= -1e-9
    assert read["incremental_r_squared_of_control_over_phase"] >= -1e-9
    assert read["both_r_squared"] >= read["control_only_r_squared"]


def test_both_increments_are_reported_so_absorption_is_visible() -> None:
    """증분만 적으면 '국면이 대조를 흡수했다'를 확인할 수 없다."""

    weeks = 300
    read = C.compare(
        _phase(weeks),
        C.build_control(_market(weeks, seed=5), _spread(weeks), 13),
        pd.Series(_market(weeks, seed=6).to_numpy() ** 2, index=_index(weeks)),
        "t",
        stride=16,
    )
    assert "incremental_r_squared_of_phase" in read
    assert "incremental_r_squared_of_control_over_phase" in read
    assert "phase_absorbs_the_control" in read


def test_a_label_unrelated_to_the_target_does_not_beat_its_null() -> None:
    weeks = 500
    read = C.compare(
        _phase(weeks),
        C.build_control(_market(weeks, seed=8), _spread(weeks), 13),
        pd.Series(_market(weeks, seed=9).to_numpy() ** 2, index=_index(weeks)),
        "t",
        stride=16,
    )
    assert float(read["null_p"]) > 0.05


def test_a_short_sample_is_refused_rather_than_fitted() -> None:
    weeks = 60
    read = C.compare(
        _phase(weeks),
        C.build_control(_market(weeks), _spread(weeks), 13),
        _market(weeks),
        "short",
    )
    assert read["usable"] is False


# ── 에피소드 제외 ───────────────────────────────────────────────────────────


def test_the_stronger_exclusion_removes_the_forward_window_too() -> None:
    weeks = 400
    weekly, spread = _market(weeks, seed=10), _spread(weeks)
    read = LO.run(
        _phase(weeks),
        C.build_control(weekly, spread, 13),
        pd.Series(_market(weeks, seed=11).to_numpy() ** 2, index=_index(weeks)),
        13,
        stride=32,
    )
    for row in read["rows"]:
        assert row["event_including_weeks_removed"] >= row["block_only_weeks_removed"]
    assert read["both_strengths_must_hold"] is True


def test_both_summaries_are_produced() -> None:
    weeks = 300
    read = LO.run(
        _phase(weeks),
        C.build_control(_market(weeks, seed=12), _spread(weeks), 13),
        pd.Series(_market(weeks, seed=13).to_numpy() ** 2, index=_index(weeks)),
        13,
        stride=32,
    )
    assert read["block_only_summary"]["lowest"] is not None
    assert read["event_including_summary"]["lowest"] is not None
    assert sum(read["episodes_by_phase"].values()) == read["episodes"]


# ── 산출물 ──────────────────────────────────────────────────────────────────


def _summary() -> dict[str, object] | None:
    path = OUTPUT / "validation_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_report_carries_the_prespecified_rule_verbatim() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["prespecified_rule"] == prespec.rule()  # type: ignore[index]


def test_the_chosen_lookback_is_the_strongest_in_the_table() -> None:
    """보고서가 고른 창이 표에서 가장 강하지 않으면 대조를 약화시킨 것이다."""

    summary = _summary()
    if summary is None:
        return
    table = summary["lookback_table"]  # type: ignore[index]
    strongest = max(table, key=lambda row: float(row["control_only_r_squared"]))
    assert summary["chosen_lookback_weeks"] == strongest["lookback_weeks"]  # type: ignore[index]


def test_display_wording_exists_only_when_the_gate_passed() -> None:
    """실패한 결과 위에 문구를 얹으면 그 문구가 근거 없는 것이 된다."""

    summary = _summary()
    if summary is None:
        return
    assert summary["display_wording"]["drafted"] == summary["decision_gate"]["passes"]  # type: ignore[index]


def test_the_drafted_wording_stays_inside_the_constraint() -> None:
    """통과해도 만들 수 있는 것은 역사적 분포에 대한 사실 진술뿐이다."""

    summary = _summary()
    if summary is None or not summary["display_wording"]["drafted"]:  # type: ignore[index]
        return
    for row in summary["display_wording"]["lines"]:  # type: ignore[index]
        text = row["wording"]
        assert "역사적으로" in text and "였습니다" in text
        for banned in ("줄이", "늘리", "비중", "매수", "매도", "보유", "확률", "전망", "예상"):
            assert banned not in text, text
