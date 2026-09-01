"""사전 명세의 계약 — **결과보다 먼저 커밋됐는가.**

이 파일은 결과 모듈보다 먼저 존재해야 한다. 사전 명세가 결과와 같은 커밋에 들어오면
그것은 사전 명세가 아니라 사후 서술이고, 이 재실행 전체의 방어가 사라진다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from business_cycle.rotation_rerun import prespec

ROOT = Path(__file__).resolve().parents[1]
PRESPEC = "src/business_cycle/rotation_rerun/prespec.py"
RESULTS = "outputs/rotation_rerun/validation_summary.json"


def _added_commit(path: str) -> str | None:
    """이 경로를 **추가한** 커밋. 없으면 아직 커밋되지 않은 것이다."""

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
    """규칙이 결과와 같은 커밋에 들어왔으면 그것은 사전 명세가 아니다."""

    prespec_commit = _added_commit(PRESPEC)
    if prespec_commit is None:
        # 아직 커밋 전이다. 커밋 순서를 강제하는 것이 이 시험의 목적이므로, 커밋되지
        # 않은 상태에서는 결과도 커밋되어 있으면 안 된다.
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
    """규칙을 커밋한 그 자리에 산출물이 함께 들어왔으면 이미 계산한 것이다."""

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
    assert not any("outputs/rotation_rerun" in name for name in touched), touched


# ── 규칙 자체가 규칙인가 ────────────────────────────────────────────────────


def test_the_ceiling_gates_everything_below_it() -> None:
    """천장이 낮으면 그 아래 어떤 정확도 개선도 넘어설 수 없다."""

    rule = prespec.rule()
    assert rule["stage_one_ceiling"]["gates_everything_below"] is True
    low = prespec.ceiling_gate(0.05, 0.8)
    assert low["passes"] is False
    assert "여기서 끝난다" in low["verdict"]


def test_the_ceiling_needs_both_return_and_ratio() -> None:
    """수익만 크고 변동이 더 크면 쓸 자리가 아니다."""

    assert prespec.ceiling_gate(0.12, 0.6)["passes"] is False
    assert prespec.ceiling_gate(0.06, 1.4)["passes"] is False
    assert prespec.ceiling_gate(0.12, 1.4)["passes"] is True


def test_the_threshold_sits_above_what_track17_already_produced() -> None:
    """트랙 17의 +1.40%p를 '이기지 못했다'로 읽었으므로 문턱은 그 위여야 한다."""

    assert prespec.EXCESS_OVER_EQUAL_WEIGHT > 0.014
    assert prespec.CEILING_FLOOR_ANNUAL > prespec.TRACK17_CEILING_ANNUAL


def test_the_rotation_gate_needs_all_four_conditions() -> None:
    assert prespec.rotation_gate(0.03, 0.02, 0.02, True)["passes"] is True
    assert prespec.rotation_gate(0.01, 0.02, 0.02, True)["passes"] is False
    assert prespec.rotation_gate(0.03, 0.20, 0.02, True)["passes"] is False
    assert prespec.rotation_gate(0.03, 0.02, 0.005, True)["passes"] is False
    assert prespec.rotation_gate(0.03, 0.02, 0.02, False)["passes"] is False


def test_the_deciding_leaveout_strength_is_the_event_including_one() -> None:
    """트랙 19는 두 강도가 긴 지평선에서 반대 답을 준다는 것을 보였다."""

    rule = prespec.rule()
    assert (
        rule["stage_two_rotation"]["leave_one_out_strength_that_decides"]
        == "event_including_forward_windows"
    )


def test_the_real_time_window_does_not_decide() -> None:
    """실시간 창은 침체 에피소드가 2020년 하나뿐이라 한 사건이 판정하게 된다."""

    assert prespec.DECISION_SAMPLE == "revised_long"
    assert "real_time_overlap" in prespec.REPORTED_NOT_DECIDING


def test_discrimination_is_reported_but_does_not_decide() -> None:
    """'분류에 뜻이 있는가'와 '순환매에 쓸 수 있는가'는 다른 질문이다."""

    assert prespec.DISCRIMINATION_DECIDES is False


def test_the_rule_states_what_would_separate_selection_from_improvement() -> None:
    """가를 방법을 적지 않으면 '가를 수 없다'가 변명이 된다."""

    text = prespec.rule()["selection_dependence"]
    assert "표본 밖" in text and "다른 시장" in text and "전향 검정" in text
