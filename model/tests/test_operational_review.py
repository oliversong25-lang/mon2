"""운영 수용 심사: 보존, 결정 트리, 계약, 재현성.

이 단계는 동결 v1.1을 **읽기만** 한다. 테스트도 그 사실을 주장으로 두지 않고 확인한다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from business_cycle.config import load_settings
from business_cycle.four_phase import contract as C
from business_cycle.four_phase.engine import load_config
from business_cycle.operational_review import preserve, recovery, revision
from business_cycle.operational_review import review as R


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _output() -> Path:
    return _root() / "outputs" / "operational_review"


def _decision() -> dict[str, Any]:
    path = _output() / "operational_decision.json"
    if not path.exists():
        pytest.skip("운영 심사 산출물이 아직 생성되지 않았다")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _summary() -> dict[str, Any]:
    path = _output() / "validation_summary.json"
    if not path.exists():
        pytest.skip("운영 심사 산출물이 아직 생성되지 않았다")
    return dict(json.loads(path.read_text(encoding="utf-8")))


# ── 보존 ─────────────────────────────────────────────────────────────────────


def test_every_protected_hash_is_checked_before_evaluation() -> None:
    measured = preserve.measure(load_settings())
    assert set(measured) == set(preserve.PROTECTED)
    for name, expected in preserve.PROTECTED.items():
        assert measured[name] == expected, name
    assert preserve.verify(load_settings())["verified"] is True


def test_a_changed_protected_hash_stops_the_stage() -> None:
    original = dict(preserve.PROTECTED)
    try:
        preserve.PROTECTED["v1_1_config"] = "0" * 64
        with pytest.raises(preserve.ProtectedArtifactChanged):
            preserve.verify(load_settings())
    finally:
        preserve.PROTECTED.clear()
        preserve.PROTECTED.update(original)


def test_no_protected_file_was_modified_by_this_stage() -> None:
    assert preserve.git_status_of_protected_paths(load_settings()) == []


def test_the_frozen_configuration_is_referenced_not_copied() -> None:
    """§10. 채택되더라도 설정을 복사하거나 바꾸지 않고 참조한다."""

    config = load_config(load_settings())
    assert config.sha256 == preserve.PROTECTED["v1_1_config"]
    copies = list((_output()).glob("*.yaml")) if _output().exists() else []
    assert copies == []


def test_the_stage_records_that_it_ran_no_parameter_search() -> None:
    provenance = preserve.verify(load_settings())
    assert provenance["parameter_search_run"] is False
    assert provenance["stage_is_read_only"] is True
    assert provenance["v1_1_adoption_status"] == "rejected"


def test_the_review_package_contains_no_parameter_grid() -> None:
    """탐색 코드가 이 단계에 들어오지 않았는지 소스에서 확인한다."""

    package = _root() / "src" / "business_cycle" / "operational_review"
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in ("THRESHOLD_GRID", "FILTER_GRID", "itertools.product", "is_feasible("):
            assert token not in text, f"{path.name}: {token}"


# ── 실시간 무결성 ────────────────────────────────────────────────────────────


def test_no_network_or_api_key_is_used() -> None:
    cache = dict(_summary()["cache"])
    assert cache["network_used"] is False
    assert cache["api_key_used"] is False
    assert cache["latest_vintage_substitution"] is False
    assert cache["backward_fill_used"] is False


def test_688_as_of_weeks_are_reproduced() -> None:
    gates = dict(_summary()["gates"])
    integrity = dict(gates["operational_integrity"])
    assert dict(integrity["exactly_688_as_of_weeks"])["value"] == 688
    assert dict(integrity["exactly_688_as_of_weeks"])["passes"] is True


def test_future_leakage_stays_at_zero() -> None:
    integrity = dict(dict(_summary()["gates"])["operational_integrity"])
    assert dict(integrity["zero_future_information_violations"])["value"] == 0


def test_a_withheld_week_never_emits_an_official_phase() -> None:
    integrity = dict(dict(_summary()["gates"])["operational_integrity"])
    assert dict(integrity["no_official_phase_on_a_withheld_week"])["value"] == 0
    assert dict(integrity["raw_measurements_preserved_on_withheld_weeks"])["passes"] is True


# ── 회복 시점 계산 ───────────────────────────────────────────────────────────


def _synthetic(phases: list[str], start: str = "2009-01-02") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(phases), freq="W-FRI")
    return pd.DataFrame(
        {
            "raw_phase": phases,
            "filtered_winner": phases,
            "official_phase": phases,
        },
        index=index,
    )


def test_recovery_dates_are_computed_from_the_trough() -> None:
    phases = ["contraction"] * 6 + ["recovery"] * 6
    frame = _synthetic(phases)
    trough = frame.index[6]
    audit = recovery.episode_audit(frame, trough, "synthetic", "test", peak=frame.index[0])
    assert audit["official_first_recovery_lag_weeks"] == 0
    assert audit["post_trough_contraction_weeks"] == 0
    phases_late = ["contraction"] * 9 + ["recovery"] * 3
    late = recovery.episode_audit(
        _synthetic(phases_late), trough, "synthetic_late", "test", peak=frame.index[0]
    )
    assert late["official_first_recovery_lag_weeks"] == 3
    assert late["post_trough_contraction_weeks"] == 3
    assert late["longest_post_trough_contraction_run"] == 3


def test_the_post_trough_window_is_bounded_so_a_later_recession_is_not_counted() -> None:
    """경계가 없으면 2001년 저점의 성적에 금융위기가 딸려 들어온다. 실제로 그랬다."""

    phases = ["contraction"] * 4 + ["expansion"] * 60 + ["contraction"] * 10
    frame = _synthetic(phases)
    trough = frame.index[4]
    audit = recovery.episode_audit(frame, trough, "synthetic", "test", peak=frame.index[0])
    assert audit["post_trough_horizon_weeks"] == recovery.POST_TROUGH_HORIZON_WEEKS
    assert audit["post_trough_contraction_weeks"] == 0


def test_premature_recovery_is_measured_only_inside_the_recession() -> None:
    """앞선 침체 뒤의 정당한 회복이 이 에피소드의 조기 이탈로 잡히면 안 된다."""

    phases = ["recovery"] * 6 + ["expansion"] * 4 + ["contraction"] * 6 + ["recovery"] * 4
    frame = _synthetic(phases)
    peak, trough = frame.index[10], frame.index[15]
    audit = recovery.episode_audit(frame, trough, "synthetic", "test", peak=peak)
    assert audit["premature_four_week_recovery_inside_the_recession"] is None


def test_confirmation_delay_is_reported_apart_from_raw_evidence_delay() -> None:
    episode = {
        "episode": "synthetic",
        "raw_first_recovery_lag_weeks": 5,
        "filtered_winner_first_recovery_lag_weeks": 7,
        "official_first_recovery_lag_weeks": 9,
    }
    decomposed = recovery.decompose_delay(episode)
    assert decomposed["raw_evidence_lag_weeks"] == 5
    assert decomposed["filter_lag_weeks"] == 2
    assert decomposed["confirmation_lag_weeks"] == 2
    assert decomposed["total_official_lag_weeks"] == 9


def test_the_development_benchmark_excludes_2020() -> None:
    assert "recession_2020" not in recovery.DEVELOPMENT_EPISODES
    benchmark = dict(_summary()["benchmark"])
    assert "recession_2020" not in list(benchmark["development_episodes"])
    assert benchmark["development_sample_size"] == 2


def test_the_small_development_sample_is_reported_prominently() -> None:
    benchmark = dict(_summary()["benchmark"])
    assert benchmark["development_sample_size"] == len(recovery.DEVELOPMENT_EPISODES)
    report = (_output() / "operational_review_report.md").read_text(encoding="utf-8")
    assert "2개뿐" in report


# ── 결정 트리 ────────────────────────────────────────────────────────────────


def test_final_validated_can_never_be_emitted() -> None:
    assert R.FORBIDDEN_CLASSIFICATION not in R.CLASSIFICATIONS
    assert _decision()["classification"] in R.CLASSIFICATIONS
    text = (_output() / "operational_decision.json").read_text(encoding="utf-8")
    assert '"classification": "final_validated"' not in text


def test_provisional_adoption_cannot_occur_when_a_gate_fails() -> None:
    gates = {"g": {"a": {"passes": True}, "b": {"passes": False}}}
    decision = R.classify(gates, measurable=True)
    assert decision["classification"] == "operational_rejection"
    assert decision["failed_gates"] == ["g.b"]
    passing = {"g": {"a": {"passes": True}}}
    assert R.classify(passing, measurable=True)["classification"] == (
        "provisional_operational_adoption"
    )


def test_insufficient_evidence_requires_an_unmeasurable_gate() -> None:
    gates = {"g": {"a": {"passes": True}}}
    assert R.classify(gates, measurable=False)["classification"] == "insufficient_evidence"


def test_the_decision_matches_the_recorded_gate_results() -> None:
    summary = _summary()
    failed = [
        f"{group}.{name}"
        for group, entries in dict(summary["gates"]).items()
        for name, detail in dict(entries).items()
        if not dict(detail)["passes"]
    ]
    assert _decision()["failed_gates"] == failed
    assert (_decision()["classification"] == "provisional_operational_adoption") == (not failed)


def test_the_stage_never_promotes_v1_1() -> None:
    assert _decision()["v1_1_status"] == "rejected"
    assert _decision()["model_status"] == "rejected"
    original = json.loads(
        (_root() / "outputs" / "four_phase_v1_1" / "validation_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert original["adopted"] is False


def test_conditional_artifacts_appear_only_on_provisional_adoption() -> None:
    adopted = _decision()["classification"] == "provisional_operational_adoption"
    for name in ("current_state_output.json", "live_monitoring_spec.md"):
        assert (_output() / name).exists() is adopted, name


# ── 출력 계약 ────────────────────────────────────────────────────────────────


def test_the_current_state_contract_carries_exactly_one_official_phase() -> None:
    """계약 자체를 검사한다. 산출물이 있으면 그것도 함께 본다.

    산출물 유무로 건너뛰면 잠정 채택이 아닐 때 이 요건이 검사되지 않은 채 남는다.
    """

    for phase in C.PHASES:
        payload = _contract_payload()
        payload["official_current_phase"] = phase
        C.validate(payload)
        assert payload["official_current_phase"] in C.PHASES
    path = _output() / "current_state_output.json"
    if path.exists():
        emitted = json.loads(path.read_text(encoding="utf-8"))
        assert emitted["official_current_phase"] in C.PHASES
        assert emitted["model_status"] == "provisional"
        for marker in C.AMBIGUOUS_MARKERS:
            assert marker not in str(emitted["official_current_phase"]).lower()


def _contract_payload() -> dict[str, Any]:
    return {
        "official_current_phase": "expansion",
        "phase_status": "official",
        "phase_separation": 0.4,
        "evidence_quality": "high",
        "activity_level": 0.1,
        "activity_momentum": 0.1,
        "domain_breadth": {"negative_level_domains": 0, "coincident_domains": 4},
        "contribution_concentration": 0.3,
        "supporting_domains": [],
        "opposing_domains": [],
        "mixed_domains": [],
        "transition_watch": "none",
        "recession_alert": "none",
        "recession_alert_character": "absent",
        "as_of_date": "2026-08-14",
        "latest_observation_by_domain": {"production": 1.0},
        "known_limitations": ["예측이 아니다"],
    }


def test_investment_style_output_is_rejected_recursively() -> None:
    payload = _contract_payload()
    C.validate(payload)
    for token in ("portfolio", "allocation", "valuation", "ticker", "buy", "sell"):
        bad = dict(payload)
        bad["domain_breadth"] = {**payload["domain_breadth"], token: 1}
        with pytest.raises(C.ContractViolation):
            C.validate(bad)


def test_an_ambiguous_phase_label_is_rejected() -> None:
    payload = {
        "official_current_phase": "expansion or slowdown",
        "phase_status": "official",
        "phase_separation": 0.1,
        "evidence_quality": "low",
        "activity_level": 0.0,
        "activity_momentum": 0.0,
        "domain_breadth": {},
        "contribution_concentration": 0.2,
        "supporting_domains": [],
        "opposing_domains": [],
        "mixed_domains": [],
        "transition_watch": "none",
        "recession_alert": "none",
        "recession_alert_character": "absent",
        "as_of_date": "2026-08-14",
        "latest_observation_by_domain": {},
        "known_limitations": [],
    }
    with pytest.raises(C.ContractViolation):
        C.validate(payload)


# ── 개정 원인 분리 ───────────────────────────────────────────────────────────


def test_direct_revision_and_path_dependence_are_never_merged() -> None:
    causes = dict(dict(_summary()["revision_risk"])["disagreement_causes"])
    assert set(causes) == set(revision.CAUSES)
    assert causes["direct_data_revision"] > 0
    assert causes["filter_path_dependence_after_revision"] > 0
    total = sum(causes.values())
    assert total == dict(_summary()["revision_risk"])["revision_changed_the_official_phase_weeks"]


def test_the_late_2019_revision_failure_stays_documented() -> None:
    late = dict(dict(_summary()["revision_risk"])["late_2019"])
    assert late["material_revision_sensitivity_failure"] is True
    assert late["realtime_contraction_weeks"] == 0
    assert late["latest_vintage_contraction_weeks"] > 0


# ── 재현성 ───────────────────────────────────────────────────────────────────


def test_two_clean_processes_produce_identical_decision_artifacts() -> None:
    root = _root()
    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        pytest.skip("프로젝트 가상환경을 찾을 수 없다")
    before = json.loads((_output() / "operational_decision.json").read_text(encoding="utf-8"))
    subprocess.run(
        [str(python), "-m", "business_cycle.operational_review"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=1800,
    )
    after = json.loads((_output() / "operational_decision.json").read_text(encoding="utf-8"))
    before.pop("executed_at_utc")
    after.pop("executed_at_utc")
    assert before == after
