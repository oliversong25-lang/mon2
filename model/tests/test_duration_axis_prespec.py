"""사전 명세의 계약.

이 트랙에 고유한 계약은 **두 축을 같은 자로 재는가**다. 빈도가 다르거나 통 수가 다르면
천장 비교가 축의 차이를 재지 않고 자의 차이를 잰다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from business_cycle.duration_axis import prespec
from business_cycle.value_proxies import prespec as track20

ROOT = Path(__file__).resolve().parents[1]
PRESPEC = "src/business_cycle/duration_axis/prespec.py"
RESULTS = "outputs/duration_axis/validation_summary.json"


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
    assert not any("outputs/duration_axis" in name for name in touched), touched


# ── 같은 자로 재는가 ────────────────────────────────────────────────────────


def test_the_industry_axis_is_recomputed_on_the_same_grid() -> None:
    """트랙 23의 주간 숫자를 그대로 견주면 축이 아니라 빈도를 재게 된다."""

    assert "monthly" in prespec.COMPARISON_AXIS
    assert "월간" in prespec.rule()["why_the_same_ruler"]


def test_the_primary_bucket_count_is_close_to_twelve() -> None:
    """통이 적으면 천장이 기계적으로 낮아져 듀레이션 축이 불리해진다."""

    assert "decile" in prespec.PRIMARY_BUCKETS
    assert "quintile" in prespec.SECONDARY_BUCKETS


def test_the_same_top_k_as_track23() -> None:
    from business_cycle.phase_returns.rotation import TOP_K

    assert prespec.TOP_K == TOP_K


# ── 천장이 관문 노릇을 하는가 ───────────────────────────────────────────────


def test_a_ceiling_that_barely_moves_fails() -> None:
    """통 수와 빈도만 바꿔도 10~20%는 움직인다. 그것은 축의 증거가 아니다."""

    read = prespec.ceiling_gate(0.058, 1.2, 0.050)
    assert read["passes"] is False
    assert "materially_higher_than_the_industry_axis" in read["failed"]


def test_a_ceiling_that_moves_but_stays_low_fails() -> None:
    """축이 바뀌었다고 쓸 만함의 기준이 바뀌지는 않는다."""

    read = prespec.ceiling_gate(0.070, 1.2, 0.030)
    assert read["passes"] is False
    assert "clears_the_absolute_floor" in read["failed"]


def test_a_ceiling_that_clears_both_passes() -> None:
    assert prespec.ceiling_gate(0.15, 1.4, 0.05)["passes"] is True


def test_the_failure_wording_closes_the_line_on_any_axis() -> None:
    read = prespec.ceiling_gate(0.05, 0.7, 0.05)
    assert read["passes"] is False
    assert "어떤 축으로 잘라도 닫힌다" in read["verdict"]
    assert prespec.CEILING_CLOSES_THE_LINE is True


# ── 다중비교 ────────────────────────────────────────────────────────────────


def test_the_family_counts_track20_as_well() -> None:
    """같은 정렬을 반대 방향으로 읽는 것이므로 한 족보다."""

    assert prespec.TRACK20_PROXIES_TESTED == len(track20.VALUE_DEFINITIONS_TESTED)
    assert prespec.PROJECT_WIDE_FAMILY == 8


def test_the_bonferroni_threshold_matches_the_family() -> None:
    assert abs(prespec.BONFERRONI_ALPHA - 0.05 / 8) < 1e-12
    read = prespec.multiplicity(0.02)
    assert read["family_size"] == 8
    assert read["bonferroni_p"] == 0.16
    assert read["survives"] is False


def test_a_very_small_p_still_survives_the_correction() -> None:
    assert prespec.multiplicity(0.001)["survives"] is True


# ── 메커니즘이 대조를 정하는가 ─────────────────────────────────────────────


def test_the_controls_are_the_rate_pair_track25_settled_on() -> None:
    from business_cycle.variance_control import prespec as track25

    assert prespec.CONTROL_LOOKBACKS == track25.LOOKBACKS
    assert prespec.CONTROL_KEEPS_THE_TERM_SPREAD is True


def test_2022_is_named_as_an_exclusion_before_looking() -> None:
    """사후에 고르면 어떤 결과든 살릴 수 있다."""

    names = [name for name, _, _ in prespec.EPISODE_EXCLUSIONS]
    assert "ex_2022" in names and "ex_covid" in names and "ex_gfc" in names


def test_the_unfavourable_prior_is_written_into_the_rule() -> None:
    """보고서가 그것을 적어야 하고, 적으려면 규칙에 있어야 한다."""

    text = prespec.rule()["prior_is_unfavourable"]
    assert "가장 일관되게 실패한" in text
    assert "트랙 20" in text


def test_the_primary_proxy_is_named_before_results() -> None:
    assert prespec.PRIMARY_PROXY == "dividend_yield"
    assert prespec.PRIMARY_PROXY in prespec.PROXIES
