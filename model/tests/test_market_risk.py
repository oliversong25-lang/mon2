"""시계열 검정의 계약.

첫 계약은 **동시점과 전방을 섞지 않는가**다. 동시점은 국면을 서술하고 전방은 결정이
쓸 수 있었던 것이다. 섞으면 "그 국면이던 주가 나빴다"를 "그 국면이라 부른 날부터
나빠졌다"로 읽게 된다.

두 번째는 **평균만으로 판정하지 않는가**다. 노출 결정은 꼬리에 대한 결정이므로 분포가
갈려야 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from business_cycle.market_risk import leaveout as LO
from business_cycle.market_risk import market as M
from business_cycle.market_risk import prespec
from business_cycle.market_risk import spread as SP

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "market_risk"


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
    return pd.Series(rng.normal(0.001, 0.02, weeks), index=_index(weeks), name="market_excess")


# ── 동시점과 전방을 섞지 않는가 ─────────────────────────────────────────────


def test_the_forward_window_starts_after_the_call_week() -> None:
    """t주 판정이 t주 수익을 보고 내려지면 그것은 결정이 쓸 수 있었던 것이 아니다."""

    weekly = pd.Series([0.0] * 5 + [0.5] + [0.0] * 5, index=_index(11))
    ahead = M.forward_sum(weekly, 2)
    # 0.5는 6번째 주(위치 5)에 있다. 위치 3·4가 그것을 받고, 위치 5는 받지 않는다.
    assert ahead.iloc[4] > 0.4
    assert abs(float(ahead.iloc[5])) < 1e-12


def test_contemporaneous_and_forward_are_separate_outputs() -> None:
    phase, weekly = _phase(300), _market(300)
    now = M.contemporaneous(phase, weekly)
    ahead = M.forward(phase, weekly, 13)
    assert {row["phase"] for row in now} == {row["phase"] for row in ahead}
    assert "worst_drawdown_starting_in_phase" in now[0]
    assert "horizon_weeks" not in now[0]
    assert "mean_forward" in ahead[0]


def test_drawdown_beginning_within_follows_past_the_phase_boundary() -> None:
    """구간을 잘라 재면 짧은 국면의 위험이 실제보다 작아 보인다."""

    weekly = pd.Series([0.0] * 10 + [-0.2] * 5 + [0.0] * 10, index=_index(25))
    phase = pd.Series(["expansion"] * 9 + ["slowdown"] * 2 + ["contraction"] * 14, index=_index(25))
    inside = M._max_drawdown(weekly.to_numpy(dtype=float)[9:11])
    beyond = M.drawdown_beginning_within(phase, weekly, "slowdown")
    assert beyond["worst_drawdown_starting_in_phase"] < inside


# ── 평균만으로 판정하지 않는가 ──────────────────────────────────────────────


def test_the_downside_volatility_uses_only_losses() -> None:
    """전체 변동성이 같아도 손실 쪽이 다르면 다른 값이 나와야 한다."""

    calm = np.array([-0.01] * 50 + [0.05] * 50)
    rough = np.array([-0.05] * 50 + [0.01] * 50)
    assert M._downside_volatility(rough, 1) > M._downside_volatility(calm, 1)


def test_two_identical_distributions_overlap_almost_completely() -> None:
    weekly = _market(400, seed=5)
    phase = pd.Series(["expansion"] * 200 + ["slowdown"] * 200, index=_index(400))
    read = M.overlap(phase, weekly, 13, "expansion", "slowdown")
    assert read["overlap_coefficient"] > 0.5


def test_the_ratio_names_which_phases_it_compared() -> None:
    """비만 적으면 어느 국면과 어느 국면인지 나중에 알 수 없다."""

    rows = M.forward(_phase(300), _market(300, seed=2), 13)
    read = M.downside_ratio(rows)
    assert read["riskiest"] in {"recovery", "expansion", "slowdown", "contraction"}
    assert read["safest"] != read["riskiest"]
    assert read["riskiest_episodes"] > 0


def test_episode_counts_travel_with_every_forward_row() -> None:
    """전방 창이 겹치므로 관측 수는 독립 표본 크기가 아니다."""

    rows = M.forward(_phase(400), _market(400, seed=3), 26)
    for row in rows:
        assert row["episodes"] >= 1
        assert row["observations"] > row["episodes"]


# ── 기간 스프레드 ───────────────────────────────────────────────────────────


def test_adding_a_variable_can_only_raise_r_squared() -> None:
    """증분이 음수로 나오면 회귀가 잘못된 것이다."""

    weeks = 400
    phase, weekly = _phase(weeks), _market(weeks, seed=7)
    spread = pd.Series(np.linspace(-1.0, 3.0, weeks), index=_index(weeks))
    read = SP.compare(phase, spread, M.forward_sum(weekly, 13), "test", stride=16)
    assert read["incremental_r_squared_of_phase"] >= -1e-9
    assert read["both_r_squared"] >= read["spread_only_r_squared"]
    assert read["both_r_squared"] >= read["phase_only_r_squared"]


def test_a_label_unrelated_to_returns_does_not_beat_its_null() -> None:
    """국면과 수익이 무관하면 증분이 이동 분포 안에 있어야 한다."""

    weeks = 500
    weekly = _market(weeks, seed=11)
    spread = pd.Series(np.linspace(0.0, 2.0, weeks), index=_index(weeks))
    read = SP.compare(_phase(weeks), spread, M.forward_sum(weekly, 13), "test", stride=16)
    assert float(read["null_p"]) > 0.05


def test_the_reading_reports_variance_separately_from_returns() -> None:
    """노출 결정이 묻는 것은 흔들림의 크기다. 수익만 보고 지워 버리면 안 된다."""

    read = SP.read(
        {"usable": True, "null_p": 0.30},
        {"usable": True, "null_p": 0.001},
    )
    assert read["phase_adds_beyond_the_spread_on_returns"] is False
    assert read["phase_adds_beyond_the_spread_on_variance"] is True
    assert "흔들림의 크기" in read["reading"]


def test_a_short_sample_is_refused_rather_than_fitted() -> None:
    weeks = 50
    read = SP.compare(
        _phase(weeks),
        pd.Series(np.zeros(weeks), index=_index(weeks)),
        _market(weeks),
        "short",
    )
    assert read["usable"] is False


# ── 에피소드 제외 ───────────────────────────────────────────────────────────


def test_the_stronger_exclusion_removes_the_forward_window_too() -> None:
    read = LO.run(_phase(400), _market(400, seed=13), 13)
    for row in read["rows"]:
        assert row["event_including_weeks_removed"] >= row["block_only_weeks_removed"]
    assert read["both_strengths_must_hold"] is True


def test_both_summaries_are_produced_so_neither_can_be_chosen_later() -> None:
    read = LO.run(_phase(300), _market(300, seed=17), 13)
    assert read["block_only_summary"]["lowest"] is not None
    assert read["event_including_summary"]["lowest"] is not None


# ── 산출물 ──────────────────────────────────────────────────────────────────


def _summary() -> dict[str, object] | None:
    path = OUTPUT / "validation_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_report_carries_the_prespecified_rule_verbatim() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["prespecified_rule"] == prespec.rule()  # type: ignore[index]


def test_the_term_spread_decides_the_verdict() -> None:
    """스프레드를 넘지 못했는데 통과로 적혀 있으면 사전 명세가 지켜지지 않은 것이다."""

    summary = _summary()
    if summary is None:
        return
    verdict = summary["verdict"]  # type: ignore[index]
    if not verdict["beats_the_term_spread"]:
        assert verdict["usable_for_exposure"] is False


def test_the_2020_check_is_applied_to_the_spread_control_too() -> None:
    """트랙 17의 표제가 그 한 해에서 나왔다. 결정적인 자리에는 반드시 걸어야 한다."""

    summary = _summary()
    if summary is None:
        return
    without = summary["term_spread_without_episode"]  # type: ignore[index]
    assert "ex_covid" in without and "ex_gfc" in without


def test_the_report_says_the_outcome_is_settled_not_a_fallback() -> None:
    path = OUTPUT / "market_risk_report.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    summary = _summary()
    if summary is not None and not summary["verdict"]["usable_for_exposure"]:  # type: ignore[index]
        assert "물러선 것이 아니라 정해진 것이다" in text
