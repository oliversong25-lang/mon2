"""미국 4국면 경기시계 v1: 계약, 증거, 필터, 게이트 모순."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.config import load_baseline, load_settings
from business_cycle.data.alfred import slice_vintage
from business_cycle.four_phase import alfred as AL
from business_cycle.four_phase import contract as C
from business_cycle.four_phase import evidence as E
from business_cycle.four_phase import filter as F
from business_cycle.four_phase import freshness as FRESH
from business_cycle.four_phase import frontier as FR
from business_cycle.four_phase import validation as V
from business_cycle.four_phase.engine import (
    STOPPED_CONFIG_NAME,
    load_config,
    prepare,
    score,
)
from business_cycle.validation.phase4 import load_core_observations


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
        "recession_alert_character": "preliminary",
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
        (_root() / "outputs" / "four_phase_v1_1" / "frozen_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert config.sha256 == recorded
    assert config.document["model"] == "us_four_phase_v1"
    assert config.document["version"] == "1.1"


def test_the_stopped_v1_0_configuration_is_preserved_unchanged() -> None:
    """§10. 새 버전을 만들되 이전 스냅샷을 조용히 덮어쓰지 않는다."""

    settings = load_settings()
    stopped = load_config(settings, STOPPED_CONFIG_NAME)
    recorded = (
        (_root() / "outputs" / "four_phase" / "frozen_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert stopped.sha256 == recorded
    assert stopped.sha256 == "892fbbfb4b9f72f1611097354298380b14875e955a6f3b0e36a47376c2b53027"
    assert stopped.document["status"] == "development_locked_not_validated"


def test_the_new_configuration_records_what_changed_and_why() -> None:
    document = load_config(load_settings()).document
    decision = document["conceptual_decision"]
    assert decision["option"] == 2
    assert decision["previous_config_sha256"] == (
        "892fbbfb4b9f72f1611097354298380b14875e955a6f3b0e36a47376c2b53027"
    )
    assert decision["rejected"] == [1, 3]
    assert document["thresholds"]["minimum_coincident_domains"] == 2
    assert document["adoption_gates"]["false_positive_rate_maximum"] == 0.05
    assert document["adoption_gates"]["core_recession_recall_minimum"] == 0.90
    assert document["adoption_gates"]["recession_recall_minimum"] == 0.80
    assert set(decision["changed_gates"]) and set(decision["changed_parameters"])


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
    stopped = load_config(settings, STOPPED_CONFIG_NAME).document["soft_filter"]
    assert set(stopped) == {"lambda", "epsilon"}
    # §8은 안정화 장치를 **전역 단일 규칙 하나**로 제한한다. 국면마다 따로 두지 않는다.
    current = load_config(settings).document["soft_filter"]
    assert set(current) == {"lambda", "epsilon", "confirmation_weeks", "immediate_margin"}
    assert not any(name in key for key in current for name in C.PHASES)


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


#: 모델 로직이 아닌 모듈. 각각 이유가 다르다.
#:
#: * ``report.py`` 산출물 생성기. 개발·검증 구간 경계와 NBER 에피소드 시작일을 쓴다.
#: * ``frontier.py`` 개발구간 탐색 하네스. 개발 구간 경계를 쓴다.
#: * ``alfred.py`` 아카이브 커버리지 경계(2013-06-14)를 쓴다. 이 날짜는 모델의 규칙이
#:   아니라 자료의 사실이다 — 일곱 지표가 모두 진짜 빈티지를 갖는 첫 주다.
_NOT_MODEL_LOGIC = {"report.py", "frontier.py", "alfred.py"}


def test_no_date_literals_in_four_phase_model_logic() -> None:
    root = _root() / "src" / "business_cycle" / "four_phase"
    offenders: list[str] = []
    for path in root.glob("*.py"):
        if path.name in _NOT_MODEL_LOGIC:
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


# ── §2 공식 침체는 동행 도메인 둘 이상의 확인을 요구한다 ─────────────────────


def _breadth_thresholds(minimum: int = 2) -> E.Thresholds:
    document = _thresholds().to_dict()
    document["minimum_coincident_domains"] = minimum
    return E.Thresholds(**document)


def test_one_domain_alone_cannot_declare_official_contraction() -> None:
    """총량 심각도가 아무리 커도 폭이 없으면 공식 침체가 되지 않는다."""

    thresholds = _breadth_thresholds()
    evidence = E.contraction_evidence(
        level=-3.5,
        momentum=-3.5,
        negative_level_domains=1,
        negative_momentum_domains=1,
        labor_stress_level=-3.5,
        labor_stress_momentum=-3.5,
        thresholds=thresholds,
    )
    assert evidence["contraction_evidence"] == 0.0
    assert evidence["broad_route_gate"] == 0.0
    assert evidence["rapid_route_gate"] == 0.0
    # 그 사실이 숨겨지지는 않는다. 경보 증거는 여전히 크다.
    assert evidence["alert_evidence"] > thresholds.contraction_entry


def test_two_domains_can_declare_official_contraction() -> None:
    thresholds = _breadth_thresholds()
    evidence = E.contraction_evidence(
        level=-3.5,
        momentum=-3.5,
        negative_level_domains=2,
        negative_momentum_domains=2,
        labor_stress_level=-3.5,
        labor_stress_momentum=-3.5,
        thresholds=thresholds,
    )
    assert evidence["contraction_evidence"] > thresholds.contraction_entry


def test_labour_stress_alone_still_cannot_declare_contraction() -> None:
    """노동시장은 동행 도메인이 아니다. 뒷받침 몫은 진입 문턱보다 작아야 한다."""

    thresholds = _breadth_thresholds()
    evidence = E.contraction_evidence(
        level=0.9,
        momentum=0.9,
        negative_level_domains=4,
        negative_momentum_domains=4,
        labor_stress_level=-9.0,
        labor_stress_momentum=-9.0,
        thresholds=thresholds,
    )
    assert evidence["corroboration"] == 1.0
    assert thresholds.corroboration_share < thresholds.contraction_entry


def test_confirming_domains_excludes_labour_stress() -> None:
    thresholds = _breadth_thresholds()
    level = pd.Series(
        {
            "production": -2.0,
            "employment": -2.0,
            "real_income": 0.1,
            "consumption": 0.1,
            "labor_stress": -5.0,
        }
    )
    momentum = pd.Series(dict.fromkeys(level.index, 0.0))
    assert E.confirming_coincident_domains(level, momentum, thresholds) == 2


# ── §3 침체 경보는 공식 국면과 분리된다 ──────────────────────────────────────


def test_a_concentrated_shock_raises_the_alert_without_declaring_contraction() -> None:
    thresholds = _breadth_thresholds()
    level, character = E.recession_alert(
        alert_evidence=thresholds.contraction_entry + 0.05,
        confirming_domains=1,
        thresholds=thresholds,
    )
    assert level == "high"
    assert character == "severe_but_concentrated"


def test_a_broad_shock_is_reported_as_confirmed() -> None:
    thresholds = _breadth_thresholds()
    level, character = E.recession_alert(
        alert_evidence=thresholds.contraction_entry + 0.05,
        confirming_domains=3,
        thresholds=thresholds,
    )
    assert level == "high"
    assert character == "broad_and_confirmed"


def test_weak_alert_evidence_is_preliminary_or_absent() -> None:
    thresholds = _breadth_thresholds()
    assert E.recession_alert(0.85 * thresholds.contraction_entry, 0, thresholds) == (
        "elevated",
        "preliminary",
    )
    assert E.recession_alert(0.0, 0, thresholds) == ("none", "absent")


def test_alert_is_not_a_fifth_phase() -> None:
    assert not set(C.RECESSION_ALERT) & set(C.PHASES)
    assert not set(C.ALERT_CHARACTER) & set(C.PHASES)


def test_contract_rejects_contraction_declared_from_a_concentrated_shock() -> None:
    payload = _payload()
    payload["official_current_phase"] = "contraction"
    payload["recession_alert"] = "high"
    payload["recession_alert_character"] = "severe_but_concentrated"
    with pytest.raises(C.ContractViolation):
        C.validate(payload)


def test_contract_rejects_an_unknown_alert_character() -> None:
    payload = _payload()
    payload["recession_alert_character"] = "very_bad"
    with pytest.raises(C.ContractViolation):
        C.validate(payload)


def test_contract_rejects_alert_level_and_character_disagreeing() -> None:
    payload = _payload()
    payload["recession_alert"] = "none"
    payload["recession_alert_character"] = "preliminary"
    with pytest.raises(C.ContractViolation):
        C.validate(payload)


# ── §8 확인 규칙 ─────────────────────────────────────────────────────────────


def _sequence(names: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    index = pd.date_range("2000-01-07", periods=len(names), freq="W-FRI")
    rows = []
    for name in names:
        row = dict.fromkeys(C.PHASES, 0.1)
        row[name] = 0.7
        rows.append(row)
    frame = pd.DataFrame(rows, index=index, columns=list(C.PHASES))
    quality = pd.Series(False, index=index)
    return frame, frame.copy(), quality


def test_a_one_week_challenger_does_not_move_the_official_phase() -> None:
    filtered, raw, quality = _sequence(
        ["expansion", "expansion", "slowdown", "expansion", "expansion"]
    )
    official, pending = F.confirm_transitions(filtered, raw, quality, 2, 0.30)
    assert list(official) == ["expansion"] * 5
    assert int(pending.iloc[2]) == 1


def test_a_persistent_challenger_always_wins_so_no_state_is_absorbing() -> None:
    filtered, raw, quality = _sequence(
        ["expansion", "slowdown", "slowdown", "slowdown", "slowdown"]
    )
    official, _ = F.confirm_transitions(filtered, raw, quality, 2, 0.30)
    assert official.iloc[-1] == "slowdown"
    # 확인 기간보다 오래 기다리게 만드는 경로가 없다.
    assert list(official).index("slowdown") == 2


def test_high_quality_evidence_with_a_large_margin_transitions_immediately() -> None:
    filtered, raw, quality = _sequence(["expansion", "contraction", "expansion"])
    quality.iloc[:] = True
    official, _ = F.confirm_transitions(filtered, raw, quality, 3, 0.30)
    assert official.iloc[1] == "contraction"


def test_the_confirmation_rule_is_finite_memory() -> None:
    """먼 과거가 다른 두 경로가 같은 최근 증거에서 만난다."""

    names = ["slowdown"] * 6 + ["expansion"] * 6
    filtered, raw, quality = _sequence(names)
    first, _ = F.confirm_transitions(filtered, raw, quality, 2, 0.30)
    shifted, shifted_raw, shifted_quality = _sequence(["contraction"] * 6 + names[6:])
    second, _ = F.confirm_transitions(shifted, shifted_raw, shifted_quality, 2, 0.30)
    assert first.iloc[-1] == second.iloc[-1] == "expansion"


def test_confirmation_rule_rejects_impossible_parameters() -> None:
    filtered, raw, quality = _sequence(["expansion", "expansion"])
    with pytest.raises(ValueError):
        F.confirm_transitions(filtered, raw, quality, 0, 0.30)
    with pytest.raises(ValueError):
        F.confirm_transitions(filtered, raw, quality, 2, 1.5)


# ── §4 분절 검증 ─────────────────────────────────────────────────────────────


def _truth(pattern: str) -> pd.Series:
    index = pd.date_range("2000-01-07", periods=len(pattern), freq="W-FRI")
    return pd.Series([character == "1" for character in pattern], index=index)


def test_episode_thirds_come_from_the_recession_dates_alone() -> None:
    truth = _truth("0" + "1" * 9 + "0")
    masks = V.segments(truth)
    assert int(masks["first_third"].sum()) == 3
    assert int(masks["middle_third"].sum()) == 3
    assert int(masks["final_third"].sum()) == 3
    assert (masks["core"] == masks["middle_third"]).all()
    # 두 에피소드도 각각 나뉜다. 하나로 이어 붙이지 않는다.
    two = _truth("111000111")
    assert int(V.segments(two)["middle_third"].sum()) == 2


def test_trough_adjacent_weeks_are_the_end_of_each_episode() -> None:
    truth = _truth("0" + "1" * 12 + "0")
    mask = V.trough_mask(truth)
    assert int(mask.sum()) == V.TROUGH_WEEKS
    assert bool(mask[12]) and not bool(mask[4])


def test_isolated_false_positives_are_not_a_persistent_episode() -> None:
    predicted = _truth("1010100000").to_numpy()
    truth = _truth("0000000000").to_numpy()
    summary = V.false_positive_episodes(predicted, truth)
    assert summary["false_positive_episodes"] == 3
    assert summary["longest_false_positive_episode"] == 1
    assert summary["four_week_confirmed_false_positive_episodes"] == 0
    persistent = _truth("1111100000").to_numpy()
    assert (
        V.false_positive_episodes(persistent, truth)["four_week_confirmed_false_positive_episodes"]
        == 1
    )


def test_trough_recovery_is_counted_apart_from_missing_the_core() -> None:
    truth = _truth("0" + "1" * 12 + "0")
    phase = pd.Series(
        ["expansion"] + ["contraction"] * 8 + ["recovery"] * 4 + ["expansion"],
        index=truth.index,
    )
    metrics = V.recession_metrics(phase, truth)
    assert metrics["core_recall"] == 1.0
    assert metrics["trough_adjacent_classified_recovery"] == 4
    assert metrics["non_trough_recession_weeks_classified_recovery"] == 0


def test_a_completed_sample_leaves_every_phase_it_enters() -> None:
    """건전성 불변식. 마지막 주를 차지한 국면은 아직 나올 기회가 없었을 뿐이다."""

    index = pd.date_range("2000-01-07", periods=10, freq="W-FRI")
    ending = pd.Series(["expansion"] * 2 + ["slowdown"] * 8, index=index)
    assert V.stability(ending)["unexited_phases"] == []
    assert V.stability(ending)["phase_exits"]["slowdown"] == 0
    healthy = pd.Series(["expansion", "slowdown"] * 5, index=index)
    assert V.stability(healthy)["unexited_phases"] == []
    assert V.stability(healthy)["phase_entries"]["slowdown"] == 5


def test_an_h_style_lock_shows_up_as_a_long_disagreement_run() -> None:
    """후보 H를 가둔 것은 나갈 길이 닫힌 것이 아니라 관측과 어긋난 채 버틴 것이었다."""

    index = pd.date_range("2000-01-07", periods=12, freq="W-FRI")
    official = pd.Series(["slowdown"] * 12, index=index)
    raw = pd.Series(["slowdown"] * 3 + ["recovery"] * 8 + ["slowdown"], index=index)
    assert V.longest_disagreement(raw, official) == 8
    agreeing = pd.Series(["slowdown"] * 12, index=index)
    assert V.longest_disagreement(agreeing, official) == 0


def test_signal_timing_separates_the_alert_from_the_official_phase() -> None:
    index = pd.date_range("2000-01-07", periods=10, freq="W-FRI")
    phase = pd.Series(["expansion"] * 6 + ["contraction"] * 4, index=index)
    raw = pd.Series(["expansion"] * 4 + ["contraction"] * 6, index=index)
    alert = pd.Series(["none"] * 2 + ["high"] * 8, index=index)
    timing = V.signal_timing(phase, alert, raw, index[0])
    assert timing["first_recession_alert_weeks"] == 2
    assert timing["first_raw_contraction_weeks"] == 4
    assert timing["first_official_contraction_weeks"] == 6
    assert timing["four_week_confirmed_contraction_weeks"] == 6


def test_certainty_must_not_invert_when_evidence_is_weak() -> None:
    index = pd.date_range("2000-01-07", periods=6, freq="W-FRI")
    separation = pd.Series([0.8, 0.7, 0.4, 0.3, 0.1, 0.1], index=index)
    reasons = pd.Series([0, 0, 1, 1, 2, 3], index=index)
    assert V.certainty_monotonicity(separation, reasons)["no_inversion"] is True
    inverted = pd.Series([0.1, 0.1, 0.4, 0.3, 0.9, 0.9], index=index)
    assert V.certainty_monotonicity(inverted, reasons)["no_inversion"] is False


# ── §7 프런티어 ──────────────────────────────────────────────────────────────


def _feasible_row() -> dict[str, object]:
    return {
        "single_domain_official_contraction_weeks": 0,
        "overall_recall": 0.82,
        "core_recall": 0.95,
        "false_positive_rate": 0.02,
        "two_step_transitions": 0,
        "three_week_whipsaws": 1,
        "phases_reached": list(C.PHASES),
        "unexited_phases": [],
        "longest_disagreement_run": 7,
        "certainty_no_inversion": True,
        "gfc_first_official_contraction_weeks": 7.0,
    }


def test_the_frontier_holds_the_breadth_requirement_fixed() -> None:
    assert FR.MINIMUM_COINCIDENT_DOMAINS == 2
    assert "minimum_coincident_domains" not in FR.THRESHOLD_GRID
    thresholds = FR.thresholds_from(_thresholds(), {"contraction_entry": 0.7})
    assert thresholds.minimum_coincident_domains == 2
    assert thresholds.contraction_entry == 0.7


def test_the_frontier_gates_match_the_conceptual_decision() -> None:
    assert FR.GATES["overall_recall_minimum"] == 0.80
    assert FR.GATES["core_recall_minimum"] == 0.90
    assert FR.GATES["false_positive_rate_maximum"] == 0.05
    # 금융위기는 개발구간 안에 있으므로 이 시점 요건은 홀드아웃 정보가 아니다.
    assert FR.GATES["gfc_first_official_contraction_maximum_weeks"] == 10
    assert FR.GATES["longest_disagreement_run_maximum"] == 26
    assert "2020" not in str(FR.DEVELOPMENT_EPISODES)


def test_every_mandatory_criterion_can_reject_a_combination() -> None:
    assert FR.is_feasible(_feasible_row()) is True
    for key, bad in (
        ("single_domain_official_contraction_weeks", 1),
        ("overall_recall", 0.79),
        ("core_recall", 0.89),
        ("false_positive_rate", 0.06),
        ("two_step_transitions", 6),
        ("three_week_whipsaws", 6),
        ("unexited_phases", ["recovery"]),
        ("longest_disagreement_run", 27),
        ("certainty_no_inversion", False),
        ("phases_reached", ["expansion", "slowdown", "contraction"]),
        ("gfc_first_official_contraction_weeks", 11.0),
    ):
        row = _feasible_row()
        row[key] = bad
        assert FR.is_feasible(row) is False, key


def test_the_selection_rule_is_fixed_in_code_before_results() -> None:
    keys = [name for name, _ in FR.SELECTION_ORDER]
    assert keys[0] == "four_week_confirmed_false_positive_episodes"
    assert "overall_recall" in keys
    assert not any("2020" in key or "2026" in key for key in keys)


# ── §12 최신 수정치 채택 판정이 기록되는가 ───────────────────────────────────


def _summary() -> dict[str, object]:
    path = _root() / "outputs" / "four_phase_v1_1" / "validation_summary.json"
    if not path.exists():
        pytest.skip("4국면 v1.1 산출물이 아직 생성되지 않았다")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_the_official_contraction_never_rests_on_one_domain() -> None:
    summary = _summary()
    for window in ("development", "latest_vintage"):
        assert summary[window]["single_domain_official_contraction_weeks"] == 0


def test_segmented_recall_is_reported_separately() -> None:
    summary = _summary()
    for window in ("development", "latest_vintage"):
        report = summary[window]
        for key in (
            "first_third_recall",
            "middle_third_recall",
            "final_third_recall",
            "first_half_recall",
            "core_recall",
            "overall_recall",
        ):
            assert key in report
        assert "weighted_score" not in report


def test_the_false_positive_ceiling_was_not_relaxed() -> None:
    summary = _summary()
    gates = load_config(load_settings()).gates
    assert gates["false_positive_rate_maximum"] == 0.05
    assert summary["latest_vintage"]["false_positive_rate"] <= 0.05


def test_the_stopped_stage_record_is_still_present() -> None:
    """§10. v1.0 기록을 지우지 않는다."""

    path = _root() / "outputs" / "four_phase" / "validation_summary.json"
    assert path.exists()
    stopped = json.loads(path.read_text(encoding="utf-8"))
    assert stopped["adopted"] is False
    assert stopped["latest_vintage_validation_run"] is False


# ── §2 프런티어 재현성과 기록 ────────────────────────────────────────────────


def _frontier() -> dict[str, object]:
    path = _root() / "outputs" / "four_phase_v1_1" / "development_frontier.json"
    if not path.exists():
        pytest.skip("프런티어 산출물이 아직 생성되지 않았다")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_the_frontier_artifact_records_everything_needed_to_rerun_it() -> None:
    document = _frontier()
    for key in (
        "source_commit",
        "generated_at_utc",
        "configuration_base",
        "development_window",
        "warmup_window",
        "threshold_grid",
        "filter_grid",
        "stage_one_points",
        "combinations_evaluated",
        "rejected_by_gate",
        "feasible_combinations",
        "gates",
        "selection_rule_sha256",
        "selection_order",
        "selected",
        "selected_rank_by_metric",
        "pareto",
    ):
        assert key in document, key
    assert document["configuration_base"] == STOPPED_CONFIG_NAME
    assert document["development_window"] == ["1995-01-01", "2012-12-31"]


def test_the_selection_rule_hash_still_matches_the_recorded_one() -> None:
    """결과를 본 뒤 선택 규칙이나 격자가 바뀌지 않았음을 지문으로 확인한다."""

    assert _frontier()["selection_rule_sha256"] == FR.selection_rule_digest()


def test_the_selected_combination_reproduces_from_committed_source() -> None:
    """산출물의 선택 행을 생산 코드로 다시 계산해 같은 값이 나오는지."""

    selected = dict(_frontier()["selected"])  # type: ignore[arg-type]
    inputs = FR.load_inputs()
    overrides = {name: selected[name] for name in FR.THRESHOLD_GRID}
    observation = FR.observation_layer(
        inputs.prepared,
        FR.thresholds_from(inputs.config.thresholds, overrides),
        inputs.config.stale_weeks,
    )
    row = FR.assess(
        inputs,
        observation,
        float(selected["lam"]),  # type: ignore[arg-type]
        float(selected["epsilon"]),  # type: ignore[arg-type]
        int(selected["confirmation_weeks"]),  # type: ignore[arg-type]
        float(selected["immediate_margin"]),  # type: ignore[arg-type]
        inputs.config.separation_floor,
    )
    for key in (
        "overall_recall",
        "core_recall",
        "false_positive_rate",
        "two_step_transitions",
        "three_week_whipsaws",
        "gfc_first_official_contraction_weeks",
        "longest_disagreement_run",
        "four_week_confirmed_false_positive_episodes",
        "median_transition_delay_weeks",
        "maximum_transition_delay_weeks",
    ):
        assert row[key] == selected[key], key
    assert FR.failed_gates(row) == []


def test_the_frozen_configuration_matches_the_selected_combination() -> None:
    """동결 설정이 프런티어가 고른 조합과 같은지. 손으로 옮겨 적다 어긋나면 잡는다."""

    selected = dict(_frontier()["selected"])  # type: ignore[arg-type]
    document = load_config(load_settings()).document
    thresholds = document["thresholds"]
    for name in FR.THRESHOLD_GRID:
        assert thresholds[name] == selected[name], name
    soft = document["soft_filter"]
    assert soft["lambda"] == selected["lam"]
    assert soft["epsilon"] == selected["epsilon"]
    assert soft["confirmation_weeks"] == selected["confirmation_weeks"]
    assert soft["immediate_margin"] == selected["immediate_margin"]


def test_every_gate_can_reject_and_rejections_are_counted() -> None:
    row = _feasible_row()
    assert FR.failed_gates(row) == []
    row["overall_recall"] = 0.5
    row["core_recall"] = 0.5
    assert set(FR.failed_gates(row)) == {"overall_recall", "core_recall"}


# ── §9 표본 역할 분리 ────────────────────────────────────────────────────────


def test_sample_roles_are_labelled_and_the_gfc_is_not_called_out_of_sample() -> None:
    roles = dict(_summary()["sample_roles"])  # type: ignore[arg-type]
    assert roles["development"]["out_of_sample"] is False
    assert roles["development"]["window"] == ["1995-01-01", "2012-12-31"]
    assert roles["holdout_latest_vintage"]["out_of_sample"] is True
    assert roles["holdout_latest_vintage"]["window"][0].startswith("2013")
    # 전체 표본 요약은 운영 요약이지 순수 표본 밖 성적이 아니다.
    assert roles["full_causal_latest_vintage"]["out_of_sample"] is False
    assert roles["strict_alfred"]["window"][0] == "2013-06-14"


def test_development_holdout_and_full_sample_are_reported_separately() -> None:
    summary = _summary()
    for key in ("development", "holdout", "latest_vintage"):
        assert key in summary
    assert summary["development"]["weeks"] != summary["holdout"]["weeks"]


# ── §5 확인 규칙 지연 ────────────────────────────────────────────────────────


def test_the_confirmation_rule_does_not_recreate_a_state_lock() -> None:
    audit = dict(_summary()["confirmation_rule"])  # type: ignore[arg-type]
    assert audit["recreates_a_state_lock"] is False
    assert audit["weeks_stalled_beyond_the_rule"] == 0
    assert audit["maximum_delay_weeks"] <= int(audit["confirmation_weeks"])
    assert audit["immediate_transitions"] > 0
    assert audit["longest_disagreement_run"] <= 26


def test_transition_delays_never_exceed_the_confirmation_period() -> None:
    """도전자가 확인 기간만 버티면 반드시 이긴다. 그보다 오래 기다리는 경로가 없다."""

    index = pd.date_range("2000-01-07", periods=8, freq="W-FRI")
    winner = pd.Series(["expansion"] + ["slowdown"] * 7, index=index)
    official = pd.Series(["expansion"] * 3 + ["slowdown"] * 5, index=index)
    delays = V.transition_delays(winner, official)
    assert [item["delay_weeks"] for item in delays] == [2]
    summary = V.delay_summary(delays)
    assert summary["confirmed_transitions"] == 1
    assert summary["immediate_transitions"] == 0
    assert summary["maximum_delay_weeks"] == 2


# ── §6 지속된 오탐 구간 ──────────────────────────────────────────────────────


def test_persistent_false_contraction_episodes_are_listed_not_averaged() -> None:
    audit = dict(_summary()["false_positive_episodes"])  # type: ignore[arg-type]
    assert "by_kind" in audit
    assert audit["episodes"] >= audit["four_week_confirmed_episodes"]
    detail = json.loads(
        (_root() / "outputs" / "four_phase_v1_1" / "false_positive_audit.json").read_text(
            encoding="utf-8"
        )
    )["detail"]
    assert len(detail) == audit["episodes"]
    for episode in detail:
        for key in (
            "start_date",
            "end_date",
            "duration_weeks",
            "kind",
            "confirming_domains",
            "concentration",
            "dominant_domains",
            "recession_alert",
            "in_late_2019",
            "after_2022",
        ):
            assert key in episode, key


def test_adoption_requires_zero_confirmed_contraction_in_late_2019_and_after_2022() -> None:
    """채택을 주장하려면 이 둘이 0이어야 한다. 실패는 산출물에 남고 테스트를 붉히지 않는다.

    게이트 실패 자체를 테스트 실패로 만들면, 모델이 기준에 못 미쳤다는 사실과 코드가
    깨졌다는 사실이 같은 신호로 섞인다. 그래서 여기서는 **불변식**을 검사한다 —
    채택했다고 적혀 있다면 그 두 값은 반드시 0이다.
    """

    summary = _summary()
    latest = dict(summary["latest_vintage"])  # type: ignore[arg-type]
    checks = dict(dict(summary["adoption_gates"])["checks"])  # type: ignore[arg-type]
    assert checks["late_2019_confirmed_contraction"] == (
        latest["late_2019_confirmed_contraction_weeks"] == 0
    )
    assert checks["post_2022_confirmed_contraction"] == (
        latest["post_2022_confirmed_contraction_weeks"] == 0
    )
    if summary["adopted"]:
        assert latest["late_2019_confirmed_contraction_weeks"] == 0
        assert latest["post_2022_confirmed_contraction_weeks"] == 0


def test_adoption_is_consistent_with_every_mandatory_gate() -> None:
    """채택 여부가 게이트 판정과 어긋나면 안 된다. 하나라도 실패하면 채택일 수 없다."""

    summary = _summary()
    checks = dict(dict(summary["adoption_gates"])["checks"])  # type: ignore[arg-type]
    assert dict(summary["adoption_gates"])["passed"] == all(checks.values())
    if not all(checks.values()):
        assert summary["adopted"] is False
        assert summary["strict_alfred"] is None
        assert "게이트" in str(summary["strict_alfred_reason"])


# ── §4 경보 분리 ─────────────────────────────────────────────────────────────


def test_the_alert_behaves_as_the_development_diagnosis_expects() -> None:
    audit = dict(_summary()["recession_alert"])  # type: ignore[arg-type]
    assert audit["high_share_of_quiet_weeks"] < 0.05
    assert audit["high_share_of_recession_weeks"] > 0.5
    # 경보는 공식 국면을 결정하지 않는다. 두 방향 모두 실제로 일어난다는 것이 그 증거다.
    assert audit["high_alert_without_official_contraction"] > 0
    assert audit["concentrated_alert_declared_official_contraction"] == 0


def test_the_alert_is_not_a_phase_and_cannot_change_the_official_reading() -> None:
    reading = json.loads(
        (_root() / "outputs" / "four_phase_v1_1" / "current_reading.json").read_text(
            encoding="utf-8"
        )
    )
    assert reading["official_current_phase"] in C.PHASES
    assert reading["recession_alert"] in C.RECESSION_ALERT
    assert reading["recession_alert"] not in C.PHASES
    assert reading["recession_alert_character"] in C.ALERT_CHARACTER


# ── §11 ALFRED 캐시 완결성 ───────────────────────────────────────────────────


def test_the_alfred_cache_covers_the_strict_window_without_substitution() -> None:
    audit = dict(_summary()["alfred_cache"])  # type: ignore[arg-type]
    assert audit["network_used"] is False
    assert audit["api_key_used"] is False
    assert audit["latest_vintage_substitution"] is False
    assert audit["duplicate_as_of_dates"] == 0
    assert audit["missing_as_of_dates"] == []
    assert audit["expected_as_of_dates"] == audit["actual_as_of_dates"]
    assert audit["first_week"] == "2013-06-14"
    coverage = dict(audit["cache_coverage"])  # type: ignore[arg-type]
    for series_id, detail in dict(coverage["series"]).items():  # type: ignore[arg-type]
        assert detail["cached"] is True, series_id
        assert detail["covers_window"] is True, series_id


def test_the_strict_window_starts_where_every_series_has_a_real_vintage() -> None:
    assert str(AL.STRICT_START.date()) == "2013-06-14"
    settings = load_settings()
    frames = AL.cached_frames(settings)
    latest_first = max(frame["realtime_start"].min() for frame in frames.values())
    assert pd.Timestamp(latest_first) <= AL.STRICT_START


# ── 미래 정보 금지 ───────────────────────────────────────────────────────────


def test_the_scoring_path_never_reads_a_later_week() -> None:
    """앞부분만 잘라 돌려도 그 구간의 판정이 그대로여야 한다. 미래를 보지 않는다는 뜻이다."""

    settings = load_settings()
    config = load_config(settings)
    frozen = load_baseline("candidate_h_breadth_gate", settings)
    core, _ = load_core_observations(settings)
    full = score(prepare(core, frozen, pd.Timestamp("2026-08-14"), config), config)
    cut = pd.Timestamp("2015-12-25")
    short = score(prepare(core, frozen, cut, config), config)
    shared = short.official_phase.index
    assert len(shared) > 500
    assert (full.official_phase.reindex(shared) == short.official_phase).all()


# ── 캐시 보유와 신선도는 다른 사실이다 ───────────────────────────────────────


def _panel(
    weeks: int,
    ages: dict[str, float],
    arrivals: dict[str, list[bool]] | None = None,
) -> tuple[pd.DatetimeIndex, pd.DataFrame, pd.DataFrame]:
    """마지막 주의 도메인 나이와 주별 도착 여부만 있는 최소 패널."""

    index = pd.date_range("2025-01-03", periods=weeks, freq="W-FRI")
    age = pd.DataFrame({domain: [value] * weeks for domain, value in ages.items()}, index=index)
    if arrivals is None:
        arrivals = {domain: [True] * weeks for domain in ages}
    arrived = pd.DataFrame(arrivals, index=index)
    return index, age, arrived


def _policy() -> FRESH.FreshnessPolicy:
    return load_config(load_settings()).freshness


_NORMAL_AGES = {
    "production": 3.0,
    "employment": 2.0,
    "real_income": 4.0,
    "consumption": 3.0,
    "labor_stress": 0.0,
}


def test_a_cached_snapshot_does_not_by_itself_make_a_week_eligible() -> None:
    """캐시에 값이 있어도 as-of 시점 기준으로 낡았으면 공식 판정을 낼 수 없다."""

    index, age, arrived = _panel(20, _NORMAL_AGES)
    policy = _policy()
    live = FRESH.evaluate(index[-1], index, age, arrived, policy)
    assert live.status == "official"
    # 같은 캐시, 같은 값. as-of만 열 주 뒤로 옮긴다.
    stale = FRESH.evaluate(index[-1] + pd.Timedelta(weeks=10), index, age, arrived, policy)
    assert stale.information_lag_weeks == 10
    assert stale.status == "withheld"
    assert stale.fresh_coincident_domains == 0


def test_the_eligible_week_count_can_be_smaller_than_the_cached_week_count() -> None:
    audit = dict(_summary()["alfred_cache"])  # type: ignore[arg-type]
    strict = _summary().get("strict_alfred")
    if not strict:
        pytest.skip("엄격 ALFRED를 아직 실행하지 않았다")
    counts = dict(dict(strict)["phase_eligibility"])  # type: ignore[arg-type]
    total = counts["official_weeks"] + counts["preliminary_weeks"] + counts["withheld_weeks"]
    assert total == audit["expected_as_of_dates"]
    assert counts["official_weeks"] <= audit["expected_as_of_dates"]


def test_a_carried_forward_value_is_distinguishable_from_a_new_release() -> None:
    ages = dict(_NORMAL_AGES)
    index, age, arrived = _panel(
        6,
        ages,
        arrivals={
            "production": [False] * 6,
            "employment": [False] * 6,
            "real_income": [False] * 6,
            "consumption": [False] * 6,
            "labor_stress": [True] * 6,
        },
    )
    result = FRESH.evaluate(index[-1], index, age, arrived, _policy())
    assert result.domain_carried_forward["labor_stress"] is False
    assert result.domain_carried_forward["production"] is True


def test_an_all_domain_publication_pause_produces_withheld() -> None:
    """모든 도메인이 멈춰 서면, 캐시에 값이 남아 있어도 공식 판정을 내지 않는다."""

    index, age, arrived = _panel(12, _NORMAL_AGES)
    policy = _policy()
    paused = FRESH.evaluate(index[-1] + pd.Timedelta(weeks=6), index, age, arrived, policy)
    assert paused.weeks_since_any_new_observation > policy.panel_silent_withhold_weeks
    assert paused.status == "withheld"


def test_partial_staleness_produces_preliminary_not_withheld() -> None:
    ages = dict(_NORMAL_AGES)
    ages["employment"] = 9.0  # 한 도메인만 문턱을 넘는다
    index, age, arrived = _panel(12, ages)
    result = FRESH.evaluate(index[-1], index, age, arrived, _policy())
    assert result.stale_domains == ["employment"]
    assert result.fresh_coincident_domains == 3
    assert result.status == "preliminary"
    assert result.official is False and result.withheld is False


def test_normal_publication_delay_does_not_withhold() -> None:
    """월간 지표가 정상 일정대로 몇 주 늦는 것만으로 보류하지 않는다."""

    index, age, arrived = _panel(12, _NORMAL_AGES)
    policy = _policy()
    one_week_late = FRESH.evaluate(index[-1] + pd.Timedelta(weeks=1), index, age, arrived, policy)
    assert one_week_late.status == "official"
    assert one_week_late.weeks_since_any_new_observation <= policy.panel_silent_grace_weeks


def test_status_logic_cannot_change_the_raw_measurements() -> None:
    """상태 판정은 라벨일 뿐이다. 원시 경제 측정값을 건드리지 않는다."""

    settings = load_settings()
    config = load_config(settings)
    frames = AL.cached_frames(settings)
    baseline = load_baseline("candidate_h_breadth_gate", settings)
    withheld = AL._week(settings, baseline, frames, config, pd.Timestamp("2025-11-14"))
    assert withheld.phase_status == "withheld"
    assert withheld.official_phase == ""
    # 원시 국면과 총량 측정값은 그대로 남는다.
    assert withheld.raw_phase in C.PHASES
    assert np.isfinite(withheld.activity_level)
    assert np.isfinite(withheld.activity_momentum)


def test_status_does_not_overwrite_the_phase_while_the_week_stays_eligible() -> None:
    settings = load_settings()
    config = load_config(settings)
    frames = AL.cached_frames(settings)
    baseline = load_baseline("candidate_h_breadth_gate", settings)
    eligible = AL._week(settings, baseline, frames, config, pd.Timestamp("2025-10-10"))
    assert eligible.phase_status == "preliminary"
    assert eligible.official_phase in C.PHASES


def test_the_disputed_2025_weeks_reproduce_consistently() -> None:
    """2025년 발표 중단 구간이 매번 같은 상태로 재현되는지."""

    settings = load_settings()
    config = load_config(settings)
    frames = AL.cached_frames(settings)
    baseline = load_baseline("candidate_h_breadth_gate", settings)
    expected = {
        "2025-10-03": "official",
        "2025-10-10": "preliminary",
        "2025-10-17": "preliminary",
        "2025-10-24": "withheld",
        "2025-10-31": "withheld",
        "2025-11-07": "withheld",
        "2025-11-14": "withheld",
    }
    for as_of, status in expected.items():
        result = AL._week(settings, baseline, frames, config, pd.Timestamp(as_of))
        assert result.phase_status == status, as_of


def test_a_later_vintage_never_enters_an_earlier_snapshot() -> None:
    """as-of 이후에 시작된 판본은 그 시점 스냅샷에 들어오지 않는다."""

    settings = load_settings()
    frames = AL.cached_frames(settings)
    as_of = pd.Timestamp("2019-06-28")
    for series_id, frame in frames.items():
        visible = slice_vintage(frame, as_of)
        assert (visible["realtime_start"] <= as_of).all(), series_id
        assert (visible["realtime_end"] >= as_of).all(), series_id
        latest_now = frame["realtime_start"].max()
        assert visible["realtime_start"].max() <= as_of < latest_now, series_id


def test_the_cache_audit_and_the_runner_share_one_policy() -> None:
    """캐시 감사·엄격 러너·보고서가 같은 정책 객체를 쓴다. 세 곳에 따로 두지 않는다."""

    config = load_config(load_settings())
    policy = config.freshness
    document = config.document["freshness_policy"]
    assert policy.domain_stale_weeks == document["domain_stale_weeks"]
    assert policy.domain_stale_weeks == config.stale_weeks
    assert policy.panel_silent_grace_weeks == document["panel_silent_grace_weeks"]
    assert policy.panel_silent_withhold_weeks == document["panel_silent_withhold_weeks"]
    assert policy.minimum_fresh_coincident_domains == (config.thresholds.minimum_coincident_domains)
    assert set(FRESH.STATUS) == set(C.PHASE_STATUS)
    for forbidden in ("future_vintages", "latest_value_substitution", "backward_fill"):
        assert forbidden in document["never_used"]


def test_the_cache_audit_separates_coverage_from_publication_activity() -> None:
    audit = dict(_summary()["alfred_cache"])  # type: ignore[arg-type]
    assert "cache_coverage" in audit
    assert "new_vintage_activity" in audit
    activity = dict(audit["new_vintage_activity"])  # type: ignore[arg-type]
    # 2025년 가을의 전면 중단이 실제로 기록돼 있어야 한다.
    assert activity["longest_all_series_publication_pause_weeks"] >= 5
    assert audit["latest_vintage_substitution"] is False
    assert audit["backward_fill_used"] is False
