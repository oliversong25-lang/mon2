"""사전 명세의 계약 — **결과보다 먼저 커밋됐는가**, 그리고 **대조를 약화시키지 않는가.**

두 번째가 이 트랙에 고유하다. 대조를 약하게 잡으면 국면이 이기는 것은 당연하고, 그러면
검정이 아니라 연출이 된다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from business_cycle.variance_control import prespec

ROOT = Path(__file__).resolve().parents[1]
PRESPEC = "src/business_cycle/variance_control/prespec.py"
RESULTS = "outputs/variance_control/validation_summary.json"


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
    assert not any("outputs/variance_control" in name for name in touched), touched


# ── 대조를 약화시키지 않는가 ────────────────────────────────────────────────


def test_the_strongest_lookback_is_chosen_not_the_weakest() -> None:
    """약한 창을 대조로 쓰면 국면이 이기는 것은 당연하다."""

    candidates = [
        {"lookback_weeks": 4, "control_only_r_squared": 0.02},
        {"lookback_weeks": 13, "control_only_r_squared": 0.09},
        {"lookback_weeks": 26, "control_only_r_squared": 0.05},
    ]
    read = prespec.lookback_choice(candidates)
    assert read["chosen"] == 13
    assert read["weakest"] == 4


def test_all_three_lookbacks_are_specified_up_front() -> None:
    assert set(prespec.LOOKBACKS) >= {4, 13, 26}


def test_the_control_gets_both_functional_forms() -> None:
    """형태를 하나만 주면 잘못 골랐을 때 대조가 진다."""

    assert prespec.CONTROL_TAKES_BOTH_LEVEL_AND_ROOT is True


def test_the_term_spread_stays_in_the_control() -> None:
    """물음은 '국면이 둘 다 넘어서는가'다."""

    assert prespec.CONTROL_KEEPS_THE_TERM_SPREAD is True


# ── 판정 통계량 ─────────────────────────────────────────────────────────────


def test_the_decision_statistic_is_an_increment_not_a_ratio() -> None:
    """트랙 24가 최대/최소 비의 약함을 기록했다. 같은 실수를 반복하지 않는다."""

    assert "incremental" in prespec.DECISION_STATISTIC
    assert "ratio" not in prespec.DECISION_STATISTIC


def test_the_decision_target_matches_what_track24_measured() -> None:
    """정의를 바꾸면 같은 것을 심문하는지 알 수 없다."""

    assert prespec.DECISION_TARGET == "squared forward 13-week return"
    assert prespec.DECISION_HORIZON_WEEKS == 13


def test_the_secondary_target_is_named_up_front_and_cannot_decide() -> None:
    """결과를 본 뒤에 두 번째 정의를 꺼내면 그것이 곧 정의 탐색이다."""

    assert prespec.SECONDARY_TARGET
    assert prespec.SECONDARY_CANNOT_OVERTURN is True


def test_the_gate_needs_all_five_conditions() -> None:
    assert prespec.decision_gate(0.05, 0.01, 0.02, 0.02, 0.01, 0.01)["passes"] is True
    # 증분이 0 이하
    assert prespec.decision_gate(-0.01, 0.01, 0.02, 0.02, 0.01, 0.01)["passes"] is False
    # 귀무를 못 넘음
    assert prespec.decision_gate(0.05, 0.30, 0.02, 0.02, 0.01, 0.01)["passes"] is False
    # 약한 제외에서 부호가 바뀜
    assert prespec.decision_gate(0.05, 0.01, -0.01, 0.02, 0.01, 0.01)["passes"] is False
    # 강한 제외에서 부호가 바뀜
    assert prespec.decision_gate(0.05, 0.01, 0.02, -0.01, 0.01, 0.01)["passes"] is False
    # 2020 제외에서 무너짐
    assert prespec.decision_gate(0.05, 0.01, 0.02, 0.02, 0.30, 0.01)["passes"] is False
    # GFC 제외에서 무너짐
    assert prespec.decision_gate(0.05, 0.01, 0.02, 0.02, 0.01, 0.30)["passes"] is False


def test_a_failure_is_named_as_volatility_clustering() -> None:
    read = prespec.decision_gate(0.001, 0.40, 0.001, 0.001, 0.40, 0.40)
    assert read["passes"] is False
    assert "변동성 군집" in read["verdict"]


# ── 마지막 검정이라는 못박음 ────────────────────────────────────────────────


def test_the_rule_forbids_searching_further_after_a_failure() -> None:
    """여덟 트랙을 검정해 왔다. 통과할 때까지 계속하는 위험이 실재한다."""

    rule = prespec.rule()
    assert rule["this_is_the_last_test_in_this_line"] is True
    forbidden = " ".join(rule["if_it_fails_do_not"])
    for what in ("대조", "지평선", "분산 정의", "통계량"):
        assert what in forbidden


# ── 통과했을 때의 제품 형태 ─────────────────────────────────────────────────


def test_the_permitted_wording_is_a_historical_fact_not_a_forecast() -> None:
    text = prespec.PERMITTED_WORDING
    assert "역사적으로" in text
    assert "%" in text


def test_the_forbidden_list_covers_exposure_holdings_and_forecast() -> None:
    joined = " ".join(prespec.FORBIDDEN_WORDING)
    assert "노출" in joined
    assert "보유" in joined
    assert "확률" in joined


def test_the_product_form_is_fixed_before_the_result_is_known() -> None:
    """결과가 좋았다는 이유로 제품 형태를 넓히지 않는다."""

    form = prespec.rule()["product_form_if_it_passes"]
    assert form["permitted"] == prespec.PERMITTED_WORDING
    assert len(form["forbidden"]) == len(prespec.FORBIDDEN_WORDING)
