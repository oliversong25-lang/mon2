"""국면-산업 수익률 검증의 계약.

여기서 지키려는 것은 결론이 아니라 **결론을 낼 자격**이다. 앞을 훔쳐보지 않는가,
비어 있는 칸을 유의하다고 하지 않는가, 심어 둔 신호를 찾아내는가, 없는 신호를 만들지
않는가.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.phase_returns import distribution as D
from business_cycle.phase_returns import forward as F
from business_cycle.phase_returns import french, labels, latency, rotation, samples
from business_cycle.phase_returns import significance as SIG

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "phase_returns"


# ── 전방 수익률: 앞을 훔쳐보지 않는가 ────────────────────────────────────────


def test_forward_return_starts_the_week_after_the_call() -> None:
    """t의 전방 수익률은 t+1..t+h다. t 자신의 수익은 들어가지 않는다."""

    weeks = [f"2020-01-{day:02d}" for day in (3, 10, 17, 24, 31)]
    series = pd.Series([0.10, 0.01, 0.02, 0.03, 0.04], index=weeks)
    forward = F._compound_forward(series, 2)

    # 첫 주의 0.10은 어디에도 들어가면 안 된다.
    assert forward.iloc[0] == pytest.approx(1.01 * 1.02 - 1.0)
    assert forward.iloc[1] == pytest.approx(1.02 * 1.03 - 1.0)
    # 창이 모자란 뒤쪽 두 주는 결측.
    assert bool(np.isnan(forward.iloc[3]))
    assert bool(np.isnan(forward.iloc[4]))


def test_relative_return_is_industry_minus_market() -> None:
    weeks = ["2020-01-03", "2020-01-10", "2020-01-17"]
    weekly = pd.DataFrame(
        {name: [0.0, 0.02, 0.0] for name in french.INDUSTRIES}
        | {"MKT": [0.0, 0.01, 0.0]},
        index=weeks,
    )
    relative = F.forward_relative(weekly, 1)
    assert relative.loc[weeks[0], "NoDur"] == pytest.approx(0.02 - 0.01)


def test_weekly_aggregation_compounds_inside_the_week_only() -> None:
    """주 F는 (F-7, F] 구간이다. 경계 하루가 옆 주로 새면 안 된다."""

    daily = pd.DataFrame(
        {"MKT": [0.01, 0.02, 0.03]},
        index=pd.to_datetime(["2020-01-03", "2020-01-06", "2020-01-10"]),
    )
    weekly = french.to_weekly(daily, ["2020-01-03", "2020-01-10"])
    assert weekly.loc["2020-01-03", "MKT"] == pytest.approx(0.01)
    assert weekly.loc["2020-01-10", "MKT"] == pytest.approx(1.02 * 1.03 - 1.0)


def test_a_week_with_no_trading_days_is_missing_not_zero() -> None:
    daily = pd.DataFrame({"MKT": [0.01]}, index=pd.to_datetime(["2020-01-03"]))
    weekly = french.to_weekly(daily, ["2020-01-03", "2020-01-10"])
    assert bool(np.isnan(weekly.loc["2020-01-10", "MKT"]))


# ── Fama-French 원자료 해석 ──────────────────────────────────────────────────


def test_only_the_value_weighted_section_is_read() -> None:
    industries, factors = french.load_daily(str(ROOT / french.CACHE_DIR))
    assert list(industries.columns) == list(french.INDUSTRIES)
    # 동일가중 절이 섞였으면 같은 날짜가 두 번 나온다.
    assert industries.index.is_unique
    assert factors.index.is_unique
    # 퍼센트가 아니라 소수여야 한다. 일간 수익률이 1을 넘을 수 없다.
    assert float(industries.abs().max().max()) < 1.0
    # 요인 파일의 첫 관측일이 머리글로 먹히지 않았는지.
    assert len(factors) == len(industries)


# ── 순환 이동 검정: 신호를 찾는가, 없는 신호를 만들지 않는가 ──────────────────


def _planted(weeks: int = 600, effect: float = 0.05) -> tuple[pd.Series, pd.DataFrame]:
    """국면이 산업 수익률을 실제로 가르는 인공 자료."""

    index = [f"w{i:04d}" for i in range(weeks)]
    rng = np.random.default_rng(11)
    phase = pd.Series(
        [labels.PHASES[(i // 30) % 4] for i in range(weeks)], index=index, name="phase"
    )
    frame = pd.DataFrame(
        rng.normal(0.0, 0.01, size=(weeks, len(french.INDUSTRIES))),
        index=index,
        columns=list(french.INDUSTRIES),
    )
    frame.loc[phase.eq("contraction"), "NoDur"] += effect
    return phase, frame


def test_the_shift_test_finds_a_planted_signal() -> None:
    phase, frame = _planted()
    result = SIG.shift_test(phase, frame, minimum_shift=40)
    assert result["taxonomy_dispersion_p_value"] < 0.05
    cell = next(
        row
        for row in result["cells"]
        if row["phase"] == "contraction" and row["industry"] == "NoDur"
    )
    assert cell["p_value"] < 0.05


def test_the_shift_test_does_not_invent_a_signal() -> None:
    phase, frame = _planted(effect=0.0)
    result = SIG.shift_test(phase, frame, minimum_shift=40)
    assert result["taxonomy_dispersion_p_value"] > 0.05
    correction = SIG.correct(result["cells"])
    assert correction["survives_bh"] == []


def test_a_phase_with_no_observations_is_not_the_most_significant_cell() -> None:
    """비어 있는 칸은 검정 대상이 아니다.

    NaN 비교는 항상 거짓이라, 그냥 두면 이동 통계량이 관측을 한 번도 넘지 못해
    **p가 가장 작아진다.** 실제로 그 버그가 났었다.
    """

    phase, frame = _planted(effect=0.0)
    phase = phase.replace("contraction", "expansion")
    result = SIG.shift_test(phase, frame, minimum_shift=40)

    empty = [row for row in result["cells"] if row["phase"] == "contraction"]
    assert len(empty) == len(french.INDUSTRIES)
    assert all(row["p_value"] is None for row in empty)
    assert all(row["observations"] == 0 for row in empty)

    correction = SIG.correct(result["cells"])
    assert correction["cells_skipped_for_lack_of_observations"] == len(french.INDUSTRIES)
    assert correction["cells_tested"] == 3 * len(french.INDUSTRIES)
    assert all(cell["phase"] != "contraction" for cell in correction["survives_bh"])

    by_phase = {row["phase"]: row for row in result["by_phase"]}
    assert by_phase["contraction"]["p_value"] is None


def test_benjamini_hochberg_keeps_a_prefix_of_the_sorted_p_values() -> None:
    rows = [
        {
            "phase": "expansion",
            "industry": name,
            "p_value": value,
            "effect_versus_all_weeks": 0.01,
        }
        for name, value in zip(
            french.INDUSTRIES,
            [0.0001, 0.002, 0.02, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95],
            strict=True,
        )
    ]
    correction = SIG.correct(rows, fdr=0.10)
    survivors = [cell["p"] for cell in correction["survives_bh"]]
    assert survivors == sorted(survivors)
    assert correction["expected_false_positives_at_five_percent"] == 0.6
    # Bonferroni는 BH보다 넓게 통과시킬 수 없다.
    assert len(correction["survives_bonferroni"]) <= len(correction["survives_bh"])


# ── 분포 겹침 ───────────────────────────────────────────────────────────────


def test_overlap_and_superiority_agree_on_identical_samples() -> None:
    values = np.linspace(-0.1, 0.1, 200)
    assert D.overlap_coefficient(values, values) == pytest.approx(1.0)
    assert D.probability_superior(values, values) == pytest.approx(0.5, abs=0.01)


def test_disjoint_samples_do_not_overlap() -> None:
    low = np.linspace(-1.0, -0.9, 100)
    high = np.linspace(0.9, 1.0, 100)
    assert D.overlap_coefficient(low, high) == pytest.approx(0.0)
    assert D.probability_superior(high, low) == pytest.approx(1.0)


def test_a_thin_cell_is_reported_as_insufficient_rather_than_summarised() -> None:
    index = [f"w{i}" for i in range(6)]
    phase = pd.Series(["contraction"] * 6, index=index)
    frame = pd.DataFrame(
        {name: [0.01] * 6 for name in french.INDUSTRIES}, index=index
    )
    cells = D.cells(phase, frame)
    assert cells["contraction"]["NoDur"]["sufficient"] is False
    assert "mean" not in cells["contraction"]["NoDur"]


# ── 순환매: 앞을 훔쳐보지 않는가 ─────────────────────────────────────────────


def test_rotation_weights_cannot_see_the_return_they_are_paid() -> None:
    """마지막 주에 한 산업이 폭등해도, 그 주의 비중이 그것을 보고 정해지면 안 된다."""

    weeks = [f"w{i:03d}" for i in range(60)]
    frame = pd.DataFrame(
        {name: [0.0] * 60 for name in french.INDUSTRIES} | {"MKT": [0.0] * 60},
        index=weeks,
    )
    frame.loc[weeks[-1], "Durbl"] = 1.0
    phase = pd.Series(["expansion"] * 60, index=weeks)

    result = rotation.run(phase, frame, top_k=1, minimum=10)
    # 모든 주의 상대수익률이 마지막 주 전까지 동일하므로 순위는 임의다. 중요한 것은
    # 마지막 주의 폭등을 미리 집어내지 못한다는 것 — 집어냈다면 성과가 크게 양수가 된다.
    assert result["rotation"]["annualised_relative_return"] < 0.5


def test_rotation_holds_the_market_until_it_has_seen_enough_of_a_phase() -> None:
    weeks = [f"w{i:03d}" for i in range(60)]
    frame = pd.DataFrame(
        {name: [0.001] * 60 for name in french.INDUSTRIES} | {"MKT": [0.0] * 60},
        index=weeks,
    )
    phase = pd.Series(["expansion"] * 60, index=weeks)
    result = rotation.run(phase, frame, minimum=50)
    assert result["weeks_held_market_for_lack_of_history"] == 50


def test_the_full_sample_ceiling_beats_the_expanding_window() -> None:
    """정답을 미리 본 쪽이 더 나아야 한다. 아니면 상한 계산이 잘못된 것이다."""

    phase, relative = _planted(weeks=400, effect=0.03)
    frame = relative.copy()
    frame["MKT"] = 0.0
    result = rotation.run(phase, frame, minimum=20)
    assert (
        result["rotation_full_sample_ceiling"]["annualised_relative_return"]
        >= result["rotation"]["annualised_relative_return"]
    )


# ── 라벨 ────────────────────────────────────────────────────────────────────


def test_episodes_count_blocks_not_weeks() -> None:
    phase = pd.Series(
        ["expansion"] * 10 + ["slowdown"] * 5 + ["expansion"] * 10,
        index=[f"w{i}" for i in range(25)],
    )
    assert labels.episodes(phase)["expansion"] == 2
    assert labels.episodes(phase)["slowdown"] == 1


def test_withheld_weeks_are_not_folded_into_a_phase() -> None:
    real_time = labels.load_real_time(str(ROOT / labels.REAL_TIME_PATH))
    counts = real_time.counts()
    assert counts[labels.WITHHELD] > 0
    assert sum(counts.values()) == len(real_time.weeks)


def test_the_two_labellings_cover_the_same_grid_where_they_overlap() -> None:
    revised = labels.load_revised(str(ROOT / labels.REVISED_PATH))
    real_time = labels.load_real_time(str(ROOT / labels.REAL_TIME_PATH))
    shared = labels.overlap(revised, real_time)
    assert shared == real_time.weeks
    assert set(shared).issubset(set(revised.weeks))


# ── 지연 비용 ───────────────────────────────────────────────────────────────


def test_latency_cost_is_not_claimed_when_neither_estimate_beats_chance() -> None:
    """두 추정치가 각각 우연 범위 안이면 그 차이를 비용이라 부르지 않는다."""

    revised = {"rotation": {"annualised_relative_return": 0.03}}
    real_time = {"rotation": {"annualised_relative_return": 0.01}}
    block = latency.cost(
        revised,
        real_time,
        {"p_value": 0.4},
        {"p_value": 0.6},
        {4: 1e-5, 13: 2e-5, 26: 3e-5},
        {4: 1e-5, 13: 2e-5, 26: 3e-5},
    )
    assert block["latency_cost_in_annualised_relative_return"] == pytest.approx(0.02)
    assert block["latency_cost_is_measurable"] is False
    assert block["either_estimate_beats_chance"] is False


def test_a_backwards_sign_is_flagged_rather_than_absorbed() -> None:
    block = latency.cost(
        {"rotation": {"annualised_relative_return": -0.01}},
        {"rotation": {"annualised_relative_return": 0.01}},
        {"p_value": 0.01},
        {"p_value": 0.01},
        {4: 0.0, 13: 0.0, 26: 0.0},
        {4: 0.0, 13: 0.0, 26: 0.0},
    )
    assert block["sign_is_backwards"] is True
    assert block["latency_cost_is_measurable"] is False


def test_recognition_delay_is_zero_when_the_labels_never_disagree() -> None:
    index = [f"w{i}" for i in range(20)]
    values = ["expansion"] * 10 + ["slowdown"] * 10
    series = pd.Series(values, index=index)
    delay = latency.recognition_delay(series, series)
    assert delay["median_delay_weeks"] == 0.0
    assert delay["never_matched"] == 0


# ── 표본 정의 ───────────────────────────────────────────────────────────────


def test_removing_covid_actually_removes_those_weeks() -> None:
    revised = labels.load_revised(str(ROOT / labels.REVISED_PATH))
    real_time = labels.load_real_time(str(ROOT / labels.REAL_TIME_PATH))
    shared = labels.overlap(revised, real_time)
    built = {sample.name: sample for sample in samples.build(revised, real_time, shared, None)}

    covid = built["revised_long_ex_covid"].weeks
    assert not any(samples.COVID_START <= week <= samples.COVID_END for week in covid)
    assert len(covid) < len(built["revised_long"].weeks)
    # 도려낸 표본은 복리 경로가 끊기므로 순환매 대상이 아니라고 표시돼 있어야 한다.
    assert built["revised_long_ex_covid"].contiguous is False
    assert built["revised_long"].contiguous is True


# ── 산출물 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_conclusion_matches_the_computed_verdict() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    verdict = payload["verdict"]
    report = (OUTPUT / "phase_returns_report.md").read_text(encoding="utf-8")

    assert verdict["statement"] in report
    assert payload["frozen_model_modified"] is False

    # 결론이 조건으로 남아 있어야 나중에 자료가 바뀌었을 때 다시 확인할 수 있다.
    long_run = verdict["long_history_overall_p_by_horizon"]
    assert verdict["taxonomy_discriminates_industry_returns"] == all(
        value <= 0.05 for value in long_run.values()
    )


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_every_sample_reports_its_episode_counts_next_to_its_week_counts() -> None:
    """주 수만 적고 에피소드 수를 빼면 18주짜리 한 덩어리가 18개 관측으로 읽힌다."""

    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    for result in payload["analysis"].values():
        profile = result["profile"]
        assert set(profile["phase_episodes"]) >= set(labels.PHASES)
        for phase in labels.PHASES:
            weeks = profile["phase_weeks"][phase]
            blocks = profile["phase_episodes"][phase]
            assert blocks <= weeks
            assert (blocks == 0) == (weeks == 0)


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_licence_boundary_is_stated_in_the_report() -> None:
    report = (OUTPUT / "phase_returns_report.md").read_text(encoding="utf-8")
    assert "내부 검증" in report
    assert "GICS" in report
    assert "Fama-French" in report
