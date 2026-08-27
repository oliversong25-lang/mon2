"""해석층: 공식 국면은 하나, 표시 규칙은 모델을 바꾸지 않는다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from business_cycle.interpretation import contract, parity
from business_cycle.interpretation.boundary import BOUNDARY_GAP, boundary_audit, boundary_view
from business_cycle.interpretation.confidence import confidence_view
from business_cycle.interpretation.countries import REGISTRY, not_implemented_payload
from business_cycle.interpretation.domains import (
    DomainReading,
    domain_breadth,
    domain_contributions,
)
from business_cycle.interpretation.industry import availability_audit, industry_view
from business_cycle.interpretation.transition import (
    OVERWHELMING_ONE_WEEK_RISE,
    SUSTAINED_FOUR_WEEK_RISE,
    transition_view,
)

PHASES = list(contract.PHASE_ORDER)


def _history(
    winner: str = "slowdown_late",
    winner_probability: float = 0.9,
    runner_up: str = "slowdown_mid",
    runner_up_probability: float | None = None,
    weeks: int = 12,
) -> pd.DataFrame:
    """확률 경로를 손으로 만든다. 모델을 부르지 않는다.

    1·2순위를 정확히 지정하고 나머지가 잔여를 균등하게 나누므로, 합이 1이면서
    원하는 차이를 그대로 만들 수 있다. 사후 정규화를 하면 차이가 흐트러진다.
    """

    index = pd.date_range("2026-01-02", periods=weeks, freq="W-FRI")
    second = (
        runner_up_probability
        if runner_up_probability is not None
        else (1.0 - winner_probability) / 2.0
    )
    rest = (1.0 - winner_probability - second) / (len(PHASES) - 2)
    assert rest >= 0.0, "잔여 확률이 음수가 되지 않게 값을 고르세요"
    frame = pd.DataFrame({f"p_{name}": [rest] * weeks for name in PHASES}, index=index)
    frame[f"p_{winner}"] = winner_probability
    frame[f"p_{runner_up}"] = second
    frame["phase_code"] = winner
    frame["broad_phase"] = winner.split("_")[0]
    frame["x"] = 0.4
    frame["y"] = 0.6
    frame["radius"] = 0.72
    return frame


# ── 1~3. 공식 국면은 언제나 하나이고 모델이 고른 것이다 ──────────────────────


def test_exactly_one_official_broad_phase() -> None:
    history = _history()
    view = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    assert isinstance(history["broad_phase"].iloc[-1], str)
    assert history["broad_phase"].iloc[-1] in contract.BROAD_PHASES
    assert view.runner_up_phase != history["phase_code"].iloc[-1]


def test_exactly_one_official_detailed_phase() -> None:
    label = contract.label_detailed_phase("slowdown_mid")
    assert label == "slowdown_middle"
    assert label in contract.DETAILED_PHASES
    assert len(contract.DETAILED_PHASES) == 12


def test_label_map_is_a_bijection_that_preserves_order() -> None:
    """이름표만 바꾼다. 순서가 바뀌면 인접 국면 계산이 무너진다."""

    assert len(set(contract.PHASE_LABEL_MAP.values())) == 12
    assert list(contract.PHASE_LABEL_MAP) == list(contract.PHASE_ORDER)
    for code, label in contract.PHASE_LABEL_MAP.items():
        assert label.split("_")[0] == code.split("_")[0]


def test_unknown_phase_code_is_rejected_not_invented() -> None:
    with pytest.raises(contract.SchemaViolation):
        contract.label_detailed_phase("expansion_super_late")


# ── 4~7. 표시 규칙은 공식 국면을 건드리지 않는다 ─────────────────────────────


def test_runner_up_differs_from_the_official_phase() -> None:
    history = _history(winner="expansion_mid", runner_up="expansion_late")
    view = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    assert view.runner_up_phase == "expansion_late"
    assert view.runner_up_phase != "expansion_mid"


def test_boundary_flag_does_not_change_the_official_phase() -> None:
    """경계에 걸려도 승자는 그대로다. 공식 국면이 둘이 되지 않는다."""

    tight = _history(winner_probability=0.52, runner_up_probability=0.45)
    before = tight["phase_code"].tolist()
    view = boundary_view(tight, pd.Timestamp(str(tight.index[-1])))
    assert view.boundary_flag is True
    assert view.gap <= BOUNDARY_GAP
    assert tight["phase_code"].tolist() == before


def test_transition_watch_does_not_change_the_official_phase() -> None:
    history = _history()
    before = history.copy()
    view = transition_view(history, pd.Timestamp(str(history.index[-1])), "slowdown_late")
    assert isinstance(view.transition_watch, bool)
    pd.testing.assert_frame_equal(history, before)


def test_confidence_does_not_change_the_official_phase() -> None:
    history = _history()
    before = history.copy()
    boundary = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    confidence_view(boundary, [], "official", 0.9, [])
    pd.testing.assert_frame_equal(history, before)


# ── 8. 확률은 그대로다 ───────────────────────────────────────────────────────


def test_probabilities_are_untouched_by_the_layer() -> None:
    history = _history()
    columns = [f"p_{name}" for name in PHASES]
    before = history[columns].copy()
    boundary_audit(history)
    boundary_view(history, pd.Timestamp(str(history.index[-1])))
    transition_view(history, pd.Timestamp(str(history.index[-1])), "slowdown_late")
    pd.testing.assert_frame_equal(history[columns], before)


# ── 전환 감시 규칙 ───────────────────────────────────────────────────────────


def _rising_history(total_rise: float, weeks: int = 8) -> pd.DataFrame:
    history = _history(weeks=weeks)
    target = "contraction_early"
    step = total_rise / 4.0
    values = [0.02] * weeks
    for offset in range(4):
        values[weeks - 4 + offset] = 0.02 + step * (offset + 1)
    history[f"p_{target}"] = values
    return history


def test_sustained_rise_triggers_the_watch() -> None:
    history = _rising_history(SUSTAINED_FOUR_WEEK_RISE + 0.05)
    view = transition_view(history, pd.Timestamp(str(history.index[-1])), "slowdown_late")
    assert view.transition_watch is True
    assert view.trigger == "sustained_rise"
    assert view.transition_direction == "contraction_early"


def test_a_rise_below_the_threshold_does_not_trigger() -> None:
    history = _rising_history(SUSTAINED_FOUR_WEEK_RISE - 0.05)
    view = transition_view(history, pd.Timestamp(str(history.index[-1])), "slowdown_late")
    assert view.transition_watch is False
    assert view.trigger == "none"


def test_one_overwhelming_observation_can_trigger_on_its_own() -> None:
    """한 주만으로는 인정하지 않되, 이력 상위 1%의 움직임은 예외로 둔다."""

    history = _history(weeks=8)
    values = [0.02] * 8
    values[-1] = 0.02 + OVERWHELMING_ONE_WEEK_RISE + 0.01
    history["p_contraction_early"] = values
    view = transition_view(history, pd.Timestamp(str(history.index[-1])), "slowdown_late")
    assert view.transition_watch is True
    assert view.trigger == "overwhelming_single_observation"


def test_boundary_and_transition_are_different_questions() -> None:
    """붙어 있는 것과 밀리고 있는 것은 다르다. 둘이 같이 움직이면 안 된다."""

    history = _rising_history(SUSTAINED_FOUR_WEEK_RISE + 0.05)
    timestamp = pd.Timestamp(str(history.index[-1]))
    boundary = boundary_view(history, timestamp)
    transition = transition_view(history, timestamp, "slowdown_late")
    assert boundary.boundary_flag is False
    assert transition.transition_watch is True


# ── 9~10. 영역 매핑과 이름 구분 ──────────────────────────────────────────────


def test_domain_contributions_are_mapped_to_contract_names(tmp_path: Path) -> None:
    from business_cycle.config import load_settings

    settings = load_settings()
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=2, freq="W-FRI"))
    contributions = pd.DataFrame(
        {
            "PAYEMS": [0.1, 0.1],
            "W875RX1": [0.2, 0.2],
            "INDPRO": [-0.1, -0.1],
            "CMRMTSPL": [0.05, 0.05],
            "RRSFS": [0.05, 0.05],
            "ICSA": [-0.2, -0.2],
            "CCSA": [-0.1, -0.1],
        },
        index=index,
    )
    frame = domain_contributions(contributions, settings, index)
    assert set(frame.columns) == set(contract.ECONOMIC_DOMAINS)
    # 실업수당 두 계열은 하나의 영역으로 합쳐진다.
    assert frame["claims"].iloc[0] == pytest.approx(-0.3)
    # 소비는 두 지표의 합이다.
    assert frame["consumption"].iloc[0] == pytest.approx(0.1)


def test_economic_domain_breadth_is_not_called_industry_breadth() -> None:
    readings = [
        DomainReading("employment", "positive", 0.1, 0.5, 0.01, True, False, "supports", 1.0, False)
    ]
    breadth = domain_breadth(readings)
    assert breadth["measure"] == "economic_domain_breadth"
    assert "industry" not in json.dumps(breadth)


# ── 11~12. 산업 진단 ─────────────────────────────────────────────────────────


def test_missing_industry_data_produces_not_available(tmp_path: Path) -> None:
    audit = availability_audit(tmp_path, ["PAYEMS", "INDPRO"])
    view = industry_view(audit)
    assert view.industry_breadth_status == "not_available"
    assert view.industry_concentration_status == "not_measured"
    assert view.available_dimensions == 0
    assert (audit["status"] == "not_available").all()


def test_industry_diagnostics_cannot_override_the_official_phase(tmp_path: Path) -> None:
    """산업 진단은 별도 진단이다. 국면 어휘를 만들지 않는다."""

    audit = availability_audit(tmp_path, [])
    view = industry_view(audit)
    assert view.industry_breadth_status not in contract.BROAD_PHASES
    assert view.industry_breadth_status not in contract.DETAILED_PHASES
    assert not hasattr(view, "official_broad_phase")
    assert not hasattr(view, "phase_probability")


# ── 13~14. 투자 판단 필드가 없다 ─────────────────────────────────────────────


def _valid_payload() -> dict[str, Any]:
    return {
        "country": "US",
        "as_of_date": "2026-08-14",
        "official_broad_phase": "slowdown",
        "official_detailed_phase": "slowdown_late",
        "recession_status": "no",
        "official_phase_probability": 0.985,
        "runner_up_phase": "slowdown_middle",
        "runner_up_probability": 0.015,
        "winner_runner_up_gap": 0.97,
        "confidence_level": "high",
        "boundary_flag": False,
        "boundary_reason": "차이가 크다",
        "transition_watch": False,
        "transition_direction": "contraction_early",
        "data_status": "official",
        "supporting_domains": ["production"],
        "opposing_domains": [],
        "domain_breadth": {"measure": "economic_domain_breadth", "supporting": 1},
        "industry_breadth_status": "not_available",
        "industry_concentration_status": "not_measured",
        "short_explanation": "생산이 판정을 뒷받침한다.",
        "limitations": ["투자 판단이 아니다"],
    }


@pytest.mark.parametrize(
    "field",
    [
        "recommendation",
        "target_price",
        "portfolio_weight",
        "sector_allocation",
        "buy_signal",
        "intrinsic_value",
        "ticker_ranking",
    ],
)
def test_investment_fields_are_rejected(field: str) -> None:
    payload = _valid_payload()
    payload[field] = "무엇이든"
    with pytest.raises(contract.SchemaViolation, match="투자 판단"):
        contract.validate_output(payload)


def test_investment_fields_are_rejected_even_when_nested() -> None:
    payload = _valid_payload()
    payload["domain_breadth"] = {"measure": "x", "detail": [{"portfolio_action": "increase"}]}
    with pytest.raises(contract.SchemaViolation, match="투자 판단"):
        contract.validate_output(payload)


def test_valuation_and_portfolio_fields_are_rejected() -> None:
    for field in ("valuation_multiple", "allocation_target", "fair_value_estimate"):
        payload = _valid_payload()
        payload[field] = 1.0
        with pytest.raises(contract.SchemaViolation):
            contract.validate_output(payload)


# ── 18. 스키마 검증 ──────────────────────────────────────────────────────────


def test_full_schema_validation_accepts_a_correct_payload() -> None:
    contract.validate_output(_valid_payload())


def test_missing_required_field_is_rejected() -> None:
    payload = _valid_payload()
    del payload["confidence_level"]
    with pytest.raises(contract.SchemaViolation, match="필수 필드"):
        contract.validate_output(payload)


def test_ambiguous_official_label_is_rejected() -> None:
    """경계에서도 공식 국면은 하나다. 두 국면을 붙인 라벨을 막는다."""

    payload = _valid_payload()
    payload["official_detailed_phase"] = "expansion_late or slowdown_early"
    with pytest.raises(contract.SchemaViolation):
        contract.validate_output(payload)


def test_runner_up_equal_to_official_phase_is_rejected() -> None:
    payload = _valid_payload()
    payload["runner_up_phase"] = payload["official_detailed_phase"]
    with pytest.raises(contract.SchemaViolation, match="2순위"):
        contract.validate_output(payload)


def test_broad_and_detailed_phase_must_agree() -> None:
    payload = _valid_payload()
    payload["official_broad_phase"] = "expansion"
    with pytest.raises(contract.SchemaViolation, match="어긋납니다"):
        contract.validate_output(payload)


# ── 15. 날짜별 분기가 없다 ───────────────────────────────────────────────────


def test_no_date_specific_branching_in_the_layer() -> None:
    """특정 사건의 날짜를 코드에 박아 두면 그 사건에만 맞는 규칙이 된다."""

    package = Path(__file__).resolve().parents[1] / "src" / "business_cycle" / "interpretation"
    pattern = __import__("re").compile(r"(?<!\d)(19|20)\d{2}-\d{2}-\d{2}(?!\d)")
    allowed = {"1995-01-01", "2026-08-14", "2026-01-02"}
    for path in package.glob("*.py"):
        for match in pattern.findall(path.read_text(encoding="utf-8")):
            del match
        for literal in pattern.finditer(path.read_text(encoding="utf-8")):
            assert literal.group(0) in allowed, f"{path.name}에 날짜 분기: {literal.group(0)}"


# ── 16~17. 후보 H·H2 산출물 불변 ─────────────────────────────────────────────


def _artifact(*parts: str) -> Path:
    root = Path(__file__).resolve().parents[1]
    return root.joinpath("outputs", "robustness_validation", *parts)


def test_candidate_h_artifacts_are_unchanged() -> None:
    frozen = _artifact("phase6", "frozen_model_config.yaml")
    recorded = _artifact("phase6", "frozen_model_config.sha256")
    assert frozen.exists() and recorded.exists()
    summary = json.loads(_artifact("phase6", "validation_summary.json").read_text(encoding="utf-8"))
    assert summary["selected_candidate"] == "candidate_h_breadth_gate"
    assert summary["stage_a4_passed"] is True
    assert (
        summary["frozen_hash"] == "c367e2a0f8e907b6f927191f03379bab5ea5eace6b671454c4b63e44d4b2bb21"
    )


def test_candidate_h2_artifacts_are_unchanged() -> None:
    summary = json.loads(_artifact("phase8", "validation_summary.json").read_text(encoding="utf-8"))
    assert summary["candidate"] == "candidate_h2_systemic_override"
    # 단계 A-5의 판정은 유지된다. 해석층이 이 결과를 다시 해석하지 않는다.
    assert summary["alfred_passed"] is False
    assert summary["adopted"] is False
    assert summary["detection"]["official_recession_weeks_called_contraction"] == 0


# ── 19. 미래 자료 불변 ───────────────────────────────────────────────────────


def test_layer_output_is_unchanged_by_future_observations() -> None:
    """뒤에 자료가 더 붙어도 그 시점의 진단은 같아야 한다."""

    history = _rising_history(SUSTAINED_FOUR_WEEK_RISE + 0.05, weeks=10)
    timestamp = pd.Timestamp(str(history.index[7]))
    early = history.iloc[:8]
    boundary_early = boundary_view(early, timestamp)
    boundary_full = boundary_view(history, timestamp)
    assert boundary_early == boundary_full
    transition_early = transition_view(early, timestamp, "slowdown_late")
    transition_full = transition_view(history, timestamp, "slowdown_late")
    assert transition_early == transition_full


# ── 국가 등록 ────────────────────────────────────────────────────────────────


def test_korea_and_china_are_not_implemented_and_produce_no_phase() -> None:
    for code in ("KR", "CN"):
        assert REGISTRY[code].status == "not_implemented"
        payload = not_implemented_payload(code)
        assert payload["official_broad_phase"] is None
        assert payload["official_detailed_phase"] is None
        assert payload["as_of_date"] is None
        assert payload["required_domains"]


def test_diagnosing_an_unimplemented_country_is_refused() -> None:
    from business_cycle.interpretation.diagnosis import diagnose

    with pytest.raises(ValueError, match="인과 모델이 없습니다"):
        diagnose(None, None, "KR")  # type: ignore[arg-type]


def test_us_is_registered_as_implemented_with_the_frozen_baseline() -> None:
    assert REGISTRY["US"].status == "implemented"
    assert REGISTRY["US"].model_baseline == "candidate_h_breadth_gate"


# ── 패리티 도구 ──────────────────────────────────────────────────────────────


def test_parity_hash_detects_a_single_changed_probability(tmp_path: Path) -> None:
    """패리티는 기계로 확인해야 의미가 있다. 한 값만 바뀌어도 잡혀야 한다."""

    history = _history()
    frame = parity.core_frame(history, "official")
    path = tmp_path / "baseline.csv"
    parity.write_baseline(frame, path)
    assert parity.compare(frame, path).matches is True

    tampered = history.copy()
    tampered.loc[tampered.index[-1], "p_slowdown_late"] = 0.5
    result = parity.compare(parity.core_frame(tampered, "official"), path)
    assert result.matches is False
    assert "p_slowdown_late" in result.first_difference


def test_parity_hash_detects_a_changed_status(tmp_path: Path) -> None:
    history = _history()
    path = tmp_path / "baseline.csv"
    parity.write_baseline(parity.core_frame(history, "official"), path)
    result = parity.compare(parity.core_frame(history, "preliminary"), path)
    assert result.matches is False


def test_parity_compares_the_required_fields() -> None:
    report = parity.parity_report(
        parity.ParityResult(True, "a", "a", 10, ""), {"frozen_model_config.yaml": "x"}
    )
    for field in (
        "official_broad_phase",
        "official_detailed_phase",
        "phase_probabilities",
        "x",
        "y",
        "radius",
        "model_status",
    ):
        assert field in report["compared_fields"]


# ── 확신도 ───────────────────────────────────────────────────────────────────


def _reading(domain: str, stance: str) -> DomainReading:
    return DomainReading(
        domain=domain,
        direction="positive",
        standardized_contribution=0.1,
        contribution_share=0.2,
        recent_change=0.01,
        supports_official_phase=stance == "supports",
        opposes_official_phase=stance == "opposes",
        stance=stance,
        data_freshness_weeks=1.0,
        missing=False,
    )


def test_clean_week_is_high_confidence() -> None:
    history = _history()
    boundary = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    view = confidence_view(boundary, [_reading("employment", "supports")], "official", 0.95, [])
    assert view.confidence_level == "high"
    assert view.confidence_reasons == []


def test_each_risk_condition_appears_as_a_reason_code() -> None:
    history = _history(winner_probability=0.52, runner_up_probability=0.45)
    boundary = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    view = confidence_view(
        boundary,
        [_reading("production", "opposes")],
        "preliminary",
        0.1,
        ["income"],
    )
    assert view.confidence_level == "low"
    assert any("gap" in reason for reason in view.confidence_reasons)
    assert any("data status" in reason for reason in view.confidence_reasons)
    assert any("agreement" in reason for reason in view.confidence_reasons)
    assert any("disagree" in reason for reason in view.confidence_reasons)
    assert any("stale" in reason for reason in view.confidence_reasons)


def test_withheld_status_forces_low_confidence() -> None:
    history = _history()
    boundary = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    view = confidence_view(boundary, [_reading("employment", "supports")], "withheld", 0.99, [])
    assert view.confidence_level == "low"


def test_confidence_is_not_claimed_to_be_calibrated() -> None:
    history = _history()
    boundary = boundary_view(history, pd.Timestamp(str(history.index[-1])))
    view = confidence_view(boundary, [], "official", 0.9, [])
    assert view.calibrated is False
    assert "보정" in view.note


# ── 경계 규칙이 자료와 맞는지 ────────────────────────────────────────────────


def test_boundary_threshold_is_documented_as_a_measurement() -> None:
    """상수가 근거를 잃지 않게, 문서와 코드가 같은 값을 가리키는지 본다."""

    policy = (
        Path(__file__).resolve().parents[1]
        / "outputs"
        / "phase_interpretation"
        / "boundary_policy.md"
    )
    if not policy.exists():
        pytest.skip("해석층 산출물이 아직 생성되지 않았다")
    assert str(BOUNDARY_GAP) in policy.read_text(encoding="utf-8")


def test_entropy_is_higher_when_probabilities_are_spread() -> None:
    from business_cycle.interpretation.boundary import phase_entropy

    concentrated = np.array([[0.9] + [0.1 / 11] * 11])
    spread = np.array([[1.0 / 12] * 12])
    assert phase_entropy(spread)[0] > phase_entropy(concentrated)[0]
