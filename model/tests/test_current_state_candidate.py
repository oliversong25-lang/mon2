"""후보 I: 저반지름 싱크 회귀 방지, 유한 기억, 도메인 균형."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.config import load_baseline, load_settings
from business_cycle.current_state import domains as D
from business_cycle.current_state import scales as S
from business_cycle.current_state import validation as V
from business_cycle.current_state.classifier import (
    PHASES,
    StateThresholds,
    breadth_factor,
    major_scores,
    phase_scores,
    progression_score,
    separation,
)
from business_cycle.current_state.config import load_candidate
from business_cycle.current_state.stabilizer import stabilize, summary


def _thresholds() -> StateThresholds:
    return StateThresholds(
        neutral_level=0.5250,
        neutral_momentum=0.3849,
        contraction_level=-0.2873,
        contraction_level_domains=3,
        contraction_momentum_domains=3,
        concentration_flag=0.6104,
        progression_cuts={
            "recovery": (-0.9369, -0.3446),
            "expansion": (-0.5012, -0.2841),
            "slowdown": (-0.1579, 0.7594),
            "contraction": (1.4052, 1.9523),
        },
    )


def _scores(
    level: float, momentum: float, neg_level: int = 0, neg_mom: int = 0
) -> dict[str, float]:
    return phase_scores(level, momentum, neg_level, neg_mom, _thresholds())


# ── 저반지름 싱크 회귀 방지 ──────────────────────────────────────────────────


def test_no_phase_is_ever_assigned_exactly_zero() -> None:
    """0을 만들면 그 국면으로 가는 길이 막힌다. 후보 H를 133주 가둔 구조다."""

    for level, momentum in ((0.0, 0.0), (5.0, 5.0), (-5.0, -5.0), (0.01, -0.01)):
        scores = _scores(level, momentum)
        assert min(scores.values()) > 0.0
        assert pytest.approx(sum(scores.values()), rel=1e-9) == 1.0
        assert len(scores) == 12


def test_weak_evidence_gives_low_separation_not_high_certainty() -> None:
    """증거가 약할수록 분리도가 낮아야 한다. 후보 H에서는 반대였다."""

    weak = separation(_scores(0.02, -0.01))
    strong = separation(_scores(2.5, 2.5))
    assert weak < 0.15
    assert strong > weak


def test_low_signal_cannot_be_amplified_beyond_the_margin() -> None:
    index = pd.date_range("2026-01-02", periods=20, freq="W-FRI")
    frame = pd.DataFrame([_scores(0.01, -0.01) for _ in index], index=index)
    result = stabilize(frame, 0.0212)
    report = summary(frame, result)
    assert report["max_gain_within_margin"] is True
    assert report["any_zero_score"] is False


def test_persistent_contradictory_evidence_moves_the_official_phase() -> None:
    """직전 국면은 유리할 뿐 거부권이 없다."""

    index = pd.date_range("2026-01-02", periods=30, freq="W-FRI")
    rows = [_scores(2.0, 2.0) for _ in range(10)] + [_scores(-2.0, -2.0, 4, 4) for _ in range(20)]
    frame = pd.DataFrame(rows, index=index)
    result = stabilize(frame, 0.0212)
    assert result.official.iloc[5].startswith("expansion")
    assert result.official.iloc[-1].startswith("contraction")


def test_no_phase_becomes_an_absorbing_state_under_persistent_evidence() -> None:
    """어느 국면에서 출발하든 지속되는 반대 증거는 국면을 바꾼다."""

    index = pd.date_range("2026-01-02", periods=60, freq="W-FRI")
    frame = pd.DataFrame([_scores(2.0, 2.0) for _ in index], index=index)
    for start in PHASES:
        previous = start
        for _, row in frame.iterrows():
            adjusted = row.copy()
            adjusted[previous] = adjusted[previous] + 0.0212
            previous = str(adjusted.idxmax())
        assert previous.startswith("expansion"), f"{start}에서 출발했을 때 빠져나오지 못했다"


def test_finite_memory_paths_converge_on_identical_recent_evidence() -> None:
    """먼 과거가 다른 두 경로가 같은 최근 증거에서 만나야 한다."""

    index = pd.date_range("2026-01-02", periods=26, freq="W-FRI")
    frame = pd.DataFrame([_scores(-1.5, 1.2, 1, 0) for _ in index], index=index)
    result = V.convergence_test(frame, 0.0212, windows=(4, 13, 26))
    assert result["after_13_weeks"]["converged"] is True
    assert result["after_26_weeks"]["converged"] is True


# ── 도메인 균형과 지배 방지 ──────────────────────────────────────────────────


def test_two_indicator_domain_does_not_get_double_weight() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=2, freq="W-FRI"))
    signals = pd.DataFrame(
        {
            "INDPRO": [1.0, 1.0],
            "PAYEMS": [1.0, 1.0],
            "W875RX1": [1.0, 1.0],
            "RRSFS": [3.0, 3.0],
            "CMRMTSPL": [1.0, 1.0],
            "ICSA": [0.0, 0.0],
            "CCSA": [0.0, 0.0],
        },
        index=index,
    )
    levels = D.domain_level_frame(signals)
    # 소비는 두 계열의 평균이지 합이 아니다.
    assert levels["consumption"].iloc[0] == pytest.approx(2.0)
    assert levels["labor_stress"].iloc[0] == pytest.approx(0.0)
    assert list(levels.columns) == list(D.DOMAINS)


def test_one_extreme_domain_cannot_drive_the_aggregate() -> None:
    """후보 H에서 실질소득 한 도메인이 Y의 100% 이상을 만들었다. 중앙값은 그럴 수 없다."""

    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=1, freq="W-FRI"))
    balanced = pd.DataFrame(
        {
            "production": [0.2],
            "employment": [0.1],
            "real_income": [0.1],
            "consumption": [0.2],
            "labor_stress": [0.1],
        },
        index=index,
    )
    extreme = balanced.copy()
    extreme["real_income"] = [-9.0]
    assert D.aggregate_level(balanced).iloc[0] == pytest.approx(0.1)
    assert D.aggregate_level(extreme).iloc[0] == pytest.approx(0.1)


def test_labor_stress_alone_cannot_declare_contraction() -> None:
    """노동시장 스트레스는 동행 도메인이 아니다. 폭 계산에 들어가지 않는다."""

    assert "labor_stress" not in D.COINCIDENT_DOMAINS
    assert breadth_factor(0, 0, _thresholds()) == 0.0
    scores = _scores(-2.0, -2.0, 0, 0)
    assert sum(v for k, v in scores.items() if k.startswith("contraction")) < 0.01


def test_breadth_gate_moves_removed_mass_to_slowdown_not_to_zero() -> None:
    thresholds = _thresholds()
    narrow = major_scores(-2.0, -2.0, 1, 1, thresholds)
    broad = major_scores(-2.0, -2.0, 4, 4, thresholds)
    assert narrow["contraction"] < broad["contraction"]
    assert narrow["slowdown"] > broad["slowdown"]
    assert pytest.approx(sum(narrow.values()), rel=1e-9) == 1.0


# ── 오염된 X 척도를 물려받지 않는다 ──────────────────────────────────────────


def test_rolling_mad_is_not_inflated_by_a_single_shock() -> None:
    """한 번의 극단이 척도를 지배하면 그 뒤 10년간 모멘텀 단위가 왜곡된다."""

    index = pd.date_range("2000-01-07", periods=600, freq="W-FRI")
    rng = np.random.default_rng(42)
    values = pd.Series(rng.normal(0, 1, len(index)), index=index)
    shocked = values.copy()
    shocked.iloc[300] = 40.0
    window, minimum = 200, 50
    mad_clean = S.causal_scale(values, "rolling_mad", window, minimum).iloc[400]
    mad_shocked = S.causal_scale(shocked, "rolling_mad", window, minimum).iloc[400]
    std_clean = S.causal_scale(values, "rolling_std", window, minimum).iloc[400]
    std_shocked = S.causal_scale(shocked, "rolling_std", window, minimum).iloc[400]
    assert abs(mad_shocked / mad_clean - 1.0) < 0.02
    assert std_shocked / std_clean > 1.5


def test_scales_use_past_information_only() -> None:
    index = pd.date_range("2020-01-03", periods=200, freq="W-FRI")
    values = pd.Series(np.arange(200, dtype=float), index=index)
    future = values.copy()
    future.iloc[150:] = 1e6
    early = S.causal_scale(values, "rolling_mad", 100, 30).iloc[:150]
    early_future = S.causal_scale(future, "rolling_mad", 100, 30).iloc[:150]
    pd.testing.assert_series_equal(early, early_future)


def test_genuine_extremes_stay_extreme_after_robust_scaling() -> None:
    index = pd.date_range("2000-01-07", periods=400, freq="W-FRI")
    rng = np.random.default_rng(7)
    values = pd.Series(rng.normal(0, 1, len(index)), index=index)
    values.iloc[350] = 12.0
    scaled, _ = S.standardize(values, "rolling_mad", 200, 50)
    assert abs(scaled.iloc[350]) > 8.0


# ── 하위국면 의미 ────────────────────────────────────────────────────────────


def test_slowdown_progression_is_ordered_by_current_severity() -> None:
    """slowdown_late는 각도 구역이 아니라 '더 나빠진 현재 상태'여야 한다."""

    mild = progression_score("slowdown", 0.8, -0.1, 0)
    clear = progression_score("slowdown", 0.3, -0.4, 0)
    severe = progression_score("slowdown", -0.2, -0.6, 2)
    assert mild < clear < severe


def test_each_major_progression_has_an_economic_direction() -> None:
    assert progression_score("recovery", -1.5, 0.5, 0) < progression_score("recovery", -0.1, 0.5, 0)
    assert progression_score("expansion", 1.0, 0.9, 0) < progression_score("expansion", 1.0, 0.1, 0)
    assert progression_score("contraction", -1.0, -1.0, 3) < progression_score(
        "contraction", -2.5, -1.0, 4
    )


def test_monotonicity_check_detects_a_non_monotonic_subphase_set() -> None:
    """후보 H의 실패 형태(late가 middle보다 덜 나쁨)를 검사가 잡아내야 한다."""

    frame = pd.DataFrame(
        [
            {
                "phase": "slowdown_early",
                "broad": "slowdown",
                "weeks": 10,
                "level_median": 0.5,
                "momentum_median": -0.1,
                "negative_domains_median": 0.0,
                "concentration_median": 0.3,
            },
            {
                "phase": "slowdown_middle",
                "broad": "slowdown",
                "weeks": 10,
                "level_median": 0.5,
                "momentum_median": -0.3,
                "negative_domains_median": 1.0,
                "concentration_median": 0.3,
            },
            {
                "phase": "slowdown_late",
                "broad": "slowdown",
                "weeks": 10,
                "level_median": 0.9,
                "momentum_median": -0.1,
                "negative_domains_median": 0.0,
                "concentration_median": 0.4,
            },
        ]
    )
    checks = V.monotonicity_checks(frame)
    assert checks["slowdown"]["monotonic"] is False


# ── 좌표는 시각화 전용 ───────────────────────────────────────────────────────


def test_official_phase_does_not_depend_on_coordinate_sector_mapping() -> None:
    """구역 매핑을 바꿔도 공식 국면은 달라지지 않아야 한다."""

    scores = _scores(0.6, -0.5)
    winner = max(scores, key=lambda name: scores[name])
    shifted_bounds = {
        name: (float(i * 30 + 15), float(i * 30 + 45)) for i, name in enumerate(PHASES)
    }
    diagnostic = V.sector_agreement(
        pd.DataFrame({"angle": [200.0]}, index=pd.DatetimeIndex(["2026-08-14"])),
        pd.Series([winner], index=pd.DatetimeIndex(["2026-08-14"])),
        shifted_bounds,
    )
    assert "진단" in diagnostic["note"]
    assert max(scores, key=lambda name: scores[name]) == winner


# ── 동결 설정과 기존 산출물 ──────────────────────────────────────────────────


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_frozen_candidate_config_hash_is_unchanged() -> None:
    settings = load_settings()
    candidate = load_candidate(settings)
    recorded = (
        (_root() / "outputs" / "current_state" / "frozen_candidate_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert candidate.sha256 == recorded
    assert candidate.candidate == "candidate_i_current_state"


def test_candidate_i_does_not_use_the_low_radius_lock() -> None:
    settings = load_settings()
    candidate = load_candidate(settings)
    assert candidate.document["radius_role"] == "visualization_and_evidence_only"
    assert "low_radius_jump_constraint" not in candidate.document


def test_rejected_normalisation_variants_are_recorded_not_adopted() -> None:
    settings = load_settings()
    candidate = load_candidate(settings)
    rejected = candidate.document["normalization"]["rejected_variants"]
    assert "payems_over_population" in rejected
    assert "additional_inflation_adjustment" in rejected
    assert candidate.document["normalization"]["inherit_from"] == "candidate_h_breadth_gate"


def test_candidate_h_and_h2_remain_untouched() -> None:
    settings = load_settings()
    frozen = load_baseline("candidate_h_breadth_gate", settings)
    assert frozen.model["low_radius_jump_constraint"] is True
    assert "systemic_shock_override" not in frozen.model
    summary_h = json.loads(
        (
            _root() / "outputs" / "robustness_validation" / "phase6" / "validation_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        summary_h["frozen_hash"]
        == "c367e2a0f8e907b6f927191f03379bab5ea5eace6b671454c4b63e44d4b2bb21"
    )
    summary_h2 = json.loads(
        (
            _root() / "outputs" / "robustness_validation" / "phase8" / "validation_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert summary_h2["adopted"] is False


def test_candidate_i_is_recorded_as_rejected() -> None:
    """기각을 산출물에 남긴다. 실패를 조용히 지우지 않는다."""

    path = _root() / "outputs" / "current_state" / "validation_summary.json"
    if not path.exists():
        pytest.skip("후보 I 산출물이 아직 생성되지 않았다")
    summary_i = json.loads(path.read_text(encoding="utf-8"))
    assert summary_i["adopted"] is False
    assert summary_i["rejection_reasons"]
    # 구조 결함은 실제로 고쳐졌다는 것도 함께 남는다.
    assert summary_i["state_validity"]["candidate_I"]["longest_run_overall"] < 104
    assert summary_i["evidence_versus_separation"]["weak_high_separation_weeks"] == 0
    assert all(
        result["monotonic"] is True for result in summary_i["subphase_monotonicity"].values()
    )
