"""회복 인식 의미론 심사: 규약, 지연 층 분리, 결정 트리, 계약, 재현성.

이 단계는 동결 v1.1을 **읽기만** 한다. 테스트도 그 사실을 주장으로 두지 않고 확인한다.
skip을 쓰지 않는다 — 건너뛴 시험은 통과한 시험이 아니다.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from business_cycle.config import load_settings
from business_cycle.four_phase.engine import load_config
from business_cycle.operational_review.preserve import ProtectedArtifactChanged
from business_cycle.recovery_semantics import (
    canonical,
    consistency,
    decide,
    gates,
    latency,
    manifest,
    monitoring,
    preserve,
    review,
    timeline,
    turning,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _output() -> Path:
    return _root() / "outputs" / "recovery_semantics"


def _summary() -> dict[str, Any]:
    path = _output() / "validation_summary.json"
    assert path.exists(), "회복 의미론 산출물이 없다. `python -m business_cycle.recovery_semantics`"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _decision() -> dict[str, Any]:
    path = _output() / "recovery_semantics_decision.json"
    assert path.exists(), "결정 산출물이 없다"
    return dict(json.loads(path.read_text(encoding="utf-8")))


# ── 보존 ─────────────────────────────────────────────────────────────────────


def test_protected_hashes_are_verified_before_analysis() -> None:
    provenance = preserve.verify(load_settings())
    assert provenance["verified"] is True
    assert provenance["expected_source_commit"] == preserve.SOURCE_COMMIT
    assert provenance["stage_is_read_only"] is True
    assert provenance["parameter_search_run"] is False
    assert provenance["model_logic_changed"] is False


def test_the_recorded_provenance_matches_the_verified_hashes() -> None:
    recorded = dict(_summary()["provenance"])
    measured = preserve.verify(load_settings())
    assert recorded["hashes"] == measured["hashes"]
    assert recorded["expected_source_commit"] == preserve.SOURCE_COMMIT


def test_a_changed_protected_hash_stops_the_stage() -> None:
    from business_cycle.operational_review.preserve import PROTECTED

    original = dict(PROTECTED)
    try:
        PROTECTED["v1_1_config"] = "0" * 64
        with pytest.raises(ProtectedArtifactChanged):
            preserve.verify(load_settings())
    finally:
        PROTECTED.clear()
        PROTECTED.update(original)


def test_no_protected_model_or_configuration_path_was_modified() -> None:
    assert preserve.git_status_of_protected_paths(load_settings()) == []
    for name in ("configs/four_phase_v1_1.yaml", "src/business_cycle/four_phase/engine.py"):
        assert name in preserve.PROTECTED_PATHS


def test_the_prior_rejections_are_carried_forward_unchanged() -> None:
    provenance = dict(_summary()["provenance"])
    prior = dict(provenance["prior_decisions_preserved"])
    assert prior["four_phase_v1_1_latest_vintage_protocol"] == "rejected"
    assert prior["operational_review"] == "operational_rejection"
    assert _decision()["prior_v1_1_status_under_the_latest_vintage_protocol"] == "rejected"


def test_the_prior_decision_is_guarded_by_content_not_by_file_bytes() -> None:
    record = preserve.prior_decision_record(load_settings())
    assert record["classification"] == "operational_rejection"
    assert record["v1_1_status"] == "rejected"
    assert record["compared_by"] == "decision_content_not_file_bytes"
    # 앞 단계를 다시 돌리면 `executed_at_utc`만 바뀐 산출물이 다시 쓰인다. 그 시각 차이가
    # 이 단계를 멈춰서는 안 되므로, 산출물 경로는 git 엄격 검사 대상이 아니다.
    assert "outputs/operational_review" not in preserve.PROTECTED_PATHS
    # 대신 결정 자체가 바뀌면 멈춘다.
    assert record["failed_gates"]


def test_a_changed_prior_decision_stops_the_stage() -> None:
    settings = load_settings()
    original = (settings.root / preserve.PRIOR_DECISION_RECORD).read_text(encoding="utf-8")
    target = settings.root / preserve.PRIOR_DECISION_RECORD
    try:
        tampered = dict(json.loads(original))
        tampered["classification"] = "provisional_operational_adoption"
        target.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(ProtectedArtifactChanged):
            preserve.prior_decision_record(settings)
    finally:
        target.write_text(original, encoding="utf-8", newline="\n")


def test_this_stage_never_searched_parameters() -> None:
    package = _root() / "src" / "business_cycle" / "recovery_semantics"
    # 지문 이름으로서의 `selection_rule`은 보존 대상이지 탐색이 아니다. 탐색을 실제로
    # 만드는 것들만 금지한다 — 격자, 조합 폭발, 최적화, 프런티어 실행.
    banned = (
        "itertools.product",
        "linspace",
        "param_grid",
        "search_space",
        "optimi",
        "import frontier",
        "frontier.run",
        "frontier.search",
        "Thresholds(",
    )
    for path in package.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in body, f"{path.name}에 모수 탐색 흔적이 있다: {token}"


# ── §3 월간 전환점 구간 ──────────────────────────────────────────────────────


def test_turning_month_intervals_span_the_whole_month() -> None:
    for episode, month in turning.TROUGH_MONTHS.items():
        interval = turning.turning_month(episode)
        assert str(interval.start.date()) == f"{month}-01"
        assert interval.end.day == interval.end.days_in_month
        assert interval.start.month == interval.end.month


def test_the_three_positions_are_decided_by_the_interval_not_a_single_day() -> None:
    gfc = turning.turning_month("gfc_2009")
    assert gfc.position("2009-05-31") == "pre_trough_recovery"
    assert gfc.position("2009-06-01") == "within_turning_month"
    assert gfc.position("2009-06-05") == "within_turning_month"
    assert gfc.position("2009-06-30") == "within_turning_month"
    assert gfc.position("2009-07-01") == "post_trough_delay"
    assert gfc.position(None) is None


def test_within_trough_month_recovery_is_not_labelled_definitively_premature() -> None:
    detail = dict(_summary()["episode_2009"])
    assert detail["first_official_recovery"] == "2009-06-05"
    assert detail["position_of_first_official_recovery"] == "within_turning_month"
    assert detail["reclassification"] == "within_turning_month"
    assert detail["definitively_premature"] is False
    # 뒤에 저점이 있었다는 독립 주간 증거를 지어내지 않았다.
    assert detail["independent_weekly_evidence_of_a_later_trough"] is None
    # 앞 단계의 기록은 지우지 않는다.
    assert detail["original_finding_preserved"] is True


def test_pre_trough_recovery_is_still_detected() -> None:
    index = pd.date_range("2009-05-01", periods=10, freq="W-FRI")
    frame = pd.DataFrame(
        {"official_phase": ["recovery"] * 10, "raw_phase": ["recovery"] * 10},
        index=pd.Index([str(value.date()) for value in index]),
    )
    scan = gates.pre_trough_recovery_scan(frame, "gfc_2009", turning.turning_month("gfc_2009"))
    assert scan["official_phase__genuine_pre_trough_four_week_recovery"] == "2009-05-01"


def test_a_run_starting_inside_the_month_is_not_a_pre_trough_recovery() -> None:
    index = pd.date_range("2009-06-05", periods=8, freq="W-FRI")
    frame = pd.DataFrame(
        {"official_phase": ["recovery"] * 8, "raw_phase": ["recovery"] * 8},
        index=pd.Index([str(value.date()) for value in index]),
    )
    scan = gates.pre_trough_recovery_scan(frame, "gfc_2009", turning.turning_month("gfc_2009"))
    assert scan["official_phase__first_four_week_recovery_from_the_peak"] == "2009-06-05"
    assert scan["official_phase__genuine_pre_trough_four_week_recovery"] is None


def test_the_raw_layer_pre_trough_recovery_is_disclosed_not_hidden() -> None:
    disclosed = dict(_summary()["amber_conditions"])[
        "pre_trough_recovery_in_the_raw_or_filtered_layer"
    ]
    assert disclosed["reported_only"] is True
    assert dict(disclosed["value"])["gfc_2009"]["raw"] == "2009-05-22"


def test_usrec_is_only_a_secondary_comparison() -> None:
    secondary = dict(dict(_summary()["convention"])["usrec_secondary_comparison"])
    for entry in secondary.values():
        assert dict(entry)["role"] == "secondary_comparison_only"


# ── §6 달력 지연과 구간대 ────────────────────────────────────────────────────


def test_post_trough_latency_is_measured_from_the_end_of_the_month() -> None:
    month = turning.turning_month("recession_2020")
    assert str(month.end.date()) == "2020-04-30"
    assert month.calendar_latency_weeks("2020-07-17") == 11
    # 월 안이나 그 앞은 음수가 아니라 0이다. 없는 정확도를 주장하지 않는다.
    assert month.calendar_latency_weeks("2020-04-10") == 0
    assert month.calendar_latency_weeks("2020-03-01") == 0


def test_the_green_amber_red_boundaries_are_exact() -> None:
    assert turning.band(0) == "green"
    assert turning.band(8) == "green"
    assert turning.band(9) == "amber"
    assert turning.band(13) == "amber"
    assert turning.band(14) == "red"
    assert turning.band(None) is None
    assert turning.GREEN_MAXIMUM_WEEKS == 8
    assert turning.AMBER_MAXIMUM_WEEKS == 13


def test_the_bands_were_declared_before_the_audit() -> None:
    convention = dict(_summary()["convention"])
    assert convention["bands_declared_before_the_audit"] is True
    assert convention["no_day_inside_the_trough_month_was_selected"] is True
    assert convention["primary"] == "interval_censored_monthly_turning_point"


# ── §5 지연 층 분리 ──────────────────────────────────────────────────────────


def test_every_delay_layer_stays_separate() -> None:
    """층 이름을 여기서 다시 적지 않는다. 구간 정의에서 끌어와야 갈라지지 않는다."""

    layers = dict(dict(_summary()["delay_decomposition"])["layers"])
    expected = [f"{name}_weeks" for name, _, _ in latency.SEGMENTS]
    expected.append("freshness_or_withholding_delay_weeks")
    for name in expected:
        assert name in layers, name
    assert set(layers) == set(expected)
    # 발표·변환·원시점수·확인·필터가 각각 별개의 키다. 합쳐 적지 않는다.
    assert len(expected) == 7
    for name in (
        "publication_delay_weeks",
        "domain_observation_availability_delay_weeks",
        "transformation_delay_weeks",
        "raw_phase_score_delay_weeks",
        "transition_filter_delay_weeks",
        "confirmation_delay_weeks",
    ):
        assert name in layers, name


def test_the_sequential_layers_sum_to_the_calendar_latency() -> None:
    decomposition = dict(_summary()["delay_decomposition"])
    assert (
        decomposition["sequential_layer_sum_weeks"]
        == decomposition["calendar_recovery_latency_weeks"]
    )


def test_adjusted_latency_never_replaces_calendar_latency() -> None:
    decomposition = dict(_summary()["delay_decomposition"])
    assert decomposition["adjusted_latency_does_not_replace_calendar_latency"] is True
    assert decomposition["calendar_recovery_latency_weeks"] is not None
    assert decomposition["evidence_availability_adjusted_latency_weeks"] is not None
    assert (
        decomposition["evidence_availability_adjusted_latency_weeks"]
        <= decomposition["calendar_recovery_latency_weeks"]
    )
    assert decomposition["calendar_band"] == turning.band(
        decomposition["calendar_recovery_latency_weeks"]
    )


def test_the_limitation_label_comes_from_the_fixed_vocabulary() -> None:
    decomposition = dict(_summary()["delay_decomposition"])
    assert decomposition["limitation_label"] in latency.LIMITATION_LABELS


def test_a_state_machine_delay_is_named_a_state_machine_delay() -> None:
    """자료는 진작 회복을 지지했는데 상태 기계가 붙잡고 있던 경우."""

    weeks = pd.date_range("2020-05-08", "2020-07-17", freq="W-FRI")
    frame = pd.DataFrame(
        {
            "activity_momentum": [1.0] * len(weeks),
            "activity_level": [-1.0] * len(weeks),
            "positive_momentum_domains": [4] * len(weeks),
            "phase_status": ["official"] * len(weeks),
            "raw_phase": ["recovery"] * len(weeks),
            "filtered_winner": ["recovery"] * len(weeks),
            "official_phase": ["contraction"] * (len(weeks) - 1) + ["recovery"],
            "production__momentum": [9.0] * len(weeks),
            "production__observation_through": ["2020-05-01"] * len(weeks),
        },
        index=pd.Index([str(value.date()) for value in weeks]),
    )
    dates: dict[str, str | None] = {
        "calendar_trough_interval_end": "2020-04-30",
        "first_post_trough_data_available": "2020-05-08",
        "recovery_observable_date": "2020-05-08",
        "first_raw_recovery": "2020-05-08",
        "recovery_recognizable_date": "2020-05-08",
        "first_filtered_recovery": "2020-05-08",
        "first_official_recovery": "2020-07-17",
    }
    result = latency.decompose(frame, dates, turning.turning_month("recession_2020"), 3, 3.7547, 8)
    assert result["invariants"]["holds"] is True
    assert result["state_machine_delay_weeks"] == 10
    assert result["limitation_label"] == "state-machine delay"
    # 상태 기계 지연은 확인 구간에 온전히 귀속된다. 다른 구간으로 흩어지지 않는다.
    segments = {row["segment"]: row["duration_weeks"] for row in result["segments"]}
    assert segments["confirmation_delay"] == 10
    assert segments["publication_delay"] == 1
    assert segments["transformation_delay"] == 0


def test_the_evidence_anchor_uses_the_frozen_breadth_concept() -> None:
    config = load_config(load_settings())
    assert config.thresholds.minimum_coincident_domains == 2
    dates = dict(_summary()["recovery_availability_dates"])
    assert dates["recovery_observable_date"] == "2020-06-19"
    assert dates["first_post_trough_data_available"] == "2020-06-05"


def test_the_domain_timeline_separates_coincident_domains_from_the_bridge() -> None:
    rows = list(_summary()["domain_recovery_timeline"])
    roles = {row["domain"]: row["role"] for row in rows}
    assert roles["labor_stress"] == "bridge_only"
    for domain in ("production", "employment", "real_income", "consumption"):
        assert roles[domain] == "coincident"


def test_the_recomputed_real_time_window_reproduces_the_recorded_path() -> None:
    reproduction = dict(_summary()["reproduction"])
    assert reproduction["reproduces_the_recorded_real_time_path"] is True
    assert all(
        value == 0
        for value in dict(reproduction["recomputed_versus_recorded_disagreements"]).values()
    )


# ── §8 사전 통과 게이트 재확인 ───────────────────────────────────────────────


def test_every_previously_passed_gate_is_rechecked_and_still_passes() -> None:
    recheck = dict(_summary()["rechecked_gates"])
    assert recheck["missing_gates"] == []
    assert recheck["regressed_gates"] == []
    assert recheck["all_previously_passed_gates_still_pass"] is True
    assert len(recheck["checked"]) == len(gates.PREVIOUSLY_PASSED)


def test_the_cache_only_guarantees_hold() -> None:
    cache = dict(_summary()["cache"])
    for flag in (
        "network_used",
        "api_key_used",
        "latest_vintage_substitution",
        "backward_fill_used",
    ):
        assert cache[flag] is False, flag
    assert cache["expected_as_of_dates"] == 688


# ── §9 결정 트리 ─────────────────────────────────────────────────────────────


def _amber(passing: bool = True) -> dict[str, dict[str, Any]]:
    names = (
        "delay_not_extended_by_filter_or_confirmation",
        "official_follows_raw_within_confirmation_allowance",
        "adjusted_latency_within_allowance",
        "no_contraction_recovery_contraction_round_trip_within_13_weeks",
        "no_genuine_pre_trough_four_week_recovery",
        "previously_passed_gates_still_pass",
        "one_unambiguous_official_phase",
    )
    return {name: {"value": 0, "passes": passing} for name in names}


def test_exactly_one_classification_is_emitted() -> None:
    decision = _decision()
    assert decision["classification"] in decide.CLASSIFICATIONS
    assert isinstance(decision["classification"], str)
    assert decision["allowed_classifications"] == list(decide.CLASSIFICATIONS)


def test_final_validated_is_not_an_available_classification() -> None:
    assert decide.FORBIDDEN_CLASSIFICATION not in decide.CLASSIFICATIONS
    assert _decision()["forbidden_classification"] == "final_validated"
    assert _decision()["is_final_validation"] is False


def test_provisional_adoption_is_never_called_final_validation() -> None:
    decision = _decision()
    if decision["classification"] != "provisional_operational_adoption":
        pytest.fail("이 시험은 잠정 채택 상태에서만 의미가 있다")
    manifest_path = _output() / "operational_manifest.json"
    record = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert record["model_status"] == "provisional"
    assert record["is_final_validation"] is False
    assert record["is_fully_validated"] is False
    assert record["strict_real_time_recession_episodes"] == 1
    assert record["historical_v1_1_status_under_the_latest_vintage_protocol"] == "rejected"
    assert record["new_model_logic_created"] is False
    assert record["parameters_changed"] is False


def test_a_red_calendar_band_forces_rejection() -> None:
    result = decide.classify(
        reclassification_2009="within_turning_month",
        genuine_pre_trough_episodes=[],
        calendar_band="red",
        amber=_amber(),
        all_previous_gates_pass=True,
        model_or_parameter_changed=False,
        measurable=True,
    )
    assert result["classification"] == "operational_rejection_confirmed"


def test_a_regressed_gate_forces_rejection() -> None:
    result = decide.classify(
        reclassification_2009="within_turning_month",
        genuine_pre_trough_episodes=[],
        calendar_band="amber",
        amber=_amber(),
        all_previous_gates_pass=False,
        model_or_parameter_changed=False,
        measurable=True,
    )
    assert result["classification"] == "operational_rejection_confirmed"
    assert "사전 통과 게이트가 퇴행했다" in result["reason"]


def test_a_genuine_pre_trough_recovery_forces_rejection() -> None:
    result = decide.classify(
        reclassification_2009="pre_trough_recovery",
        genuine_pre_trough_episodes=["gfc_2009"],
        calendar_band="green",
        amber=_amber(),
        all_previous_gates_pass=True,
        model_or_parameter_changed=False,
        measurable=True,
    )
    assert result["classification"] == "operational_rejection_confirmed"


def test_amber_adoption_requires_every_condition() -> None:
    baseline = decide.classify(
        reclassification_2009="within_turning_month",
        genuine_pre_trough_episodes=[],
        calendar_band="amber",
        amber=_amber(),
        all_previous_gates_pass=True,
        model_or_parameter_changed=False,
        measurable=True,
    )
    assert baseline["classification"] == "provisional_operational_adoption"
    for name in _amber():
        broken = _amber()
        broken[name] = {"value": 99, "passes": False}
        result = decide.classify(
            reclassification_2009="within_turning_month",
            genuine_pre_trough_episodes=[],
            calendar_band="amber",
            amber=broken,
            all_previous_gates_pass=True,
            model_or_parameter_changed=False,
            measurable=True,
        )
        assert result["classification"] == "operational_rejection_confirmed", name


def test_a_model_change_blocks_adoption() -> None:
    result = decide.classify(
        reclassification_2009="within_turning_month",
        genuine_pre_trough_episodes=[],
        calendar_band="amber",
        amber=_amber(),
        all_previous_gates_pass=True,
        model_or_parameter_changed=True,
        measurable=True,
    )
    assert result["classification"] != "provisional_operational_adoption"


def test_an_unmeasurable_episode_is_a_measurement_definition_failure() -> None:
    result = decide.classify(
        reclassification_2009="within_turning_month",
        genuine_pre_trough_episodes=[],
        calendar_band="amber",
        amber=_amber(),
        all_previous_gates_pass=True,
        model_or_parameter_changed=False,
        measurable=False,
    )
    assert result["classification"] == "measurement_definition_failure"


# ── §10 현재상태 계약 ────────────────────────────────────────────────────────


def _state() -> dict[str, Any]:
    path = _output() / "current_state_output.json"
    assert path.exists(), "잠정 채택인데 현재상태 산출물이 없다"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_the_current_output_names_exactly_one_official_phase() -> None:
    state = _state()
    manifest.validate_contract(state)
    assert state["official_current_phase"] in ("recovery", "expansion", "slowdown", "contraction")
    body = (_output() / "current_state_report.md").read_text(encoding="utf-8")
    first = body.splitlines()[0]
    assert first.startswith("Current official U.S. phase: ")
    assert first.split(": ", 1)[1] == state["official_current_phase"]


def test_an_ambiguous_phase_label_is_rejected() -> None:
    for label in manifest.AMBIGUOUS_LABELS:
        bad = {**_state(), "official_current_phase": label}
        with pytest.raises(ValueError):
            manifest.validate_contract(bad)


def test_a_withheld_week_cannot_emit_an_official_phase() -> None:
    state = _state()
    manifest.validate_contract({**state, "phase_status": "withheld", "official_current_phase": ""})
    with pytest.raises(ValueError):
        manifest.validate_contract(
            {**state, "phase_status": "withheld", "official_current_phase": "expansion"}
        )


def test_every_required_contract_field_is_present() -> None:
    state = _state()
    for name in manifest.REQUIRED_FIELDS:
        assert name in state, name
    with pytest.raises(ValueError):
        manifest.validate_contract({k: v for k, v in state.items() if k != "recession_alert"})


def test_the_recovery_latency_warning_is_carried_into_the_output() -> None:
    warning = dict(_state()["recovery_latency_warning"])
    decomposition = dict(_summary()["delay_decomposition"])
    assert warning["band"] == decomposition["calendar_band"]
    assert (
        warning["calendar_recovery_latency_weeks"]
        == decomposition["calendar_recovery_latency_weeks"]
    )
    assert _state()["model_status"] == "provisional"


def test_investment_related_fields_and_language_are_rejected_recursively() -> None:
    state = _state()
    manifest.validate_contract(state)
    for token in ("portfolio", "allocation", "valuation", "price_target", "buy", "sell"):
        nested = {**state, "breadth": {**state["breadth"], token: 1}}
        with pytest.raises(ValueError):
            manifest.validate_contract(nested)
        worded = {**state, "known_limitations": [*state["known_limitations"], f"a {token} idea"]}
        with pytest.raises(ValueError):
            manifest.validate_contract(worded)


# ── §11 13주 모니터링 ────────────────────────────────────────────────────────


def test_the_monitoring_spec_states_what_it_cannot_validate() -> None:
    body = (_output() / "live_monitoring_spec.md").read_text(encoding="utf-8")
    assert "침체 탐지 정확도" in body
    assert "final_validated" in body
    for condition in monitoring.SUSPENSION_CONDITIONS:
        assert condition in body
    for condition in monitoring.ALERT_CONDITIONS:
        assert condition in body


def test_a_monitoring_snapshot_is_immutable_and_carries_the_hashes() -> None:
    provenance = preserve.verify(load_settings())
    snapshot = monitoring.snapshot(_state(), provenance["hashes"])
    assert snapshot["immutable"] is True
    assert set(snapshot["hashes"]) == set(monitoring.PRESERVED_HASHES)
    assert len(snapshot["payload_sha256"]) == 64
    again = monitoring.snapshot(_state(), provenance["hashes"])
    assert again["payload_sha256"] == snapshot["payload_sha256"]


def test_phase_churn_reporting_counts_transitions_in_a_rolling_window() -> None:
    quiet = [{"official_current_phase": "expansion"} for _ in range(6)]
    assert monitoring.churn(quiet)["alerts"] is False
    noisy = [
        {"official_current_phase": phase}
        for phase in ("expansion", "slowdown", "expansion", "slowdown")
    ]
    assert monitoring.churn(noisy)["alerts"] is True


def test_the_monitoring_archive_path_is_one_file_per_as_of_week() -> None:
    first = monitoring.archive_path(_root(), "2026-08-14")
    second = monitoring.archive_path(_root(), "2026-08-21")
    assert first != second
    assert first.name == "2026-08-14.json"


# ── 재현성 ───────────────────────────────────────────────────────────────────


def test_the_recorded_digest_matches_a_recomputed_one() -> None:
    payload = _summary()
    assert payload["run_digest"] == review.digest(payload)
    assert _decision()["run_digest"] == payload["run_digest"]


def test_two_clean_processes_produce_identical_results() -> None:
    root = _root()
    python = root / ".venv" / "Scripts" / "python.exe"
    assert python.exists(), "프로젝트 가상환경을 찾을 수 없다"
    before = _decision()["run_digest"]
    subprocess.run(
        [str(python), "-m", "business_cycle.recovery_semantics"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=3600,
    )
    assert _decision()["run_digest"] == before


def test_no_provisional_artifact_exists_unless_the_stage_adopted() -> None:
    adopted = _decision()["classification"] == "provisional_operational_adoption"
    for name in (
        "operational_manifest.json",
        "current_state_output.json",
        "live_monitoring_spec.md",
    ):
        assert (_output() / name).exists() is adopted, name


def test_the_timeline_reads_the_frozen_model_without_rewriting_it() -> None:
    source = (_root() / "src" / "business_cycle" / "recovery_semantics" / "timeline.py").read_text(
        encoding="utf-8"
    )
    assert "from ..four_phase.engine import" in source
    assert "def contraction_evidence" not in source
    assert "def recovery_evidence" not in source
    assert set(timeline.LAYERS)


# ── §2 2001년 red의 범위 ─────────────────────────────────────────────────────


def test_the_red_gate_scope_is_explicit_and_predeclared() -> None:
    audit = dict(_summary()["red_scope_audit"])
    assert audit["scope"] == consistency.RED_GATE_SCOPE
    assert audit["gate_episode"] == consistency.RED_GATE_EPISODE == "recession_2020"
    assert audit["declared_before_results"] is True
    assert audit["applies_to_every_evaluable_episode"] is False
    # 구간대 어휘 자체는 모든 에피소드에 적용된다. 좁힌 것은 게이트지 어휘가 아니다.
    assert audit["band_vocabulary_applies_to_every_episode"] is True


def test_the_2001_red_result_is_reported_not_exempted() -> None:
    audit = dict(_summary()["red_scope_audit"])
    assert "recession_2001" in audit["episodes_in_the_red_band"]
    assert audit["episodes_in_the_red_band_are_gated"] is False
    assert audit["not_exempted_after_seeing_the_result"] is True
    assert audit["reported_as_major_limitation"] is True
    body = (_output() / "recovery_semantics_report.md").read_text(encoding="utf-8")
    # 첫 장에 있어야 한다. 뒤에 묻어 두면 공시가 아니다.
    assert "2001년 침체는 같은 구간대에서 `red`다" in body.split("## §2.")[0]


def test_the_counterfactual_of_a_universal_red_gate_is_recorded() -> None:
    audit = dict(_summary()["red_scope_audit"])
    assert (
        audit["counterfactual_if_the_red_gate_applied_to_every_episode"]
        == "operational_rejection_confirmed"
    )
    assert audit["counterfactual_changes_the_decision"] is True


def test_the_2001_thirty_one_weeks_is_not_continuous_contraction() -> None:
    gap = dict(_summary()["post_trough_gap"])
    assert gap["calendar_recovery_latency_weeks"] == 31
    assert gap["calendar_band"] == "red"
    assert gap["gap_kind"] == "recovery_label_skipped_on_the_way_out"
    assert gap["gap_kind"] in consistency.GAP_KINDS
    # 침체는 7주뿐이고 나머지는 후퇴기·확장기였다.
    assert gap["official_contraction_weeks_in_the_gap"] < gap["gap_weeks_after_the_trough_month"]
    assert gap["contraction_exit_latency_weeks"] == 8
    assert gap["contraction_exit_band"] == "green"
    weeks = dict(gap["weeks_by_official_phase"])
    assert sum(weeks.values()) == gap["gap_weeks_after_the_trough_month"]


def test_the_post_trough_weekly_path_carries_every_requested_column() -> None:
    path = list(_summary()["post_trough_phase_path"])
    assert len(path) == dict(_summary()["post_trough_gap"])["gap_weeks_after_the_trough_month"]
    row = dict(path[0])
    for name in (
        "raw_phase",
        "official_phase",
        "confirmation_pending",
        "transition_watch",
        "phase_separation",
        "evidence_quality_high",
        "confirming_domains",
        "positive_momentum_domains",
        "filtered_recovery",
        "raw_recovery",
    ):
        assert name in row, name
    assert any(name.endswith("__weeks_since_release") for name in row)


def test_a_continuous_contraction_gap_is_labelled_differently() -> None:
    index = pd.date_range("2001-12-07", periods=6, freq="W-FRI")
    frame = pd.DataFrame(
        {"official_phase": ["contraction"] * 6, "raw_phase": ["contraction"] * 6},
        index=pd.Index([str(value.date()) for value in index]),
    )
    _, summary = consistency.post_trough_phase_path(
        frame, "recession_2001", turning.turning_month("recession_2001")
    )
    assert summary["gap_kind"] == "continuous_post_trough_contraction"


# ── §3 층별 회복 열의 위치 ───────────────────────────────────────────────────


def test_the_four_sequence_positions_are_decided_by_start_and_end() -> None:
    gfc = turning.turning_month("gfc_2009")
    assert (
        consistency.sequence_position("2009-05-01", "2009-05-22", gfc)
        == "entirely_pre_trough_month"
    )
    assert (
        consistency.sequence_position("2009-05-22", "2009-06-12", gfc)
        == "begins_pre_trough_and_overlaps_turning_month"
    )
    assert consistency.sequence_position("2009-06-05", "2009-06-26", gfc) == "within_turning_month"
    assert consistency.sequence_position("2009-07-03", "2009-07-24", gfc) == "post_trough_month"
    assert set(consistency.SEQUENCE_POSITIONS) == {
        "entirely_pre_trough_month",
        "begins_pre_trough_and_overlaps_turning_month",
        "within_turning_month",
        "post_trough_month",
    }


def test_each_layer_reports_its_own_recovery_timeline() -> None:
    rows = {row["layer"]: row for row in _summary()["layer_recovery_timelines"]}
    assert set(rows) == set(consistency.LAYERS)
    raw = rows["raw_phase"]
    assert raw["first_four_week_sequence_start"] == "2009-05-22"
    assert raw["first_four_week_sequence_end"] == "2009-06-12"
    assert raw["weeks_of_the_sequence_before_the_trough_month"] == 2
    assert raw["weeks_of_the_sequence_inside_the_turning_month"] == 2
    assert raw["sequence_position"] == "begins_pre_trough_and_overlaps_turning_month"
    assert raw["entered_recovery_before_the_trough_month"] is True
    assert raw["entire_confirmed_sequence_before_the_trough_month"] is False
    official = rows["official_phase"]
    assert official["sequence_position"] == "within_turning_month"
    assert official["entered_recovery_before_the_trough_month"] is False
    for row in rows.values():
        for ahead in (4, 8, 13):
            assert row[f"return_to_contraction_within_{ahead}_weeks"] == 0


def test_the_early_raw_sequence_is_disclosed_as_a_diagnostic_not_dismissed() -> None:
    rows = {row["layer"]: row for row in _summary()["layer_recovery_timelines"]}
    assert rows["raw_phase"]["role"] == "stability_diagnostic_disclosed_not_gated"
    assert rows["filtered_winner"]["role"] == "stability_diagnostic_disclosed_not_gated"
    assert rows["official_phase"]["role"] == "adoption_gate"
    body = (_output() / "recovery_semantics_report.md").read_text(encoding="utf-8")
    assert "2009-05-22" in body


def test_the_adoption_gate_still_reads_the_official_layer_only() -> None:
    # §9-B는 "genuine pre-trough **confirmed** recovery"였다. 확인된 국면은 공식 국면이다.
    # 층을 바꾸지 않았음을 보인다.
    disclosed = dict(_summary()["amber_conditions"])["no_genuine_pre_trough_four_week_recovery"]
    assert disclosed["value"] == []
    assert disclosed["passes"] is True
    scans = {scan["episode"]: scan for scan in _summary()["pre_trough_scans"]}
    gfc = scans["gfc_2009"]
    assert gfc["official_phase__four_week_sequence_position"] == "within_turning_month"
    assert (
        gfc["raw_phase__four_week_sequence_position"]
        == "begins_pre_trough_and_overlaps_turning_month"
    )
    assert gfc["raw_phase__genuine_pre_trough_four_week_recovery"] is None


def test_a_sequence_entirely_before_the_month_still_fails_the_gate() -> None:
    index = pd.date_range("2009-04-24", periods=5, freq="W-FRI")
    frame = pd.DataFrame(
        {"official_phase": ["recovery"] * 5, "raw_phase": ["recovery"] * 5},
        index=pd.Index([str(value.date()) for value in index]),
    )
    scan = gates.pre_trough_recovery_scan(frame, "gfc_2009", turning.turning_month("gfc_2009"))
    assert scan["official_phase__four_week_sequence_position"] == "entirely_pre_trough_month"
    assert scan["official_phase__genuine_pre_trough_four_week_recovery"] == "2009-04-24"


# ── §4 구간 분해의 불변식 ────────────────────────────────────────────────────


def test_the_segments_are_sequential_and_non_overlapping() -> None:
    decomposition = dict(_summary()["delay_decomposition"])
    invariants = dict(decomposition["invariants"])
    assert invariants["boundaries_are_monotonic"] is True
    assert invariants["segments_are_contiguous_with_no_gaps"] is True
    assert invariants["overlapping_segments"] == []
    assert invariants["week_sum_equals_calendar_latency"] is True
    assert invariants["day_sum_equals_calendar_latency"] is True
    assert invariants["holds"] is True
    rows = list(decomposition["segments"])
    assert [row["segment"] for row in rows] == [name for name, _, _ in latency.SEGMENTS]
    for before, after in zip(rows[:-1], rows[1:], strict=True):
        assert before["end_date"] == after["start_date"]


def test_every_segment_records_its_boundary_conditions() -> None:
    for row in _summary()["delay_decomposition"]["segments"]:
        assert row["start_date"] and row["end_date"]
        assert row["entry_condition"] and row["exit_condition"]
        assert row["duration_weeks"] is not None and row["duration_days"] is not None
        assert isinstance(row["evidence_at_the_exit_boundary"], dict)
        assert row["evidence_at_the_exit_boundary"]["domain_observation_through"]


def test_a_broken_decomposition_raises_instead_of_reporting() -> None:
    rows = [
        {
            "segment": "a",
            "start_date": "2020-04-30",
            "end_date": "2020-06-05",
            "duration_weeks": 5,
            "duration_days": 36,
        },
        # 빈틈: 앞 구간의 끝과 뒤 구간의 시작이 다르다.
        {
            "segment": "b",
            "start_date": "2020-06-19",
            "end_date": "2020-07-17",
            "duration_weeks": 4,
            "duration_days": 28,
        },
    ]
    with pytest.raises(latency.DecompositionInvariantViolated):
        latency.check_invariants(rows, 11, 78)


def test_publication_is_not_credited_with_the_whole_delay() -> None:
    rows = {row["segment"]: row for row in _summary()["delay_decomposition"]["segments"]}
    total = dict(_summary()["delay_decomposition"])["calendar_recovery_latency_weeks"]
    assert rows["publication_delay"]["duration_weeks"] == 5
    assert rows["publication_delay"]["duration_weeks"] < total


def test_the_transformation_delay_is_attributed_by_measurement() -> None:
    concurrency = dict(_summary()["delay_decomposition"]["concurrency"])
    assert concurrency["attributed_to"] == "bounded_equal_weight_domain_aggregation"
    assert concurrency["weeks_examined"] > 0
    assert (
        concurrency["weeks_with_every_domain_at_the_momentum_cap"]
        == (concurrency["weeks_examined"])
    )
    assert (
        concurrency["weeks_where_the_aggregate_equals_the_capped_sign_vote"]
        == (concurrency["weeks_examined"])
    )
    assert concurrency["not_added_arithmetically"] is True
    causes = [item["cause"] for item in concurrency["concurrent_contributors"]]
    assert "further_publication_beyond_the_first_post_trough_month" in causes
    assert any(name.startswith("one_sided_momentum_window") for name in causes)


# ── §5 의미 지문 ─────────────────────────────────────────────────────────────


def test_the_semantic_digest_matches_a_recomputed_one() -> None:
    payload = _summary()
    assert payload["semantic_digest"] == canonical.semantic_digest(payload)
    assert _decision()["semantic_digest"] == payload["semantic_digest"]
    assert set(payload["semantic_digest_excludes"]) == set(canonical.VOLATILE_FIELDS)
    assert set(payload["semantic_digest_covers"]) == set(canonical.COVERED)


def test_only_a_timestamp_change_preserves_the_semantic_digest() -> None:
    payload = _summary()
    before = canonical.semantic_digest(payload)
    payload["provenance"]["executed_at_utc"] = "1999-01-01T00:00:00+00:00"
    payload["provenance"]["head_commit"] = "0" * 40
    assert canonical.semantic_digest(payload) == before


MUTATIONS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    (
        "classification",
        lambda p: p["decision"].__setitem__("classification", "operational_rejection_confirmed"),
    ),
    ("decision_reason", lambda p: p["decision"].__setitem__("reason", "다른 사유")),
    (
        "gate_result",
        lambda p: p["rechecked_gates"]["groups"]["contraction_entry"][
            "first_official_contraction_within_10_weeks"
        ].__setitem__("passes", False),
    ),
    (
        "amber_condition",
        lambda p: p["amber_conditions"]["adjusted_latency_within_allowance"].__setitem__(
            "passes", False
        ),
    ),
    (
        "protected_hash",
        lambda p: p["provenance"]["hashes"].__setitem__("v1_1_config", "0" * 64),
    ),
    (
        "latency_value",
        lambda p: p["delay_decomposition"].__setitem__("calendar_recovery_latency_weeks", 99),
    ),
    (
        "latency_layer",
        lambda p: p["delay_decomposition"]["layers"].__setitem__("transformation_delay_weeks", 99),
    ),
    (
        "episode_classification",
        lambda p: p["turning_month_audit"][0].__setitem__("calendar_band", "green"),
    ),
    (
        "episode_2009",
        lambda p: p["episode_2009"].__setitem__("reclassification", "pre_trough_recovery"),
    ),
    (
        "current_phase",
        lambda p: p["current_state"].__setitem__("official_current_phase", "slowdown"),
    ),
    ("model_status", lambda p: p.__setitem__("model_status", "final")),
    ("sample_role", lambda p: p["sample_roles"].__setitem__("recession_2020", "holdout")),
]


@pytest.mark.parametrize("name,mutate", MUTATIONS, ids=[name for name, _ in MUTATIONS])
def test_any_substantive_change_breaks_the_semantic_digest(
    name: str, mutate: Callable[[dict[str, Any]], None]
) -> None:
    payload = _summary()
    before = canonical.semantic_digest(payload)
    mutate(payload)
    assert canonical.semantic_digest(payload) != before, name


def test_raw_artifacts_are_preserved_alongside_the_canonical_comparison() -> None:
    payload = _summary()
    # 정규화는 비교에만 쓴다. 원본 기록은 그대로 남아 있어야 한다.
    assert payload["provenance"]["executed_at_utc"]
    assert payload["provenance"]["head_commit"]
    assert payload["delay_decomposition"]["segments"]
    assert payload["post_trough_phase_path"]
