"""사전 명세의 계약 — **결과보다 먼저 커밋됐는가.**

트랙 23과 같은 강제다. 규칙이 결과와 같은 커밋에 들어오면 그것은 사전 명세가 아니다.

두 번째 계약은 문턱이 **새로 세워졌는가**다. 순환매 관문을 그대로 가져오면 비용 구조가
다른 질문에 남의 산술을 쓰는 것이 된다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from business_cycle.market_risk import prespec
from business_cycle.rotation_rerun import prespec as rotation

ROOT = Path(__file__).resolve().parents[1]
PRESPEC = "src/business_cycle/market_risk/prespec.py"
RESULTS = "outputs/market_risk/validation_summary.json"


def _added_commit(path: str) -> str | None:
    result = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.split("\n") if line.strip()]
    return lines[-1] if lines else None


def test_the_prespec_is_committed_before_the_results() -> None:
    prespec_commit = _added_commit(PRESPEC)
    if prespec_commit is None:
        assert _added_commit(RESULTS) is None, "결과가 규칙보다 먼저 커밋됐다"
        return

    results_commit = _added_commit(RESULTS)
    if results_commit is None:
        return
    assert results_commit != prespec_commit, "규칙과 결과가 같은 커밋에 들어왔다"
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", prespec_commit, results_commit],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert ancestor.returncode == 0, "규칙 커밋이 결과 커밋의 조상이 아니다"


def test_the_prespec_commit_carries_no_results() -> None:
    commit = _added_commit(PRESPEC)
    if commit is None:
        return
    listed = subprocess.run(
        ["git", "show", "--name-only", "--format=", commit],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    touched = [line.strip() for line in listed.stdout.split("\n") if line.strip()]
    assert not any("outputs/market_risk" in name for name in touched), touched


# ── 문턱이 새로 세워졌는가 ──────────────────────────────────────────────────


def test_the_threshold_is_not_imported_from_the_rotation_gate() -> None:
    """비용 구조가 다른 질문에 남의 산술을 쓰면 안 된다."""

    text = prespec.rule()["why_the_threshold_differs_from_the_rotation_gate"]
    assert "비용은 구속 조건이 아니다" in text
    assert not hasattr(prespec, "CEILING_FLOOR_ANNUAL")
    assert prespec.DOWNSIDE_RATIO_FLOOR != rotation.EXCESS_OVER_EQUAL_WEIGHT


def test_the_gate_is_about_separation_not_return() -> None:
    """위험 신호는 수익으로 벤치마크를 이길 필요가 없다. 구간을 갈라야 한다."""

    stage = prespec.rule()["stage_one_separation"]
    assert "downside_volatility_ratio_floor" in stage
    assert "overlap_ceiling" in stage
    assert not any("return" in key for key in stage)


def test_the_separation_gate_needs_all_three_conditions() -> None:
    assert prespec.separation_gate(1.8, 0.7, 0.02)["passes"] is True
    assert prespec.separation_gate(1.2, 0.7, 0.02)["passes"] is False
    assert prespec.separation_gate(1.8, 0.95, 0.02)["passes"] is False
    assert prespec.separation_gate(1.8, 0.7, 0.30)["passes"] is False


def test_means_alone_cannot_pass_the_gate() -> None:
    """평균이 갈려도 분포가 포개지면 개별 시점의 결정은 신뢰할 수 없다."""

    read = prespec.separation_gate(2.5, 0.97, 0.001)
    assert read["passes"] is False
    assert "the_two_distributions_do_not_simply_overlap" in read["failed"]


# ── 제외 강도 ───────────────────────────────────────────────────────────────


def test_both_exclusion_strengths_must_hold() -> None:
    """트랙 19와 23에서 두 강도의 방향이 서로 반대로 나왔다. 하나만 보면 고를 수 있게 된다."""

    assert prespec.BOTH_EXCLUSION_STRENGTHS_MUST_HOLD is True
    assert prespec.robustness_gate(1.4, 1.4, 1.5, 1.8)["passes"] is True
    assert prespec.robustness_gate(1.1, 1.4, 1.5, 1.8)["passes"] is False
    assert prespec.robustness_gate(1.4, 1.1, 1.5, 1.8)["passes"] is False


def test_removing_2020_must_leave_half_the_excess_over_chance() -> None:
    """비 1.0이 우연이므로 잔존은 1.0 초과분으로 재야 한다."""

    # 전체 1.8이면 초과분 0.8, 절반은 0.4 → 1.4 이상이어야 한다.
    assert prespec.robustness_gate(1.4, 1.4, 1.45, 1.8)["passes"] is True
    assert prespec.robustness_gate(1.4, 1.4, 1.30, 1.8)["passes"] is False


# ── 기간 스프레드 ───────────────────────────────────────────────────────────


def test_the_term_spread_is_decisive() -> None:
    """넘어서지 못하면 다른 조건과 무관하게 부정이다."""

    assert prespec.TERM_SPREAD_IS_DECISIVE is True
    read = prespec.spread_gate(0.0004, 0.42)
    assert read["passes"] is False
    assert "넘어서지 못한다" in read["verdict"]


def test_a_negative_increment_cannot_pass() -> None:
    assert prespec.spread_gate(-0.001, 0.01)["passes"] is False


def test_the_increment_threshold_is_the_null_not_the_absolute_size() -> None:
    """주간 수익 회귀에서 R²는 원래 작다. 절대 크기로 문턱을 걸면 항상 실패한다."""

    assert prespec.INCREMENTAL_R_SQUARED_FLOOR == 0.0
    assert prespec.spread_gate(0.0009, 0.03)["passes"] is True


# ── 관측 정의 ───────────────────────────────────────────────────────────────


def test_the_large_negative_week_is_defined_before_looking() -> None:
    """사후에 고르면 어떤 국면이든 두드러지게 만들 수 있다."""

    assert prespec.LARGE_NEGATIVE_WEEK == -0.03


def test_the_real_time_window_does_not_decide() -> None:
    assert prespec.DECISION_SAMPLE == "revised_long"
    assert "real_time_overlap" in prespec.REPORTED_NOT_DECIDING


def test_the_decision_horizon_is_one_of_the_reported_horizons() -> None:
    assert prespec.DECISION_HORIZON_WEEKS in prespec.HORIZONS


def test_the_failure_wording_calls_the_outcome_settled_not_fallback() -> None:
    """부정이면 그것은 물러선 것이 아니라 정해진 것이다."""

    text = prespec.rule()["what_counts_as_failure"]
    assert "물러선 것이 아니라" in text
    assert "서술과 상태 인식" in text
