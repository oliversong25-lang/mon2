"""단계 A-4: 사건 날짜 정의, 영역 폭 게이트, 기하·영점 진단."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from business_cycle.config import load_baseline
from business_cycle.models.phase import emission_probabilities, phase_definitions
from business_cycle.pipeline import run_pipeline
from business_cycle.validation.phase6 import (
    CONFIRMATION_WEEKS,
    _classify_jump,
    event_timing,
)


def _history(phases: list[str], start: str = "2019-11-01") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(phases), freq="W-FRI")
    broad = [code.split("_")[0] for code in phases]
    return pd.DataFrame({"phase_code": phases, "broad_phase": broad}, index=index)


def _flags(index: pd.DatetimeIndex, start: str, end: str) -> pd.Series:
    return pd.Series((index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end)), index=index)


# ── 시점 개념 분리 ───────────────────────────────────────────────────────────


def test_first_signal_and_confirmation_decision_are_different_dates() -> None:
    """첫 신호와 4주 확인 결정일은 다른 개념이다. 한 필드로 묶으면 안 된다."""

    history = _history(["slowdown_late"] * 3 + ["contraction_early"] * 8)
    actual = _flags(pd.DatetimeIndex(history.index), "2020-01-24", "2020-02-14")
    timing = event_timing(
        history,
        actual,
        "test",
        pd.Timestamp("2020-01-24"),
        pd.Timestamp("2020-02-14"),
        pd.Timestamp("2019-11-01"),
    )
    assert timing.first_contraction_signal_date == "2019-11-22"
    # 4주 연속이 처음 충족되는 주는 첫 신호보다 3주 뒤다.
    assert timing.confirmation_decision_date == "2019-12-13"
    assert timing.first_contraction_signal_date != timing.confirmation_decision_date


def test_effective_episode_start_is_backdated_from_the_decision() -> None:
    history = _history(["slowdown_late"] * 3 + ["contraction_early"] * 8)
    actual = _flags(pd.DatetimeIndex(history.index), "2020-01-24", "2020-02-14")
    timing = event_timing(
        history,
        actual,
        "test",
        pd.Timestamp("2020-01-24"),
        pd.Timestamp("2020-02-14"),
        pd.Timestamp("2019-11-01"),
    )
    decision = pd.Timestamp(timing.confirmation_decision_date)
    effective = pd.Timestamp(timing.confirmed_episode_effective_date)
    assert (decision - effective).days == 7 * (CONFIRMATION_WEEKS - 1)
    assert timing.confirmed_episode_effective_date == timing.continuous_episode_start_date


def test_lead_lag_is_reported_from_both_reference_points() -> None:
    history = _history(["slowdown_late"] * 3 + ["contraction_early"] * 8)
    actual = _flags(pd.DatetimeIndex(history.index), "2020-01-24", "2020-02-14")
    timing = event_timing(
        history,
        actual,
        "test",
        pd.Timestamp("2020-01-24"),
        pd.Timestamp("2020-02-14"),
        pd.Timestamp("2019-11-01"),
    )
    assert timing.entry_lead_lag_from_first_signal == pytest.approx(-9.0)
    assert timing.entry_lead_lag_from_confirmation_decision == pytest.approx(-6.0)
    assert (
        timing.entry_lead_lag_from_first_signal != timing.entry_lead_lag_from_confirmation_decision
    )


def test_pre_and_post_nber_false_positives_are_counted_separately() -> None:
    """NBER 시작 전 오탐과 종료 후 오탐은 전혀 다른 문제다."""

    phases = ["contraction_early"] * 4 + ["recovery_early"] * 2 + ["contraction_mid"] * 6
    history = _history(phases)
    index = pd.DatetimeIndex(history.index)
    actual = _flags(index, "2019-12-13", "2019-12-27")
    timing = event_timing(
        history,
        actual,
        "test",
        pd.Timestamp("2019-12-13"),
        pd.Timestamp("2019-12-27"),
        pd.Timestamp("2019-11-01"),
    )
    # 시작 전 4주, 종료 후 3주. 같은 "오탐"이라도 성격이 다르므로 따로 센다.
    assert timing.pre_nber_false_positive_weeks == 4
    assert timing.post_nber_false_positive_weeks == 3


def test_continuous_episode_start_can_precede_the_search_window() -> None:
    history = _history(["contraction_early"] * 12, start="2019-10-04")
    index = pd.DatetimeIndex(history.index)
    actual = _flags(index, "2020-01-03", "2020-01-17")
    timing = event_timing(
        history,
        actual,
        "test",
        pd.Timestamp("2020-01-03"),
        pd.Timestamp("2020-01-17"),
        pd.Timestamp("2019-12-01"),
    )
    # 검색은 12월부터 시작했지만 연속 구간은 10월에 시작했다.
    assert timing.first_contraction_signal_date == "2019-12-06"
    assert timing.continuous_episode_start_date == "2019-10-04"


# ── 영역 폭 게이트 ───────────────────────────────────────────────────────────


def _phases():
    from business_cycle.config import load_settings

    return phase_definitions(load_settings().transitions["phases"])


def test_breadth_gate_blocks_narrow_contraction() -> None:
    """폭이 모자라면 침체 확률을 인접 국면으로 넘긴다."""

    phases = _phases()
    contraction = [index for index, phase in enumerate(phases) if phase.broad == "contraction"]
    narrow = emission_probabilities(200.0, 2.0, phases, 22.0, 2.0, 0.75, -2.0, 0.75, 3.0, 4.0)
    broad = emission_probabilities(200.0, 2.0, phases, 22.0, 2.0, 0.75, -2.0, 0.75, 4.0, 4.0)
    assert narrow[contraction].sum() < broad[contraction].sum()
    assert narrow[contraction].sum() == pytest.approx(0.0, abs=1e-12)
    assert broad.sum() == pytest.approx(1.0)
    assert narrow.sum() == pytest.approx(1.0)


def test_breadth_gate_does_not_block_broad_shocks() -> None:
    phases = _phases()
    contraction = [index for index, phase in enumerate(phases) if phase.broad == "contraction"]
    ungated = emission_probabilities(200.0, 3.0, phases, 22.0, 2.0, 0.75, -3.0, 0.75)
    gated = emission_probabilities(200.0, 3.0, phases, 22.0, 2.0, 0.75, -3.0, 0.75, 5.0, 4.0)
    assert gated[contraction].sum() == pytest.approx(ungated[contraction].sum())


def test_claims_pair_counts_as_one_domain(settings) -> None:
    """ICSA와 CCSA는 같은 영역이라 폭 계산에서 한 번만 세어진다."""

    indicators = settings.indicators["indicators"]
    assert indicators["ICSA"]["domain"] == indicators["CCSA"]["domain"]
    domains = {str(value["domain"]) for value in indicators.values()}
    assert len(domains) == 5


def test_breadth_gate_is_configured_only_where_declared() -> None:
    assert (
        load_baseline("coordinate_g_scale_only_10y").model.get("contraction_breadth_gate") is None
    )
    gate = load_baseline("candidate_h_breadth_gate").model["contraction_breadth_gate"]
    assert gate["enabled"] is True
    assert gate["minimum_domains"] == 4


def test_no_pandemic_specific_branching() -> None:
    """어떤 소스 파일도 특정 연도로 분기하지 않는다."""

    from pathlib import Path

    import business_cycle

    root = Path(business_cycle.__file__).parent
    # 검증 코드에는 사례 창이 있어도 되고, nber.py는 공식 기준일 표라 날짜가 자료다.
    # 지키려는 것은 "모델이 특정 날짜로 분기하지 않는다"이다. 지표를 계산하고 보고하는
    # 모듈은 그 대상이 아니지만, 면제는 **파일 하나씩** 지정한다. 파일명만 보면
    # 앞으로 만들 모델 패키지의 report.py까지 함께 면제되어 보호가 헐거워진다.
    allowed = {"nber.py"}
    reporting = {
        ("current_state", "validation.py"),
        ("current_state", "report.py"),
        ("candidate_j", "report.py"),
        # 4국면 보고 모듈. 침체 에피소드 시작일과 오탐 확인 구간을 **보고하기 위해**
        # 쓴다. 국면 판정 경로에는 들어가지 않으며, 그 사실은 four_phase 패키지의 날짜
        # 리터럴 검사가 모델 로직 모듈에서 따로 지킨다.
        ("four_phase", "report.py"),
        # 엄격 ALFRED. 2020년 기준일은 실시간 경로를 요약하기 위한 것이고, 시작 주
        # 2013-06-14는 아카이브 커버리지라는 자료의 사실이다.
        ("four_phase", "alfred.py"),
        # 기각 모델의 실시간 감사. 2019·2020 기준일은 감사 창을 정의할 뿐
        # 국면 판정 경로에 들어가지 않는다.
        ("four_phase", "alfred_audit.py"),
        # 운영 수용 심사. 이 패키지는 동결 모델을 **읽기만** 하고 국면 분류에 관여하지
        # 않는다. 2019·2020 날짜는 NBER 회고 라벨에 맞춘 평가 창을 정의할 뿐이다.
        ("operational_review", "review.py"),
        ("operational_review", "recovery.py"),
        ("operational_review", "revision.py"),
        ("operational_review", "__main__.py"),
        # 회복 인식 의미론 심사. 같은 이유다 — 동결 모델을 읽기만 하고, 2019·2020 날짜는
        # NBER 월간 전환점을 주간 격자에 얹는 평가 창일 뿐 국면 판정에 들어가지 않는다.
        ("recovery_semantics", "turning.py"),
        ("recovery_semantics", "review.py"),
        ("recovery_semantics", "consistency.py"),
        ("recovery_semantics", "canonical.py"),
        ("recovery_semantics", "decide.py"),
        ("recovery_semantics", "manifest.py"),
        ("recovery_semantics", "__main__.py"),
        # 상태 의미론 감사. 같은 이유다 — 동결 모델을 읽기만 하고, 날짜는 감사 창이다.
        ("state_semantics", "episodes.py"),
        ("state_semantics", "review.py"),
        ("state_semantics", "preserve.py"),
        # 전이 게이트. 2020 날짜는 인식 지연을 **재는 기준점**이고 NBER 대조 창이다.
        # 국면 판정 경로에는 들어가지 않는다 — 게이트는 분리도와 원시 동의만 본다.
        ("transition_gate", "gate.py"),
        ("transition_gate", "nber.py"),
        ("transition_gate", "__main__.py"),
        # 국면-수익률 검증. 2020 날짜는 "이 결과가 코로나 한 에피소드에 얹혀 있는가"를
        # 확인하는 제외 구간이며, 국면 판정에도 수익률 계산에도 들어가지 않는다.
        ("phase_returns", "samples.py"),
        ("phase_returns", "__main__.py"),
    }
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).parts
        if "validation" in path.parts or path.name in allowed or relative in reporting:
            continue
        text = path.read_text(encoding="utf-8")
        for token in ('"2020', "'2020", '"2019', "'2019"):
            if token in text:
                offenders.append(f"{path.name}:{token}")
    assert not offenders, offenders


# ── 점프 분류 ────────────────────────────────────────────────────────────────


def _jump(**overrides: object) -> dict[str, object]:
    row = {
        "radius": 1.2,
        "negative_domains": 3,
        "dominant_share": 0.3,
        "near_origin": False,
        "persists_four_weeks": True,
        "release_count": 3,
        "previous_phase": "slowdown_early",
        "new_phase": "slowdown_late",
    }
    row.update(overrides)
    return row


def test_broad_multi_domain_shock_is_justified() -> None:
    assert (
        _classify_jump(
            _jump(
                radius=12.0,
                negative_domains=4,
                previous_phase="recovery_early",
                new_phase="contraction_mid",
            )
        )
        == "economically justified shock jump"
    )


def test_low_radius_transient_jump_is_flagged_unstable() -> None:
    assert (
        _classify_jump(_jump(radius=0.2, near_origin=True, persists_four_weeks=False))
        == "low-radius model instability"
    )


def test_single_indicator_domination_is_flagged() -> None:
    assert _classify_jump(_jump(dominant_share=0.8)) == "single-indicator domination"


def test_within_broad_phase_move_is_not_treated_as_a_recession_call() -> None:
    """같은 대국면 안의 이동은 침체 판정을 바꾸지 않는다."""

    verdict = _classify_jump(_jump(negative_domains=1))
    assert verdict == "economically plausible but uncertain"
    crossing = _classify_jump(
        _jump(negative_domains=1, previous_phase="slowdown_late", new_phase="contraction_mid")
    )
    assert crossing == "unresolved"


# ── 기하와 영점 ──────────────────────────────────────────────────────────────


def test_scaled_coordinates_keep_x_and_y_comparable(settings, synthetic_data) -> None:
    """후보 E처럼 X가 Y보다 크게 작아지면 각도가 수직축으로 무너진다."""

    run = run_pipeline(
        synthetic_data, load_baseline("candidate_h_breadth_gate", settings), "2026-08-14"
    )
    ratio = float(run.history["x"].std() / run.history["y"].std())
    assert 0.5 <= ratio <= 2.0


def test_angles_do_not_collapse_onto_the_vertical_axes(settings, synthetic_data) -> None:
    run = run_pipeline(
        synthetic_data, load_baseline("candidate_h_breadth_gate", settings), "2026-08-14"
    )
    edges = [float(value) for value in np.arange(0, 391, 30)]
    occupancy = pd.Series(pd.cut(run.history["angle"], bins=edges, right=False)).value_counts(
        normalize=True
    )
    assert float(occupancy.max()) < 0.35
    assert float(run.history["phase_code"].value_counts(normalize=True).max()) < 0.40


def test_zero_center_holds_during_expansions(settings, synthetic_data) -> None:
    """영점 중심은 "항상 0"이 아니라 "정상기에 표류가 없다"는 뜻이다."""

    run = run_pipeline(
        synthetic_data, load_baseline("candidate_h_breadth_gate", settings), "2026-08-14"
    )
    factor = run.composite.dropna()
    scale = float(run.coordinate_audit["coordinate_scale"].median())
    expansion = factor[run.history["broad_phase"].reindex(factor.index).ne("contraction")]
    assert abs(float(expansion.mean()) / scale) < 0.5


def test_coordinate_center_is_exactly_zero_for_scale_only(settings) -> None:
    model = load_baseline("candidate_h_breadth_gate", settings).model
    assert model["coordinate_standardization_method"] == "scale_only"


# ── 현재 판정과 선행 레이어 분리 ─────────────────────────────────────────────


def test_leading_layer_does_not_move_the_current_phase(settings, synthetic_data) -> None:
    variant = load_baseline("candidate_h_breadth_gate", settings)
    without = run_pipeline(synthetic_data, variant, "2026-08-14")
    signals = pd.DataFrame(
        {"PERMIT": [-5.0] * 40},
        index=pd.date_range("2025-11-07", periods=40, freq="W-FRI"),
    )
    with_leading = run_pipeline(synthetic_data, variant, "2026-08-14", signals)
    assert without.result.current_phase == with_leading.result.current_phase
    assert with_leading.result.forecast_13w["status"] == "not_calibrated"
