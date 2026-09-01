"""국면-가치 프리미엄 검정의 계약.

지키려는 것은 결론이 아니라 **결론을 낼 자격**이다. 앞을 훔쳐보지 않는가, 금리 통제를
빼먹지 않는가, 에피소드 하나에 얹힌 수치를 전체 수치처럼 적지 않는가.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.phase_returns.labels import PHASES
from business_cycle.phase_value import conditional as C
from business_cycle.phase_value import control as CT
from business_cycle.phase_value import data as D
from business_cycle.phase_value import leaveout as LO
from business_cycle.phase_value import premium as P

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "phase_value"


def _weeks(count: int, start: str = "2000-01-07") -> list[str]:
    return pd.date_range(start, periods=count, freq="W-FRI").strftime("%Y-%m-%d").tolist()


# ── 검정 A ──────────────────────────────────────────────────────────────────


def test_the_hac_t_statistic_is_wider_than_the_naive_one_under_persistence() -> None:
    """자기상관이 있으면 HAC 표준오차가 커야 한다. 안 커지면 통제를 안 한 것이다."""

    rng = np.random.default_rng(3)
    shocks = rng.normal(0.0, 0.01, size=800)
    persistent = pd.Series(pd.Series(shocks).rolling(20).mean().dropna().to_numpy())
    naive = float(persistent.mean() / (persistent.std(ddof=1) / np.sqrt(len(persistent))))
    hac = P.hac_t_statistic(persistent, lags=20)
    assert hac is not None
    assert abs(hac) < abs(naive)


def test_a_decade_with_too_few_weeks_is_marked_thin_not_summarised() -> None:
    series = pd.Series([0.001] * 20, index=_weeks(20))
    rows = P.by_decade(series)
    assert rows and all(row.get("thin") for row in rows)
    assert all("annualised" not in row for row in rows)


def test_leave_one_year_out_names_the_direction_not_a_vague_worst() -> None:
    """'가장 해가 되는 해'는 어느 방향인지 흐리다. 부호로 적는다."""

    index = _weeks(210)
    values = [0.0] * 210
    for position, week in enumerate(index):
        if week.startswith("2001"):
            values[position] = 0.05
    result = P.leave_one_year_out(pd.Series(values, index=index))
    assert result["year_whose_removal_lowers_it_most"] == "2001"
    assert result["year_whose_removal_raises_it_most"] != "2001"


def test_the_verdict_requires_both_a_positive_sign_and_a_real_t_statistic() -> None:
    """부호만 양이면 프리미엄이 있다고 말하지 않는다."""

    rng = np.random.default_rng(5)
    index = _weeks(600)
    noisy = pd.Series(rng.normal(0.0002, 0.02, size=600), index=index)
    portfolios = pd.DataFrame({"Hi 30": noisy.to_numpy(), "Lo 30": noisy.to_numpy()}, index=index)
    result = P.run(noisy, {"window": index}, portfolios, pd.Series(0.0, index=index))
    assert result["value_premium_is_positive_in_the_label_window"] is False
    assert "양이라고 말할 수 없다" in result["statement"]


# ── 전방 수익: 앞을 훔쳐보지 않는가 ─────────────────────────────────────────


def test_the_forward_value_return_starts_the_week_after_the_call() -> None:
    index = _weeks(6)
    series = pd.Series([0.50, 0.01, 0.02, 0.03, 0.04, 0.05], index=index)
    forward = C.forward_value(series, 2)
    assert forward.iloc[0] == pytest.approx(1.01 * 1.02 - 1.0)
    assert bool(np.isnan(forward.iloc[-1]))


def test_horizons_are_long_enough_for_a_value_thesis() -> None:
    """4주·13주는 가치 주장에 짧다. 잘못된 가격이 한 달에 닫히지 않는다."""

    assert min(D.HORIZONS) >= 26


# ── 검정 B ──────────────────────────────────────────────────────────────────


def test_every_phase_row_carries_an_episode_count_next_to_its_week_count() -> None:
    """18주 한 덩어리는 독립 관측 18이 아니라 1이다."""

    index = _weeks(120)
    phase = pd.Series(["expansion"] * 60 + ["contraction"] * 20 + ["expansion"] * 40, index=index)
    forward = pd.Series(np.linspace(0.0, 0.1, 120), index=index)
    for row in C.by_phase(phase, forward):
        assert "episodes" in row
        assert row["episodes"] <= row["weeks"]
    counted = {row["phase"]: row["episodes"] for row in C.by_phase(phase, forward)}
    assert counted["expansion"] == 2
    assert counted["contraction"] == 1


def test_a_thin_phase_gets_no_mean_rather_than_a_meaningless_one() -> None:
    index = _weeks(60)
    phase = pd.Series(["expansion"] * 57 + ["recovery"] * 3, index=index)
    forward = pd.Series(0.01, index=index)
    rows = {row["phase"]: row for row in C.by_phase(phase, forward)}
    assert rows["recovery"]["mean_forward_value_return"] is None
    assert rows["expansion"]["mean_forward_value_return"] is not None


def test_the_shift_test_finds_a_planted_conditional_effect() -> None:
    index = _weeks(700)
    rng = np.random.default_rng(9)
    phase = pd.Series([PHASES[(i // 40) % 4] for i in range(700)], index=index)
    forward = pd.Series(rng.normal(0.0, 0.02, size=700), index=index)
    forward[phase.eq("contraction").to_numpy()] += 0.10
    result = C.shift_test(phase, forward)
    assert result["dispersion_p_value"] < 0.05
    cell = next(row for row in result["cells"] if row["phase"] == "contraction")
    assert cell["p_value"] < 0.05


def test_the_shift_test_does_not_invent_a_conditional_effect() -> None:
    index = _weeks(700)
    rng = np.random.default_rng(9)
    phase = pd.Series([PHASES[(i // 40) % 4] for i in range(700)], index=index)
    forward = pd.Series(rng.normal(0.0, 0.02, size=700), index=index)
    assert C.shift_test(phase, forward)["dispersion_p_value"] > 0.05


# ── 금리 통제 ───────────────────────────────────────────────────────────────


def test_a_phase_effect_that_is_really_a_rate_effect_does_not_survive_the_control() -> None:
    """국면 옷을 입은 금리 효과는 통제를 넣으면 사라져야 한다.

    국면과 스프레드를 같이 움직이게 만들고, 결과는 **스프레드만** 따라 움직이게 한다.
    """

    index = _weeks(600)
    spread = pd.Series(np.sin(np.arange(600) / 40.0), index=index)
    phase = pd.Series(
        ["contraction" if value < 0 else "expansion" for value in spread], index=index
    )
    forward = spread * 0.05
    rates = pd.DataFrame(
        {"term_spread": spread, "term_spread_change": spread.diff(13)}, index=index
    )
    result = CT.run(phase, forward, rates)
    assert result["usable"]
    assert result["models"]["spread"]["r_squared"] > 0.9
    assert result["phase_adds_something_beyond_the_term_spread"] is False


def test_the_control_reports_both_sets_of_coefficients() -> None:
    """통제 있음/없음을 둘 다 내놓지 않으면 축소 정도를 볼 수 없다."""

    index = _weeks(400)
    rng = np.random.default_rng(11)
    spread = pd.Series(rng.normal(1.0, 0.5, size=400), index=index)
    phase = pd.Series([PHASES[i % 4] for i in range(400)], index=index)
    forward = pd.Series(rng.normal(0.0, 0.05, size=400), index=index)
    rates = pd.DataFrame(
        {"term_spread": spread, "term_spread_change": spread.diff(13)}, index=index
    )
    result = CT.run(phase, forward, rates)
    without = result["phase_coefficients_without_the_rate_control"]
    with_control = result["phase_coefficients_with_the_rate_control"]
    assert set(name for name in without if name.startswith("phase[")) == set(with_control)
    shrinkage = CT.coefficient_shrinkage(result)
    assert shrinkage["usable"]
    assert len(shrinkage["rows"]) == len(PHASES) - 1


def test_the_base_phase_is_excluded_from_the_dummies() -> None:
    """기준 국면까지 더미로 넣으면 설계행렬이 특이해진다."""

    names = CT._names("phase")
    assert f"phase[{CT.BASE_PHASE}]" not in names
    assert len(names) == len(PHASES)  # 절편 + 국면 3개


# ── 에피소드 제외 ───────────────────────────────────────────────────────────


def test_removing_an_episode_that_leaves_too_few_observations_is_flagged() -> None:
    """범위만 적으면 '한 에피소드를 빼면 계산조차 안 된다'는 사실이 숨는다."""

    index = _weeks(200)
    phase = pd.Series(["expansion"] * 180 + ["recovery"] * 20, index=index)
    forward = pd.Series(0.01, index=index)
    result = LO.phase_means(phase, forward)
    assert result["recovery"]["episodes"] == 1
    assert result["recovery"]["removing_one_episode_makes_this_phase_uncomputable"] is True
    assert result["recovery"]["episodes_whose_removal_leaves_too_few_observations"] == 1


def test_the_macro_window_removal_also_drops_weeks_whose_forward_window_overlaps() -> None:
    """국면 블록 하나만 빼면 이웃 주의 전방창이 여전히 같은 사건을 덮는다."""

    index = pd.Index(_weeks(400, start="2016-01-08"))
    touching = LO._weeks_touching(index, "2020-01-01", "2021-12-31", horizon=104)
    # 사건 시작 104주 전부터 이미 전방창이 사건에 닿는다.
    assert "2018-01-05" in touching
    # 그보다 더 앞선 주는 닿지 않는다. 닿았다면 창 계산이 틀린 것이다.
    assert "2016-01-08" not in touching
    # 사건이 끝난 뒤의 주는 라벨 자체가 창 밖이라 빠진다.
    assert all(week <= "2021-12-31" for week in touching)
    assert len(touching) < len(index)


def test_a_macro_window_removal_reports_which_phases_are_left_empty() -> None:
    index = pd.Index(_weeks(400, start="2016-01-08"))
    phase = pd.Series(
        ["recovery" if "2020-06-01" <= week <= "2020-12-31" else "expansion" for week in index],
        index=index,
    )
    forward = pd.Series(0.01, index=index)
    rates = pd.DataFrame({"term_spread": 1.0, "term_spread_change": 0.0}, index=index)
    rows = LO.leave_one_macro_window_out(phase, forward, rates, CT.run, horizon=26)
    covid = next(row for row in rows if row["window_removed"].startswith("covid"))
    assert "recovery" in covid["phases_left_with_no_weeks"]


# ── 산출물 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_layer_c_is_marked_blocked_with_the_data_that_would_unblock_it() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    layer = payload["layer_c"]
    assert layer["status"] == "blocked"
    assert layer["not_attempted_here"] is True
    assert layer["what_would_unblock_it"]
    report = (OUTPUT / "phase_value_report.md").read_text(encoding="utf-8")
    assert "CRSP" in report
    assert "Compustat" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_every_test_b_block_reports_the_rate_control_both_ways() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    for block in payload["test_b"]:
        control = block["rate_control"]
        assert control["usable"]
        assert "phase_coefficients_without_the_rate_control" in control
        assert "phase_coefficients_with_the_rate_control" in control
        assert set(control["models"]) == {"spread", "phase", "both"}


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_every_test_b_block_carries_a_leave_one_episode_out_range() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    for block in payload["test_b"]:
        assert block["leave_one_episode_out_phase_means"]
        assert block["leave_one_macro_window_out"]
        for phase in PHASES:
            assert phase in block["leave_one_episode_out_phase_means"]


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_a_significant_result_is_reported_with_its_covid_removal_outcome() -> None:
    """유의한 칸이 있으면 코로나 제외 결과가 같은 화면에 있어야 한다."""

    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    verdict = payload["verdict"]
    for entry in verdict["test_b_cells_where_phase_adds_beyond_the_term_spread"]:
        block = next(
            item
            for item in payload["test_b"]
            if item["labelling"] == entry["labelling"]
            and item["horizon_weeks"] == entry["horizon_weeks"]
        )
        covid = next(
            row
            for row in block["leave_one_macro_window_out"]
            if row["window_removed"].startswith("covid")
        )
        assert "still_adds_something" in covid
    assert verdict["phase_model_adds_something_beyond_the_term_spread"] == bool(
        verdict["test_b_cells_that_survive_removing_the_covid_window"]
    )


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_states_the_proxy_asymmetry() -> None:
    """음의 결과가 반증이 아니라는 것과, 양의 결과였다면 강한 지지였을 것을 둘 다 적는다."""

    report = (OUTPUT / "phase_value_report.md").read_text(encoding="utf-8")
    assert "반증하지 않는다" in report
    assert "강한 지지" in report
    assert "장부가/시가" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_carries_no_investment_instruction() -> None:
    report = (OUTPUT / "phase_value_report.md").read_text(encoding="utf-8")
    disclaimer = "투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다"
    assert disclaimer in report
    stripped = report.replace(disclaimer, "")
    for token in ("매수", "매도", "비중 확대", "추천 종목"):
        assert token not in stripped
