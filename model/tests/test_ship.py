"""출하 번들의 계약.

두 가지를 지킨다.

1. **이 숫자가 어느 변형인지 지워지지 않는가.** v1.1로 오해되는 것이 가장 조용하고
   되돌리기 어려운 사고다.
2. **검증되지 않은 것에 검증된 것과 같은 무게가 붙지 않는가.** 성숙도가 확장기에서만
   검증됐다는 사실, 분산 분포의 국면별 숫자가 에피소드 몇 개에서 나왔다는 사실.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from business_cycle.phase_returns.labels import PHASES
from business_cycle.rotation_rerun.labels17 import GATE
from business_cycle.ship import bundle as B
from business_cycle.ship import realtime as RT
from business_cycle.ship.__main__ import EXPECTED, SUPERSEDED_EXPECTATION, TRANSITION_GATE_APPLIED

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "ship"


# ── 변형이 지워지지 않는가 ──────────────────────────────────────────────────


def test_the_shipped_gate_is_persist17w() -> None:
    assert GATE.name == "persist17w"
    assert GATE.persistence_weeks == 17


def test_the_track16_transition_gate_is_not_applied() -> None:
    """경계를 고친 뒤에는 증상 처방이 오히려 결과를 깎았다."""

    assert TRANSITION_GATE_APPLIED is False


def test_the_expected_numbers_are_the_persist17w_ones() -> None:
    """정정 전 기대값은 persist13w의 것이었다. 그 사실을 상수로 남겨 둔다."""

    assert EXPECTED["transitions"] == 20
    assert EXPECTED["slowdown_weeks"] == 33
    assert EXPECTED["current_official_phase"] == "expansion"
    assert SUPERSEDED_EXPECTATION["transitions"] == 28
    assert SUPERSEDED_EXPECTATION["slowdown_weeks"] == 49
    assert SUPERSEDED_EXPECTATION["actually_from"] == "persist13w"


# ── 동결 경로와의 대조 ──────────────────────────────────────────────────────


def _frame(values: dict[str, list[Any]]) -> pd.DataFrame:
    return pd.DataFrame({"as_of": ["2020-01-03", "2020-01-10"], **values})


def test_the_agreement_check_catches_a_drifting_column() -> None:
    """옮겨 적은 감사 코드가 어긋나면 여기서 걸려야 한다."""

    left = _frame({"activity_level": [0.1, 0.2], "confirming_domains": [2, 3]})
    right = _frame({"activity_level": [0.1, 0.9], "confirming_domains": [2, 3]})
    read = RT.agrees_with_frozen(left, right)
    assert read["agrees"] is False
    assert read["disagreeing"]["activity_level"] == 1


def test_the_agreement_check_passes_when_they_match() -> None:
    left = _frame({"activity_level": [0.1, 0.2], "phase_status": ["official", "official"]})
    read = RT.agrees_with_frozen(left, left.copy())
    assert read["agrees"] is True
    assert read["weeks_compared"] == 2


def test_the_phase_columns_are_not_in_the_agreement_check() -> None:
    """국면은 게이트가 바꾸는 것이다. 그것까지 같기를 요구하면 검사가 늘 실패한다."""

    assert "official_phase" not in RT.THRESHOLD_INDEPENDENT
    assert "phase_separation" not in RT.THRESHOLD_INDEPENDENT
    assert "activity_level" in RT.THRESHOLD_INDEPENDENT


# ── 성숙도 검증 범위 ────────────────────────────────────────────────────────


def _maturity_summary(validated: list[str]) -> dict[str, Any]:
    table = {
        name: {
            "beats_duration_on_successor": name in validated,
            "within_run_shift_p": 0.01 if name in validated else 0.5,
            "successor_rate": {"base": 0.5, "duration_only": 0.6, "late_signal": 0.7},
            "episodes": 45 if name == "expansion" else 5,
        }
        for name in PHASES
    }
    return {
        "current": {"week": "2026-08-14", "phase": "expansion", "stage": "초반", "wording": "문구"},
        "verdict": {
            "with_gap_transforms": table,
            "phases_where_the_late_signal_beats_the_duration_control": validated,
            "symmetric_across_the_four_phases": len(validated) == len(PHASES),
            "statement": "설명",
        },
    }


def test_the_validation_scope_covers_every_phase() -> None:
    """네 국면 중 하나라도 빠지면 화면이 그 국면을 검증된 것으로 다룰 수 있다."""

    read = B.maturity(_maturity_summary(["expansion"]))
    assert [row["phase"] for row in read["validation_scope"]] == list(PHASES)
    assert read["validated_phases"] == ["expansion"]


def test_an_unvalidated_current_phase_gets_no_wording() -> None:
    """검증되지 않은 국면에 문구가 붙으면 검증된 것처럼 보인다."""

    summary = _maturity_summary(["slowdown"])
    read = B.maturity(summary)
    assert read["current"]["validated"] is False
    assert read["current"]["wording"] == ""


def test_a_validated_current_phase_keeps_its_wording() -> None:
    read = B.maturity(_maturity_summary(["expansion"]))
    assert read["current"]["validated"] is True
    assert read["current"]["wording"] == "문구"


def test_a_thin_phase_says_it_cannot_be_confirmed() -> None:
    """다섯 에피소드로는 확인이 안 된다. 그 이유가 데이터에 있어야 한다."""

    summary = _maturity_summary(["expansion"])
    summary["verdict"]["with_gap_transforms"]["contraction"]["beats_duration_on_successor"] = True
    read = B.maturity(summary)
    entry = next(row for row in read["validation_scope"] if row["phase"] == "contraction")
    assert entry["validated"] is False
    assert "에피소드가 5개뿐이라" in entry["why"]


# ── 분산 분포 ───────────────────────────────────────────────────────────────


def _forward_rows() -> list[dict[str, Any]]:
    return [
        {"phase": "recovery", "share_negative": 0.167, "observations": 144, "episodes": 4},
        {"phase": "expansion", "share_negative": 0.285, "observations": 1261, "episodes": 17},
        {"phase": "slowdown", "share_negative": 0.517, "observations": 87, "episodes": 11},
        {"phase": "contraction", "share_negative": 0.518, "observations": 164, "episodes": 5},
    ]


def test_the_distribution_comes_out_as_two_groups() -> None:
    read = B.variance_distribution(_forward_rows(), 13)
    assert len(read["groups"]) == 2
    assert read["groups"][0]["phases"] == ["recovery", "expansion"]
    assert read["groups"][1]["phases"] == ["slowdown", "contraction"]


def test_the_groups_are_weighted_by_observations_not_averaged() -> None:
    """국면별 비율을 단순 평균하면 관측 4주짜리와 1261주짜리가 같은 무게가 된다."""

    read = B.variance_distribution(_forward_rows(), 13)
    lower = read["groups"][0]
    expected = (0.167 * 144 + 0.285 * 1261) / (144 + 1261)
    assert abs(float(lower["share_negative"]) - expected) < 1e-4
    assert lower["observations"] == 1405


def test_the_detail_keeps_episode_counts() -> None:
    """에피소드 수가 없으면 17%와 29%가 같은 무게로 읽힌다."""

    read = B.variance_distribution(_forward_rows(), 13)
    assert [row["episodes"] for row in read["detail_by_phase"]] == [4, 17, 11, 5]


def test_the_distribution_says_it_is_the_revised_path() -> None:
    read = B.variance_distribution(_forward_rows(), 13)
    assert read["path"] == "revised_latest_vintage"
    assert "실시간" in read["path_note"]


# ── 산출물 ──────────────────────────────────────────────────────────────────


def _summary() -> dict[str, Any] | None:
    path = OUTPUT / "ship_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_bundle_records_the_variant() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["variant"]["id"] == GATE.name
    assert summary["variant"]["transition_gate_applied"] is False


def test_the_bundle_verification_matches_the_expected_numbers() -> None:
    summary = _summary()
    if summary is None:
        return
    measured = summary["verification"]["measured"]
    assert measured["current_official_phase"] == EXPECTED["current_official_phase"]
    assert measured["transitions"] == EXPECTED["transitions"]
    assert measured["slowdown_weeks"] == EXPECTED["slowdown_weeks"]


def test_the_bundle_agrees_with_the_frozen_path_on_shared_columns() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["frozen_path_agreement"]["agrees"] is True
    assert summary["frozen_path_agreement"]["weeks_compared"] > 600


def test_the_bundle_carries_the_interpretation_boundaries() -> None:
    """트랙 27의 두 경계가 변형 번들에서도 살아 있어야 한다."""

    summary = _summary()
    if summary is None:
        return
    boundaries = summary["current_state"]["interpretation_boundaries"]
    assert {entry["id"] for entry in boundaries} == {"A", "B"}
