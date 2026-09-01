"""후보 J: 유계 총량, 발표 인식 모멘텀, 계층 분류, 거리 인식 필터."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.candidate_j import aggregate as A
from business_cycle.candidate_j import filters as F
from business_cycle.candidate_j import hierarchy as H
from business_cycle.candidate_j.engine import load_config
from business_cycle.config import load_settings


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _thresholds() -> H.MajorThresholds:
    return load_config(load_settings()).thresholds


def _cuts() -> H.SubphaseCuts:
    return load_config(load_settings()).cuts


# ── §5 유계 등가중 총량 ──────────────────────────────────────────────────────


def _frame(values: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=1, freq="W-FRI"))
    from business_cycle.current_state.domains import DOMAINS

    return pd.DataFrame([dict(zip(DOMAINS, values, strict=True))], index=index)


def test_bounded_mean_keeps_every_domain_unlike_the_median() -> None:
    """중앙값은 다섯 값 중 하나만 쓴다. 유계 평균은 다섯을 모두 쓴다."""

    frame = _frame([1.0, 2.0, 3.0, 4.0, 5.0])
    result = A.bounded_mean(frame, 10.0)
    assert result.aggregate.iloc[0] == pytest.approx(3.0)
    shifted = _frame([1.0, 2.0, 3.0, 4.0, 9.0])
    # 중앙값이라면 3.0으로 같지만, 유계 평균은 다섯째 도메인의 변화를 반영한다.
    assert shifted.median(axis=1).iloc[0] == pytest.approx(3.0)
    assert A.bounded_mean(shifted, 10.0).aggregate.iloc[0] > 3.0


def test_one_extreme_domain_cannot_dominate_the_bounded_aggregate() -> None:
    calm = _frame([0.1, 0.2, 0.1, 0.2, 0.1])
    extreme = _frame([0.1, 0.2, -40.0, 0.2, 0.1])
    cap = 3.7763
    bounded = A.bounded_mean(extreme, cap).aggregate.iloc[0]
    unbounded = extreme.mean(axis=1).iloc[0]
    assert unbounded < -7.0
    assert bounded == pytest.approx((0.1 + 0.2 - cap + 0.2 + 0.1) / 5.0)
    assert bounded > A.bounded_mean(calm, cap).aggregate.iloc[0] - 1.0


def test_multiple_domains_together_can_still_produce_an_extreme_aggregate() -> None:
    """진짜 위기는 여러 도메인이 함께 극단이다. 상한이 그것을 평범하게 만들면 안 된다."""

    cap = 3.7763
    crisis = _frame([-4.0, -4.0, -4.0, -4.0, -4.0])
    assert A.bounded_mean(crisis, cap).aggregate.iloc[0] == pytest.approx(-cap)


def test_cap_must_be_positive() -> None:
    with pytest.raises(ValueError):
        A.bounded_mean(_frame([1.0] * 5), 0.0)


# ── §6 발표 인식 모멘텀 ──────────────────────────────────────────────────────


def test_a_week_without_a_release_does_not_force_zero_momentum() -> None:
    """새 소식이 없는 주를 '모멘텀 0'으로 바꾸면 없는 신호를 만드는 것이다."""

    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=12, freq="W-FRI"))
    levels = pd.DataFrame({"production": [0.0] * 4 + [1.0] * 8}, index=index)
    arrived = pd.DataFrame({"production": [False] * 4 + [True] + [False] * 7}, index=index)
    momentum = A.release_aware_momentum(levels, arrived, 4)
    tail = momentum["production"].iloc[5:]
    assert (tail.abs() > 0).all(), "발표가 없는 주에 모멘텀이 0으로 떨어졌다"
    assert tail.nunique() == 1, "발표가 없는 사이에는 마지막 추정을 유지해야 한다"


def test_momentum_updates_only_when_a_release_arrives() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=10, freq="W-FRI"))
    # 2주 차분이 주마다 달라지도록 가속하는 수준을 쓴다. 선형이면 차분이 상수라
    # 발표 주에도 값이 같아 시험이 성립하지 않는다.
    levels = pd.DataFrame({"production": np.arange(10, dtype=float) ** 2}, index=index)
    arrived = pd.DataFrame({"production": [i % 4 == 0 for i in range(10)]}, index=index)
    momentum = A.release_aware_momentum(levels, arrived, 2)
    values = momentum["production"]
    assert values.iloc[5] == values.iloc[4], "발표가 없는 주에 값이 바뀌었다"
    assert values.iloc[6] == values.iloc[4], "발표 사이에는 마지막 추정을 유지해야 한다"
    assert values.iloc[8] != values.iloc[7], "발표 주에 갱신되지 않았다"


def test_weeks_since_release_counts_up_between_releases() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=6, freq="W-FRI"))
    arrived = pd.DataFrame({"employment": [True, False, False, True, False, False]}, index=index)
    age = A.weeks_since_release(arrived)["employment"].tolist()
    assert age == [0.0, 1.0, 2.0, 0.0, 1.0, 2.0]


# ── §7·§8 계층 분류와 침체 증거 ──────────────────────────────────────────────


def test_major_scores_sum_to_one_and_are_never_zero() -> None:
    thresholds = _thresholds()
    for level, momentum in ((0.0, 0.0), (3.0, 3.0), (-3.0, -3.0), (-0.2, 0.3)):
        scores = H.major_scores(level, momentum, 2, 2, 0.0, thresholds)
        assert pytest.approx(sum(scores.values()), rel=1e-6) == 1.0
        assert min(scores.values()) > 0.0


def test_labor_stress_alone_cannot_produce_contraction_evidence() -> None:
    """§8: 어느 경로도 노동시장 스트레스만으로 성립하지 않는다."""

    thresholds = _thresholds()
    evidence = H.contraction_evidence(0.1, 0.1, 0, 0, -5.0, thresholds)
    assert evidence["contraction_evidence"] == 0.0
    assert evidence["broad_level_route"] == 0.0
    assert evidence["rapid_deterioration_route"] == 0.0


def test_two_contraction_routes_exist_and_are_separately_satisfiable() -> None:
    thresholds = _thresholds()
    broad = H.contraction_evidence(-2.5, -0.6, 4, 2, -1.0, thresholds)
    rapid = H.contraction_evidence(-0.4, -2.0, 1, 4, -2.0, thresholds)
    assert broad["broad_level_route"] > 0.0
    assert rapid["rapid_deterioration_route"] > 0.0


def test_subphase_scores_stay_within_the_selected_major() -> None:
    """다른 대국면의 하위국면 점수와 직접 경쟁하지 않는다."""

    cuts = _cuts()
    scores = H.subphase_scores("slowdown", 0.4, -0.5, 2, 2, cuts)
    assert set(scores) == set(H.SUBPHASES)
    assert pytest.approx(sum(scores.values()), rel=1e-6) == 1.0
    assert min(scores.values()) > 0.0


def test_slowdown_progression_is_ordered_by_current_severity() -> None:
    mild = H.progression("slowdown", 0.8, -0.1, 0, 0)
    clear = H.progression("slowdown", 0.3, -0.4, 1, 1)
    severe = H.progression("slowdown", -0.2, -0.6, 2, 3)
    assert mild < clear < severe


def test_contraction_progression_treats_improving_momentum_as_later() -> None:
    """침체 말기는 악화가 잦아드는 상태다. 가장 깊은 순간이 아니다."""

    deepening = H.progression("contraction", -2.0, -1.5, 4, 4)
    exhausting = H.progression("contraction", -2.0, 0.3, 4, 1)
    assert exhausting > deepening


# ── §10 거리 인식 소프트 필터 ────────────────────────────────────────────────


def test_transition_matrix_is_strictly_positive_and_ergodic() -> None:
    matrix = F.transition_matrix(4, 1.5, 0.01)
    assert (matrix > 0).all()
    assert F.is_ergodic(matrix)
    assert np.allclose(matrix.sum(axis=1), 1.0)


def test_transition_weight_decreases_with_cycle_distance() -> None:
    matrix = F.transition_matrix(4, 1.5, 0.01)
    # recovery(0) 기준: 자기 자신 > 인접 > 반대편
    assert matrix[0, 0] > matrix[0, 1]
    assert matrix[0, 1] > matrix[0, 2]
    assert matrix[0, 1] == pytest.approx(matrix[0, 3]), "순환에서 좌우 인접은 같은 거리다"


def test_zero_epsilon_is_rejected() -> None:
    """0은 되돌릴 수 없다. 후보 H를 133주 가둔 것이 정확히 0이었다."""

    with pytest.raises(ValueError, match="epsilon"):
        F.transition_matrix(4, 1.5, 0.0)


def test_reverse_movement_remains_possible() -> None:
    matrix = F.transition_matrix(4, 1.5, 0.01)
    # slowdown(2) → expansion(1) 은 순환 반대 방향이지만 여전히 양수다.
    assert matrix[2, 1] > 0.0


def test_persistent_evidence_overcomes_the_filter_from_any_start() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=60, freq="W-FRI"))
    scores = pd.DataFrame(
        {"recovery": 0.02, "expansion": 0.92, "slowdown": 0.04, "contraction": 0.02},
        index=index,
    )
    matrix = F.transition_matrix(4, 1.5, 0.01)
    for start in range(4):
        prior = np.full(4, 1e-9)
        prior[start] = 1.0
        prior = prior / prior.sum()
        for likelihood in scores.to_numpy(dtype=float):
            posterior = (prior @ matrix) * likelihood
            prior = posterior / float(posterior.sum())
        assert int(np.argmax(prior)) == 1, f"{start}에서 출발해 빠져나오지 못했다"


def test_finite_memory_converges_within_the_locked_window() -> None:
    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=26, freq="W-FRI"))
    scores = pd.DataFrame(
        {"recovery": 0.05, "expansion": 0.10, "slowdown": 0.80, "contraction": 0.05},
        index=index,
    )
    result = F.convergence(scores, 1.5, 0.01, (4, 13, 26))
    assert result["after_13_weeks"]["converged"] is True
    assert result["after_26_weeks"]["converged"] is True


def test_subphase_is_not_forced_to_early_when_the_major_changes() -> None:
    """§10: 새 대국면의 하위국면은 관측 증거로 초기화한다."""

    index = pd.DatetimeIndex(pd.date_range("2026-01-02", periods=6, freq="W-FRI"))
    majors = pd.DataFrame(
        {
            "recovery": [0.05] * 3 + [0.80] * 3,
            "expansion": [0.05] * 6,
            "slowdown": [0.85] * 3 + [0.10] * 3,
            "contraction": [0.05] * 6,
        },
        index=index,
    )
    subs = {
        major: pd.DataFrame(
            {"early": [0.05] * 6, "middle": [0.10] * 6, "late": [0.85] * 6}, index=index
        )
        for major in H.MAJORS
    }
    result = F.hierarchical_official(majors, subs, 1.5, 1.5, 0.01)
    assert result["official_major_phase"].iloc[-1] == "recovery"
    assert result["official_subphase"].iloc[-1] == "late"


# ── §13 동결 설정 ────────────────────────────────────────────────────────────


def test_frozen_config_hash_matches_the_recorded_snapshot() -> None:
    settings = load_settings()
    config = load_config(settings)
    recorded = (
        (_root() / "outputs" / "candidate_j" / "frozen_candidate_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert config.sha256 == recorded
    assert config.document["candidate"] == "candidate_j_hierarchical"


def test_config_rejects_fabricated_zero_between_releases() -> None:
    settings = load_settings()
    document = dict(load_config(settings).document)
    assert document["release_carry"]["fabricate_zero_between_releases"] is False


def test_only_three_transition_parameters_exist() -> None:
    """§12: 전이 모수는 셋뿐이다. 국면마다 따로 두지 않는다."""

    settings = load_settings()
    soft = load_config(settings).document["soft_filter"]
    assert set(soft) == {"lambda_major", "lambda_subphase", "epsilon"}


def test_inherited_decisions_are_recorded() -> None:
    settings = load_settings()
    inherited = load_config(settings).document["inherited_decisions"]
    assert inherited["momentum_scale_method"] == "rolling_mad"
    assert inherited["xy_role"] == "diagnostic_and_visualisation_only"
    assert inherited["radius_075_removed_from_transition_control"] is True
    assert "RRSFS" in inherited["no_additional_deflation"]


# ── §16 날짜 과적합 방지 ─────────────────────────────────────────────────────

_DATE = re.compile(r"""["'](19|20)\d{2}-\d{2}-\d{2}["']""")


def test_no_date_literals_in_candidate_i_or_j_model_logic() -> None:
    """모델·분류기·전이·설정 로직에는 특정 날짜가 없어야 한다."""

    root = _root() / "src" / "business_cycle"
    reporting = {
        ("current_state", "validation.py"),
        ("current_state", "report.py"),
        ("candidate_j", "report.py"),
    }
    offenders: list[str] = []
    for package in ("current_state", "candidate_j"):
        for path in (root / package).glob("*.py"):
            if path.relative_to(root).parts in reporting:
                continue
            for match in _DATE.finditer(path.read_text(encoding="utf-8")):
                offenders.append(f"{package}/{path.name}: {match.group(0)}")
    assert not offenders, offenders


def test_production_classification_cannot_branch_on_a_calendar_date() -> None:
    """같은 증거는 날짜와 무관하게 같은 결과를 낸다."""

    thresholds = _thresholds()
    cuts = _cuts()
    first = H.major_scores(-1.2, -0.9, 3, 3, -0.5, thresholds)
    second = H.major_scores(-1.2, -0.9, 3, 3, -0.5, thresholds)
    assert first == second
    assert H.subphase_scores("contraction", -1.2, -0.9, 3, 3, cuts) == H.subphase_scores(
        "contraction", -1.2, -0.9, 3, 3, cuts
    )


def test_evaluation_modules_may_document_historical_event_windows() -> None:
    """평가 모듈의 사례 창은 허용된다. 그 사실을 검사로 못박아 둔다."""

    path = _root() / "src" / "business_cycle" / "current_state" / "validation.py"
    assert _DATE.search(path.read_text(encoding="utf-8")) is not None


# ── 기존 산출물 보존 ─────────────────────────────────────────────────────────


def test_candidate_h_i_and_interpretation_artifacts_are_unchanged() -> None:
    summary_h = json.loads(
        (
            _root() / "outputs" / "robustness_validation" / "phase6" / "validation_summary.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        summary_h["frozen_hash"]
        == "c367e2a0f8e907b6f927191f03379bab5ea5eace6b671454c4b63e44d4b2bb21"
    )
    summary_i = json.loads(
        (_root() / "outputs" / "current_state" / "validation_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary_i["adopted"] is False
    assert (
        summary_i["frozen_config_sha256"]
        == "765e2ee65b70a185159faa928c2df9c734c19e583dc8655ae47c80ec3d056993"
    )
    interpretation = json.loads(
        (_root() / "outputs" / "phase_interpretation" / "validation_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert interpretation["core_model_parity"] is True


def test_candidate_j_result_is_recorded_including_failure() -> None:
    path = _root() / "outputs" / "candidate_j" / "validation_summary.json"
    if not path.exists():
        pytest.skip("후보 J 산출물이 아직 생성되지 않았다")
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["candidate"] == "candidate_j_hierarchical"
    # 구조 결함이 실제로 고쳐졌다는 것도 함께 남는다.
    assert summary["soft_filter"]["major_matrix_ergodic"] is True
    assert summary["soft_filter"]["subphase_matrix_ergodic"] is True
    assert summary["release_carry"]["domain_weeks_with_exact_zero_momentum"] == 0
    assert summary["phases_reachable"] == 12
    assert summary["minimum_major_score"] > 0
    assert all(v["monotonic"] is True for v in summary["subphase_monotonicity"].values())
    if not summary["adopted"]:
        assert summary["failed_gates"]
        assert summary["strict_alfred"]["run"] is False
