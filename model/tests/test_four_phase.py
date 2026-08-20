"""미국 4국면 경기시계 v1: 계약, 증거, 필터, 게이트 모순."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.config import load_settings
from business_cycle.four_phase import contract as C
from business_cycle.four_phase import evidence as E
from business_cycle.four_phase import filter as F
from business_cycle.four_phase.engine import load_config


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _thresholds() -> E.Thresholds:
    return load_config(load_settings()).thresholds


# ── §3 출력 계약 ─────────────────────────────────────────────────────────────


def _payload() -> dict[str, object]:
    return {
        "official_current_phase": "slowdown",
        "phase_status": "official",
        "phase_separation": 0.21,
        "evidence_quality": "medium",
        "activity_level": -0.12,
        "activity_momentum": -0.34,
        "domain_breadth": {"negative_level_domains": 2, "coincident_domains": 4},
        "contribution_concentration": 0.41,
        "supporting_domains": ["production"],
        "opposing_domains": [],
        "mixed_domains": ["employment"],
        "transition_watch": "toward_contraction",
        "recession_alert": "watch",
        "as_of_date": "2026-08-14",
        "latest_observation_by_domain": {"production": 8.0},
        "known_limitations": ["현재상태 측정이며 예측이 아니다"],
    }


def test_contract_accepts_a_correct_payload() -> None:
    C.validate(_payload())


def test_exactly_four_official_phases_exist() -> None:
    assert C.PHASES == ("recovery", "expansion", "slowdown", "contraction")
    assert len(C.PHASES) == 4
    assert not any("early" in p or "middle" in p or "late" in p for p in C.PHASES)


def test_missing_required_field_is_rejected() -> None:
    payload = _payload()
    del payload["evidence_quality"]
    with pytest.raises(C.ContractViolation, match="필수 필드"):
        C.validate(payload)


@pytest.mark.parametrize(
    "label",
    ["expansion or slowdown", "recovery / expansion", "near contraction", "mixed"],
)
def test_ambiguous_official_labels_are_rejected(label: str) -> None:
    payload = _payload()
    payload["official_current_phase"] = label
    with pytest.raises(C.ContractViolation):
        C.validate(payload)


def test_withheld_status_carries_no_official_phase() -> None:
    payload = _payload()
    payload["phase_status"] = "withheld"
    with pytest.raises(C.ContractViolation, match="판정 보류"):
        C.validate(payload)
    payload["official_current_phase"] = None
    C.validate(payload)


def test_low_evidence_still_yields_one_official_phase() -> None:
    payload = _payload()
    payload["evidence_quality"] = "low"
    C.validate(payload)
    assert payload["official_current_phase"] in C.PHASES


def test_recession_alert_is_not_a_fifth_phase() -> None:
    payload = _payload()
    payload["recession_alert"] = "contraction"
    with pytest.raises(C.ContractViolation):
        C.validate(payload)


# ── §22 투자 판단 금지 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    [
        "sector_recommendation",
        "asset_class_weight",
        "portfolio_action",
        "allocation_target",
        "valuation_multiple",
        "buy_signal",
        "target_price",
        "ticker_list",
    ],
)
def test_investment_fields_are_rejected(field: str) -> None:
    payload = _payload()
    payload[field] = "무엇이든"
    with pytest.raises(C.ContractViolation, match="투자 판단"):
        C.validate(payload)


def test_investment_fields_are_rejected_when_nested() -> None:
    payload = _payload()
    payload["domain_breadth"] = {"detail": [{"portfolio_weight": 0.3}]}
    with pytest.raises(C.ContractViolation, match="투자 판단"):
        C.validate(payload)


def test_no_investment_language_in_the_country_contract() -> None:
    """계약의 **의미 설명**에는 투자 문구가 없어야 한다.

    금지 토큰 목록 자체에는 그 단어들이 들어 있는 것이 정상이다. 그 목록은 막기 위한
    것이지 출력이 아니다.
    """

    schema = C.country_schema()
    text = json.dumps(
        {k: v for k, v in schema.items() if k != "forbidden_tokens"}, ensure_ascii=False
    ).lower()
    for token in ("buy", "sell", "portfolio", "allocation", "target_price"):
        assert token not in text


# ── §21 국가 확장 계약 ───────────────────────────────────────────────────────


def test_country_contract_keeps_semantics_stable() -> None:
    schema = C.country_schema()
    assert schema["phases"] == list(C.PHASES)
    for phase in C.PHASES:
        assert phase in schema["semantics"]
    assert "evidence_quality" in schema["semantics"]
    assert "transition_watch" in schema["semantics"]
    assert "recession_alert" in schema["semantics"]


# ── §9 침체 증거 ─────────────────────────────────────────────────────────────


def test_labor_stress_alone_cannot_reach_the_contraction_entry() -> None:
    """§9: 노동시장 단독으로는 침체를 선언할 수 없다."""

    thresholds = _thresholds()
    evidence = E.contraction_evidence(0.2, 0.2, 0, 0, -5.0, -5.0, thresholds)
    assert evidence["contraction_evidence"] <= thresholds.corroboration_share + 1e-9
    assert thresholds.corroboration_share < thresholds.contraction_entry


def test_corroboration_is_additive_not_a_hard_ceiling() -> None:
    """후보 J는 min 때문에 뒷받침 하한 0.5가 경로 전체의 천장이었다."""

    thresholds = _thresholds()
    without = E.contraction_evidence(-2.0, -2.0, 4, 4, 1.0, 1.0, thresholds)
    with_labor = E.contraction_evidence(-2.0, -2.0, 4, 4, -2.0, -2.0, thresholds)
    assert without["contraction_evidence"] > thresholds.corroboration_share
    assert with_labor["contraction_evidence"] >= without["contraction_evidence"]


def test_two_contraction_routes_are_separately_satisfiable() -> None:
    thresholds = _thresholds()
    broad = E.contraction_evidence(-2.5, -0.5, 4, 2, -1.0, -1.0, thresholds)
    rapid = E.contraction_evidence(-0.4, -2.5, 1, 4, -2.0, -2.0, thresholds)
    assert broad["broad_level_route"] > 0.0
    assert rapid["rapid_deterioration_route"] > 0.0


# ── §10 회복 증거 ────────────────────────────────────────────────────────────


def test_one_week_of_positive_momentum_is_not_recovery() -> None:
    """후보 J의 2단계 진동 10건이 전부 여기서 나왔다."""

    thresholds = _thresholds()
    brief = E.recovery_evidence(-1.0, 0.8, 3, 1, thresholds)
    persistent = E.recovery_evidence(
        -1.0, 0.8, 3, thresholds.recovery_persistence_weeks, thresholds
    )
    assert brief["recovery_evidence"] < persistent["recovery_evidence"]
    assert brief["recovery_evidence"] < 0.25


def test_recovery_requires_a_weak_starting_level() -> None:
    thresholds = _thresholds()
    from_strong = E.recovery_evidence(2.0, 0.8, 4, 20, thresholds)
    from_weak = E.recovery_evidence(-1.5, 0.8, 4, 20, thresholds)
    assert from_strong["recovery_evidence"] < from_weak["recovery_evidence"]


# ── §11 관측 점수 ────────────────────────────────────────────────────────────


def test_observation_scores_sum_to_one_and_are_never_zero() -> None:
    thresholds = _thresholds()
    breadth = dict.fromkeys(C.PHASES, 0.0)
    for level, momentum in ((0.0, 0.0), (2.0, 2.0), (-2.0, -2.0), (-0.1, 0.1)):
        scores = E.observation_scores(level, momentum, 0.0, 0.0, breadth, thresholds)
        assert pytest.approx(sum(scores.values()), rel=1e-6) == 1.0
        assert min(scores.values()) > 0.0
        assert set(scores) == set(C.PHASES)


def test_neutral_zone_does_not_automatically_favour_slowdown() -> None:
    """§11: 애매한 주를 전부 후퇴기로 보내지 않는다.

    후보 J는 침체 증거가 모자랄 때 그 몫을 전부 후퇴기로 보냈다. 여기서는 나머지 셋에
    비례 배분하므로 중립대 한가운데서 세 국면이 비슷해진다.
    """

    thresholds = _thresholds()
    breadth = dict.fromkeys(C.PHASES, 0.0)
    scores = E.observation_scores(0.0, 0.0, 0.0, 0.0, breadth, thresholds)
    assert scores["slowdown"] < 0.45, "중립대 한가운데서 후퇴기가 과반을 넘었다"
    # 실제로 지켜야 할 것은 후퇴기가 확장기보다 유리해지지 않는 것이다. 회복은 폭·지속
    # 요건 때문에 기준선(1/4)에 머무는 것이 설계 의도이므로 세 국면이 같을 필요는 없다.
    assert scores["slowdown"] == pytest.approx(scores["expansion"], abs=1e-6)
    assert scores["recovery"] == pytest.approx(0.25, abs=1e-3)


def test_breadth_tilts_the_decision_inside_the_neutral_zone() -> None:
    thresholds = _thresholds()
    flat = dict.fromkeys(C.PHASES, 0.0)
    leaning = {**flat, "expansion": 1.0}
    base = E.observation_scores(0.0, 0.0, 0.0, 0.0, flat, thresholds)
    tilted = E.observation_scores(0.0, 0.0, 0.0, 0.0, leaning, thresholds)
    assert tilted["expansion"] > base["expansion"]


# ── §12 4상태 소프트 필터 ────────────────────────────────────────────────────


def test_transition_matrix_is_strictly_positive_and_ergodic() -> None:
    matrix = F.transition_matrix(1.8, 0.01)
    assert (matrix > 0).all()
    assert F.is_ergodic(matrix)
    assert np.allclose(matrix.sum(axis=1), 1.0)
    assert matrix.shape == (4, 4)


def test_zero_epsilon_is_rejected() -> None:
    with pytest.raises(ValueError, match="epsilon"):
        F.transition_matrix(1.8, 0.0)


def test_adjacent_moves_are_preferred_over_the_opposite_phase() -> None:
    matrix = F.transition_matrix(1.8, 0.01)
    assert matrix[0, 0] > matrix[0, 1] > matrix[0, 2]
    assert matrix[0, 1] == pytest.approx(matrix[0, 3])


def test_persistent_evidence_overcomes_the_filter_from_any_start() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=52, freq="W-FRI"))
    scores = pd.DataFrame(
        {"recovery": 0.03, "expansion": 0.90, "slowdown": 0.04, "contraction": 0.03},
        index=index,
    )
    matrix = F.transition_matrix(1.8, 0.01)
    for start in range(4):
        prior = np.full(4, 1e-9)
        prior[start] = 1.0
        prior = prior / prior.sum()
        for likelihood in scores[list(C.PHASES)].to_numpy(dtype=float):
            posterior = (prior @ matrix) * likelihood
            prior = posterior / float(posterior.sum())
        assert C.PHASES[int(np.argmax(prior))] == "expansion"


def test_finite_memory_converges_on_identical_recent_evidence() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=26, freq="W-FRI"))
    scores = pd.DataFrame(
        {"recovery": 0.05, "expansion": 0.08, "slowdown": 0.82, "contraction": 0.05},
        index=index,
    )
    result = F.convergence(scores, 1.8, 0.01, (4, 13, 26))
    assert all(value["converged"] for value in result.values())


# ── §16 동결 설정 ────────────────────────────────────────────────────────────


def test_frozen_config_hash_matches_the_recorded_snapshot() -> None:
    settings = load_settings()
    config = load_config(settings)
    recorded = (
        (_root() / "outputs" / "four_phase" / "frozen_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert config.sha256 == recorded
    assert config.document["model"] == "us_four_phase_v1"


def test_radius_is_diagnostic_only() -> None:
    settings = load_settings()
    assert load_config(settings).document["radius_role"] == "diagnostic_only"


def test_inherited_preprocessing_decisions_are_recorded() -> None:
    settings = load_settings()
    inherited = load_config(settings).document["inherited_preprocessing"]
    assert inherited["momentum_scale_method"] == "rolling_mad"
    assert inherited["fabricate_zero_between_releases"] is False
    assert "RRSFS" in inherited["no_additional_deflation"]
    assert "payems_population" in inherited["no_scale_division"]


def test_only_two_transition_parameters_exist() -> None:
    settings = load_settings()
    soft = load_config(settings).document["soft_filter"]
    assert set(soft) == {"lambda", "epsilon"}


# ── §18 게이트 모순이 기록으로 남는가 ────────────────────────────────────────


def test_gate_contradiction_is_recorded_and_the_model_is_not_adopted() -> None:
    path = _root() / "outputs" / "four_phase" / "validation_summary.json"
    if not path.exists():
        pytest.skip("4국면 산출물이 아직 생성되지 않았다")
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["adopted"] is False
    assert summary["gate_contradiction"] is True
    assert summary["latest_vintage_validation_run"] is False
    assert summary["strict_alfred_run"] is False
    feasibility = summary["gate_feasibility"]
    assert feasibility["negative_level_domains_at_least_2"]["feasible"] is False
    assert feasibility["negative_level_domains_at_least_0"]["feasible"] is True
    # 구조 지표는 실제로 좋아졌다는 것도 함께 남는다.
    # 구조 지표는 개발구간 **측정값**이다. 최신 수정치 검증을 실행하지 않았으므로
    # 채택 게이트 판정이 아니다. 그래서 값이 기록돼 있는지만 확인한다.
    metrics = summary["development_metrics"]
    assert metrics["two_step_transitions"] <= 5
    assert isinstance(metrics["three_week_whipsaws"], int)
    assert len(metrics["phases_reached"]) == 4
    assert summary["soft_filter"]["ergodic"] is True
    assert summary["release_carry"]["domain_weeks_with_exact_zero_momentum"] == 0


# ── 날짜 과적합 방지 ─────────────────────────────────────────────────────────

_DATE = re.compile(r"""["'](19|20)\d{2}-\d{2}-\d{2}["']""")


def test_no_date_literals_in_four_phase_model_logic() -> None:
    root = _root() / "src" / "business_cycle" / "four_phase"
    offenders: list[str] = []
    for path in root.glob("*.py"):
        if path.name == "report.py":
            continue
        for match in _DATE.finditer(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {match.group(0)}")
    assert not offenders, offenders


def test_identical_evidence_gives_identical_scores() -> None:
    thresholds = _thresholds()
    breadth = dict.fromkeys(C.PHASES, 0.25)
    first = E.observation_scores(-0.4, -0.6, 0.5, 0.1, breadth, thresholds)
    second = E.observation_scores(-0.4, -0.6, 0.5, 0.1, breadth, thresholds)
    assert first == second


# ── 이전 후보 보존 ───────────────────────────────────────────────────────────


def test_previous_candidates_remain_unchanged() -> None:
    phase6 = json.loads(
        (
            _root() / "outputs" / "robustness_validation" / "phase6" / "validation_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        phase6["frozen_hash"] == "c367e2a0f8e907b6f927191f03379bab5ea5eace6b671454c4b63e44d4b2bb21"
    )
    for path, digest in (
        (
            "current_state/frozen_candidate_config.sha256",
            "765e2ee65b70a185159faa928c2df9c734c19e583dc8655ae47c80ec3d056993",
        ),
        (
            "candidate_j/frozen_candidate_config.sha256",
            "a0d875268f1d720a29659f96695e74391db7fd9a3a0213b8c8970e6399a6098f",
        ),
    ):
        recorded = (_root() / "outputs" / path).read_text(encoding="utf-8").split()[0]
        assert recorded == digest
    for name in ("current_state", "candidate_j"):
        summary = json.loads(
            (_root() / "outputs" / name / "validation_summary.json").read_text(encoding="utf-8")
        )
        assert summary["adopted"] is False
