"""상태 의미론 감사: 계약 도출, 분류 사다리, 2001년 경로, 결정, 의미 지문.

이 단계는 동결 v1.1을 **읽기만** 한다. skip을 쓰지 않는다.

가장 중요한 시험은 두 방향이다 — **국면 순서가 뒤집혔다는 것만으로는 실패가 아니고,
강한 증거를 거스른 라벨은 실패다.** 둘 중 하나만 지키면 감사가 무의미해진다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from business_cycle.config import load_settings
from business_cycle.four_phase.engine import load_config
from business_cycle.operational_review.preserve import ProtectedArtifactChanged
from business_cycle.state_semantics import (
    canonical,
    classify,
    contract,
    decide,
    episodes,
    preserve,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _output() -> Path:
    return _root() / "outputs" / "state_semantics"


def _summary() -> dict[str, Any]:
    path = _output() / "validation_summary.json"
    assert path.exists(), "상태 의미론 산출물이 없다. `python -m business_cycle.state_semantics`"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _decision() -> dict[str, Any]:
    path = _output() / "state_semantics_decision.json"
    assert path.exists(), "결정 산출물이 없다"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _weekly() -> pd.DataFrame:
    return pd.read_csv(_output() / "weekly_semantic_audit.csv")


# ── 보존 ─────────────────────────────────────────────────────────────────────


def test_protected_hashes_and_prior_decisions_are_verified_first() -> None:
    provenance = preserve.verify(load_settings())
    assert provenance["verified"] is True
    assert provenance["expected_source_commit"] == preserve.SOURCE_COMMIT
    assert provenance["stage_is_read_only"] is True
    assert provenance["model_logic_changed"] is False
    assert provenance["new_classifier_introduced"] is False
    assert provenance["recovery_semantics_semantic_digest"] == preserve.RECOVERY_SEMANTICS_DIGEST


def test_a_changed_protected_hash_stops_the_audit() -> None:
    from business_cycle.operational_review.preserve import PROTECTED

    original = dict(PROTECTED)
    try:
        PROTECTED["v1_1_config"] = "0" * 64
        with pytest.raises(ProtectedArtifactChanged):
            preserve.verify(load_settings())
    finally:
        PROTECTED.clear()
        PROTECTED.update(original)


def test_no_protected_path_was_modified() -> None:
    assert preserve.git_status_of_protected_paths(load_settings()) == []
    assert "src/business_cycle/recovery_semantics" in preserve.PROTECTED_PATHS


def test_the_two_prior_decisions_are_carried_forward() -> None:
    prior = preserve.prior_decisions(load_settings())
    assert (
        prior["outputs/operational_review/operational_decision.json"]["classification"]
        == "operational_rejection"
    )
    assert (
        prior["outputs/recovery_semantics/recovery_semantics_decision.json"]["classification"]
        == "provisional_operational_adoption"
    )


# ── §3 계약이 동결 코드에서 나왔는가 ────────────────────────────────────────


def test_the_semantic_contract_is_derived_from_the_frozen_config() -> None:
    config = load_config(load_settings())
    built = contract.build(config)
    assert built["derived_not_invented"] is True
    assert built["new_classifier_introduced"] is False
    assert built["frozen_config_sha256"] == config.sha256
    assert built["thresholds"] == config.thresholds.to_dict()
    assert built["soft_filter"]["confirmation_weeks"] == config.confirmation_weeks
    assert built["soft_filter"]["separation_floor"] == config.separation_floor
    for phase in ("recovery", "expansion", "slowdown", "contraction"):
        entry = built["phases"][phase]
        assert entry["level_condition"] and entry["momentum_condition"]
        assert entry["breadth_requirement"] and entry["damping"]


def test_no_new_phase_classifier_is_introduced() -> None:
    package = _root() / "src" / "business_cycle" / "state_semantics"
    banned = (
        "def observation_scores",
        "def contraction_evidence",
        "def recovery_evidence",
        "def confirm_transitions",
        "def transition_matrix",
        "Thresholds(",
    )
    for path in package.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in body, f"{path.name}에 새 분류기가 있다: {token}"


def test_the_sign_quadrant_centre_is_zero_not_the_neutral_band() -> None:
    # 동결 구성은 sigmoid(level / neutral_level)이므로 부호 경계는 0이다. neutral_*은
    # 문턱이 아니라 전이의 부드러움을 정하는 척도다.
    assert contract.sign_quadrant(0.1, 0.1) == "expansion"
    assert contract.sign_quadrant(0.1, -0.1) == "slowdown"
    assert contract.sign_quadrant(-0.1, 0.1) == "recovery"
    assert contract.sign_quadrant(-0.1, -0.1) == "contraction"
    assert contract.opposite("expansion") == "contraction"
    assert contract.opposite("recovery") == "slowdown"


# ── §4 사다리 ────────────────────────────────────────────────────────────────


def _row(**overrides: Any) -> dict[str, Any]:
    base = {
        "phase_status": "official",
        "official_phase": "expansion",
        "raw_phase": "expansion",
        "filtered_winner": "expansion",
        "evidence_quality_high": True,
        "confirmation_pending": 0,
        "neutral_both": False,
        "raw_versus_official_run": 0,
    }
    base.update(overrides)
    return base


def test_every_week_gets_exactly_one_class_from_the_vocabulary() -> None:
    weekly = _weekly()
    assert set(weekly["semantic_class"]) <= set(classify.CLASSES)
    assert weekly["semantic_class"].notna().all()
    assert len(weekly) == sum(
        entry["total_weeks"] for entry in dict(_summary()["samples"]).values()
    )


def test_a_reversed_phase_order_alone_is_not_a_failure() -> None:
    # 후퇴기에서 확장기로 재가속. 순서상 "역행"이지만 증거가 뒷받침하면 지지다.
    label, reason = classify.classify_week(
        _row(official_phase="expansion", raw_phase="expansion"), 3
    )
    assert label == "semantically_supported"
    assert reason == "official_equals_raw_high_quality"
    # 회복기에서 침체로 되돌아감. 마찬가지다.
    label, _ = classify.classify_week(
        _row(official_phase="contraction", raw_phase="contraction"), 3
    )
    assert label == "semantically_supported"


def test_strong_evidence_contradicting_the_official_phase_is_a_conflict() -> None:
    label, reason = classify.classify_week(
        _row(official_phase="expansion", raw_phase="contraction", filtered_winner="contraction"),
        3,
    )
    assert label == "semantic_conflict"
    assert reason == "contradicts_strong_evidence"


def test_neutral_band_retention_is_classified_separately() -> None:
    label, reason = classify.classify_week(
        _row(
            official_phase="expansion",
            raw_phase="slowdown",
            filtered_winner="slowdown",
            neutral_both=True,
            evidence_quality_high=False,
        ),
        3,
    )
    assert label == "neutral_band_retention"
    assert reason == "neutral_band"


def test_confirmation_lag_is_bounded_and_named() -> None:
    label, reason = classify.classify_week(
        _row(official_phase="slowdown", raw_phase="expansion", confirmation_pending=2), 3
    )
    assert label == "bounded_confirmation_lag"
    assert reason == "confirmation_in_flight"
    # 확인 한도를 넘어선 대기는 이 설명으로 덮이지 않는다.
    label, _ = classify.classify_week(
        _row(
            official_phase="slowdown",
            raw_phase="expansion",
            filtered_winner="expansion",
            confirmation_pending=9,
            raw_versus_official_run=99,
        ),
        3,
    )
    assert label == "semantic_conflict"


def test_the_filter_explanation_is_bounded_not_unlimited() -> None:
    inside = _row(
        official_phase="expansion",
        raw_phase="slowdown",
        filtered_winner="expansion",
        raw_versus_official_run=classify.RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT,
    )
    label, reason = classify.classify_week(inside, 3)
    assert label == "semantically_supported"
    assert reason == "filter_absorbed_raw_flip"
    outside = dict(inside)
    outside["raw_versus_official_run"] = classify.RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT + 1
    label, _ = classify.classify_week(outside, 3)
    assert label == "semantic_conflict"


def test_low_evidence_cannot_be_reported_as_high() -> None:
    for sample, rules in dict(_summary()["hard_rules"]).items():
        entry = rules["every_low_separation_output_reports_low_quality"]
        assert entry["passes"] is True, sample
        assert entry["value"] == 0
    weekly = _weekly()
    high = weekly[weekly["evidence_quality"] == "high"]
    assert (high["phase_separation"] >= high["separation_floor"]).all()


def test_a_withheld_week_emits_no_official_phase() -> None:
    label, _ = classify.classify_week(_row(phase_status="withheld"), 3)
    assert label == "withheld"
    weekly = _weekly()
    withheld = weekly[weekly["phase_status"] == "withheld"]
    assert len(withheld) > 0
    assert withheld["official_phase"].fillna("").eq("").all()


def test_materiality_uses_the_frozen_neutral_band_not_bare_signs() -> None:
    # 수준 −0.008 같은 칼날 위의 값을 "수준이 어긋났다"고 주장하지 않는다.
    weekly = _weekly()
    config = load_config(load_settings())
    flagged = weekly[weekly["level_materially_contradicted"]]
    assert (flagged["activity_level"].abs() > config.thresholds.neutral_level).all()
    flagged = weekly[weekly["momentum_materially_contradicted"]]
    assert (flagged["activity_momentum"].abs() > config.thresholds.neutral_momentum).all()


# ── §6 2001년 경로 ──────────────────────────────────────────────────────────


def test_the_2001_chronology_is_preserved_and_answered() -> None:
    path = dict(_summary()["path_2001"])
    assert path["weeks"] > 0
    assert path["non_monotonic_is_not_a_failure"] is True
    for key in (
        "1_contraction_exit_supported_by_contemporaneous_evidence",
        "2_why_slowdown_rather_than_recovery",
        "3_why_expansion_later",
        "4_why_recovery_only_after_slowdown_and_expansion",
        "5_did_labels_reflect_genuine_changes",
        "6_was_any_label_retained_against_strong_evidence",
        "7_semantic_contradiction_or_non_monotonic_but_supported",
        "8_would_forcing_a_recovery_label_have_been_more_accurate",
    ):
        assert key in path["answers"], key
    transitions = path["official_phase_transitions"]
    assert transitions, "전환이 하나도 없으면 경로 감사가 성립하지 않는다"


def test_the_2001_path_conflicts_are_reported_not_hidden() -> None:
    path = dict(_summary()["path_2001"])
    assert path["semantic_conflict_weeks"] == 0
    # 둘 다 어긋난 높은 증거 주가 있으면 그 사실이 답변에 그대로 적혀 있어야 한다.
    answer = dict(path["answers"]["6_was_any_label_retained_against_strong_evidence"])
    assert answer["count"] == path["high_evidence_both_signs_contradicted_weeks"]
    if answer["count"]:
        assert answer["answer"].startswith("그렇다")
        assert answer["high_evidence_both_signs_contradicted_weeks"]


def test_the_vintage_limitation_is_preserved() -> None:
    assert "2013-06-14" in dict(_summary()["path_2001"])["vintage_limitation"]


# ── §7 에피소드 ─────────────────────────────────────────────────────────────


def test_every_required_episode_is_audited() -> None:
    audited = {entry["episode"] for entry in _summary()["episodes"]}
    assert audited == {name for name, _, _, _ in episodes.EPISODES}
    for entry in _summary()["episodes"]:
        assert entry["kind"] in episodes.EPISODE_KINDS
        assert entry.get("phase_order_reversals_are_not_failures", True) is True


# ── §8 현재 출력 ────────────────────────────────────────────────────────────


def test_the_current_output_names_exactly_one_phase_and_keeps_low_quality() -> None:
    now = dict(_summary()["current_state_semantics"])
    assert now["official_current_phase"] in ("recovery", "expansion", "slowdown", "contraction")
    assert now["single_official_phase"] is True
    assert now["evidence_quality_was_not_upgraded"] is True
    body = (_output() / "state_semantics_report.md").read_text(encoding="utf-8")
    assert f"Current official U.S. phase: {now['official_current_phase']}" in body
    assert f"Evidence quality: {now['evidence_quality']}" in body
    for ambiguous in ("expansion or slowdown", "near recovery", "between phases"):
        assert ambiguous not in now["official_current_phase"]


def test_the_current_output_separates_low_evidence_from_retention() -> None:
    now = dict(_summary()["current_state_semantics"])
    assert now["semantically_supported_or_retained"] in (
        "semantically_supported",
        "low_evidence_but_contemporaneously_agreed",
        "low_evidence_retained_state",
    )
    if now["official_current_phase"] == now["raw_current_phase"]:
        assert now["semantically_supported_or_retained"] != "low_evidence_retained_state"
        assert now["previous_state_materially_determines_the_result"] is False


def test_the_current_output_says_what_would_change_the_phase() -> None:
    requirement = dict(
        _summary()["current_state_semantics"]["what_would_change_the_official_phase"]
    )
    assert set(requirement) == {
        "route_a_immediate_transition",
        "route_b_confirmation",
        "what_would_move_the_evidence_quality_to_high",
    }
    assert all(value for value in requirement.values())


# ── §10 결정 ────────────────────────────────────────────────────────────────


def test_exactly_one_classification_is_emitted() -> None:
    decision = _decision()
    assert decision["classification"] in decide.CLASSIFICATIONS
    assert decision["forbidden_classification"] == "final_validated"
    assert decide.FORBIDDEN_CLASSIFICATION not in decide.CLASSIFICATIONS
    assert decision["is_final_validation"] is False


def _decide(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "high_evidence_conflicts": 0,
        "bounded_delays_within_limits": True,
        "low_evidence_disclosed": True,
        "path_2001": {"exposes_a_real_semantic_failure": False},
        "previous_gates_pass": True,
        "hashes_unchanged": True,
        "hard_rules": {},
        "repeated_contradiction_beyond_the_bound": False,
    }
    base.update(overrides)
    return decide.decide(**base)


def test_a_high_evidence_conflict_makes_the_lock_impossible() -> None:
    assert _decide()["classification"] == "provisional_model_locked"
    assert _decide(high_evidence_conflicts=1)["classification"] == "operational_rejection_confirmed"


def test_a_real_2001_semantic_failure_forces_rejection() -> None:
    result = _decide(path_2001={"exposes_a_real_semantic_failure": True})
    assert result["classification"] == "operational_rejection_confirmed"


def test_a_regressed_gate_or_changed_hash_forces_rejection() -> None:
    assert _decide(previous_gates_pass=False)["classification"] == "operational_rejection_confirmed"
    assert _decide(hashes_unchanged=False)["classification"] == "operational_rejection_confirmed"
    assert (
        _decide(repeated_contradiction_beyond_the_bound=True)["classification"]
        == "operational_rejection_confirmed"
    )


def test_a_literally_failing_hard_rule_is_disclosed_with_its_counterfactual() -> None:
    result = _decide(hard_rules={"some_rule": {"passes": False}})
    assert result["hard_rules_literally_failing"] == ["some_rule"]
    assert result["hard_rule_failures_are_disclosed_not_gated"] is True
    assert result["counterfactual_if_every_section_five_rule_gated"] == (
        "operational_rejection_confirmed"
    )
    assert result["counterfactual_changes_the_decision"] is True
    # 실제 산출물도 같은 방식으로 공시해야 한다.
    decision = _decision()
    if decision["hard_rules_literally_failing"]:
        body = (_output() / "state_semantics_report.md").read_text(encoding="utf-8")
        assert decision["hard_rules_literally_failing"][0] in body


def test_all_previous_gates_are_rerun_unchanged() -> None:
    recovery = json.loads(
        (_root() / "outputs/recovery_semantics/validation_summary.json").read_text(encoding="utf-8")
    )
    assert recovery["rechecked_gates"]["regressed_gates"] == []
    assert recovery["rechecked_gates"]["all_previously_passed_gates_still_pass"] is True
    assert recovery["decision"]["classification"] == "provisional_operational_adoption"


# ── §11 의미 지문 ───────────────────────────────────────────────────────────


def test_the_semantic_digest_matches_a_recomputed_one() -> None:
    payload = _summary()
    assert payload["semantic_digest"] == canonical.semantic_digest(payload)
    assert _decision()["semantic_digest"] == payload["semantic_digest"]
    assert set(payload["semantic_digest_excludes"]) == set(canonical.VOLATILE_FIELDS)
    assert set(payload["semantic_digest_covers"]) == set(canonical.COVERED)


def test_only_volatile_execution_metadata_is_excluded() -> None:
    payload = _summary()
    before = canonical.semantic_digest(payload)
    payload["provenance"]["executed_at_utc"] = "1999-01-01T00:00:00+00:00"
    payload["provenance"]["head_commit"] = "0" * 40
    assert canonical.semantic_digest(payload) == before


MUTATIONS: list[tuple[str, Any]] = [
    (
        "classification",
        lambda p: p["decision"].__setitem__("classification", "operational_rejection_confirmed"),
    ),
    (
        "weekly_class",
        lambda p: p["weekly_class_sequence"]["strict_alfred_real_time"][0].__setitem__(
            1, "semantic_conflict"
        ),
    ),
    (
        "conflict_count",
        lambda p: p["samples"]["strict_alfred_real_time"].__setitem__(
            "high_evidence_semantic_conflicts", 7
        ),
    ),
    (
        "path_2001",
        lambda p: p["path_2001"].__setitem__("semantic_conflict_weeks", 5),
    ),
    (
        "current_phase",
        lambda p: p["current_state_semantics"].__setitem__("official_current_phase", "slowdown"),
    ),
    (
        "previous_state_dependence",
        lambda p: p["samples"]["strict_alfred_real_time"].__setitem__(
            "previous_state_retention_weeks", 999
        ),
    ),
    (
        "protected_hash",
        lambda p: p["provenance"]["hashes"].__setitem__("v1_1_config", "0" * 64),
    ),
    (
        "hard_rule",
        lambda p: p["hard_rules"]["strict_alfred_real_time"][
            "no_high_evidence_semantic_conflict"
        ].__setitem__("passes", False),
    ),
    ("episode_kind", lambda p: p["episodes"][0].__setitem__("kind", "semantic_conflict_present")),
]


@pytest.mark.parametrize("name,mutate", MUTATIONS, ids=[name for name, _ in MUTATIONS])
def test_any_substantive_change_breaks_the_semantic_digest(name: str, mutate: Any) -> None:
    payload = _summary()
    before = canonical.semantic_digest(payload)
    mutate(payload)
    assert canonical.semantic_digest(payload) != before, name


# ── 산출물·재현성·금지 ──────────────────────────────────────────────────────


def test_the_lock_artifacts_exist_only_when_locked() -> None:
    locked = _decision()["classification"] == "provisional_model_locked"
    for name in (
        "operational_manifest.json",
        "current_state_output.json",
        "live_monitoring_spec.md",
        "monitoring_baseline_snapshot.json",
    ):
        assert (_output() / name).exists() is locked, name


def test_the_monitoring_baseline_records_development_stopped() -> None:
    if _decision()["classification"] != "provisional_model_locked":
        return
    baseline = json.loads(
        (_output() / "monitoring_baseline_snapshot.json").read_text(encoding="utf-8")
    )
    assert baseline["role"] == "thirteen_week_monitoring_baseline"
    assert baseline["model_development"] == "stopped"
    assert baseline["immutable"] is True
    record = json.loads((_output() / "operational_manifest.json").read_text(encoding="utf-8"))
    assert record["model_status"] == "provisional"
    assert record["us_four_phase_model_development"] == "stopped"
    assert record["is_final_validation"] is False


def test_investment_related_output_remains_prohibited() -> None:
    if _decision()["classification"] != "provisional_model_locked":
        return
    from business_cycle.recovery_semantics import manifest as MF

    state = json.loads((_output() / "current_state_output.json").read_text(encoding="utf-8"))
    MF.validate_contract(state)
    for token in ("portfolio", "allocation", "valuation", "buy", "sell"):
        with pytest.raises(ValueError):
            MF.validate_contract({**state, "breadth": {**state["breadth"], token: 1}})


def test_two_clean_processes_produce_identical_results() -> None:
    root = _root()
    python = root / ".venv" / "Scripts" / "python.exe"
    assert python.exists(), "프로젝트 가상환경을 찾을 수 없다"
    before = _decision()["semantic_digest"]
    subprocess.run(
        [str(python), "-m", "business_cycle.state_semantics"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=3600,
    )
    assert _decision()["semantic_digest"] == before
