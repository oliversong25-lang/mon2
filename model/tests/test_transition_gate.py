"""전이 게이트: 동결 v1.1 불변, 게이트 동작, 그리고 **어느 조건이 일을 하는지**.

가장 중요한 시험은 마지막이다. 분리도와 원시 동의 중 무엇이 채터링을 잡는지 뒤바꿔
기억하면, 다음 사람이 분리도를 올려 지연만 사고 채터링은 그대로 두게 된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from business_cycle.config import load_settings
from business_cycle.four_phase.engine import load_config
from business_cycle.transition_gate import characterise as C
from business_cycle.transition_gate import nber as N
from business_cycle.transition_gate.gate import (
    DEFAULT_STALE_HOLD_WEEKS,
    GateConfig,
    apply,
    evaluate,
)


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _frame() -> pd.DataFrame:
    return C.load_path(str(_root() / "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"))


def _summary() -> dict[str, Any]:
    path = _root() / "outputs/transition_gate/validation_summary.json"
    assert path.exists(), "산출물이 없다. `python -m business_cycle.transition_gate`"
    return dict(json.loads(path.read_text(encoding="utf-8")))


# ── 동결 v1.1 불변 ───────────────────────────────────────────────────────────


def test_the_frozen_config_is_untouched() -> None:
    config = load_config(load_settings())
    assert config.sha256 == "e052a4f41ca2d01431bab32e6df8bbd383ea9a2dab09982a6675e789bcc3265a"
    assert _summary()["frozen_config_sha256"] == config.sha256
    assert _summary()["frozen_model_modified"] is False


def test_the_gate_is_a_parallel_variant_not_a_new_engine() -> None:
    package = _root() / "src" / "business_cycle" / "transition_gate"
    banned = (
        "def observation_scores",
        "def contraction_evidence",
        "def recovery_evidence",
        "def filter_scores",
        "def confirm_transitions",
        "Thresholds(",
    )
    for path in package.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in body, f"{path.name}에 점수 재계산이 있다: {token}"
    assert _summary()["variant"] == "parallel_gate_over_frozen_v1_1"


def test_the_gate_leaves_the_original_columns_intact() -> None:
    frame = _frame()
    result = apply(frame, GateConfig(0.5, True))
    for column in ("official_phase", "raw_phase", "phase_separation", "confirming_domains"):
        assert result[column].equals(frame[column]), column


def test_a_gate_with_no_conditions_reproduces_v1_1_exactly() -> None:
    frame = _frame()
    result = apply(frame, GateConfig())
    # 보류 주는 양쪽 다 국면이 없다. 그 외에는 한 주도 달라지면 안 된다.
    eligible = result[result["phase_status"].ne("withheld")]
    assert eligible["gated_phase"].equals(eligible["official_phase"])
    assert evaluate(frame, GateConfig())["transitions"] == 72


# ── 게이트 동작 ──────────────────────────────────────────────────────────────


def test_the_recommended_gate_cuts_chatter_without_buying_latency() -> None:
    row = _summary()["recommended"]
    # 권고는 원시 동의 단독이다. 분리도 문턱은 걸지 않는다.
    assert row["require_raw_agreement"] is True
    assert row["separation_threshold"] is None
    assert row["transitions"] < 72
    assert row["phases_shorter_than_four_weeks"] < 18
    # 이것이 권고의 핵심 근거다. 지연을 한 주도 늘리지 않는다.
    assert row["contraction_delay_weeks"] == 0
    assert row["recovery_delay_weeks"] == 0


def test_a_blocked_transition_holds_the_previous_phase() -> None:
    frame = _frame()
    result = apply(frame, GateConfig(require_raw_agreement=True))
    blocked = result[result["gate_reason"].str.startswith("blocked")]
    assert len(blocked) > 0
    for week in blocked.index:
        # 막힌 주에도 국면은 하나 나온다. 빈칸으로 두지 않는다.
        assert str(result.at[week, "gated_phase"]) in C.PHASES


def test_a_prolonged_block_degrades_to_withheld() -> None:
    frame = _frame()
    # 시효를 짧게 줄이면 강등이 반드시 나와야 한다 — 규칙이 살아 있는지 보는 시험이다.
    result = apply(frame, GateConfig(0.9, True, stale_hold_weeks=2))
    degraded = result[result["gate_reason"].eq("stale_hold_degraded_to_withheld")]
    assert len(degraded) > 0
    for week in degraded.index:
        assert str(result.at[week, "gated_phase"]) == ""
        assert str(result.at[week, "gated_status"]) == "withheld"


def test_the_hold_never_freezes_a_phase_forever() -> None:
    """오래 막히면 국면이 얼어붙는 대신 판정이 내려가야 한다.

    `blocked_run_weeks`는 막힌 기간을 세는 카운터라 시효를 넘어 계속 올라간다 —
    그 자체는 정상이다. 봐야 할 것은 **시효를 넘긴 주에 국면이 남아 있는가**다.
    """

    frame = _frame()
    result = apply(frame, GateConfig(0.9, True))
    overdue = result[result["blocked_run_weeks"] > DEFAULT_STALE_HOLD_WEEKS]
    assert len(overdue) > 0, "0.9는 반드시 시효를 넘기는 구간을 만든다"
    # 시효를 넘긴 주는 하나도 국면을 내보내면 안 된다.
    assert (overdue["gated_phase"] == "").all()
    assert (overdue["gated_status"] == "withheld").all()
    # 그리고 시효 안쪽에서는 직전 국면을 유지한다.
    inside = result[
        (result["blocked_run_weeks"] > 0)
        & (result["blocked_run_weeks"] <= DEFAULT_STALE_HOLD_WEEKS)
    ]
    assert (inside["gated_phase"] != "").all()


def test_every_gate_keeps_the_two_domain_contraction_breadth_rule() -> None:
    for row in _summary()["sweep"]:
        assert row["contraction_weeks_meet_the_two_domain_breadth_gate"] is True, row["gate"]


def test_withheld_weeks_stay_withheld_under_every_gate() -> None:
    frame = _frame()
    upstream = set(frame.index[frame["phase_status"].eq("withheld")])
    for config in (GateConfig(), GateConfig(0.7, True)):
        result = apply(frame, config)
        for week in upstream:
            assert str(result.at[week, "gated_phase"]) == ""


# ── 어느 조건이 일을 하는가 ──────────────────────────────────────────────────


def test_raw_agreement_is_the_condition_that_catches_the_2020_blips() -> None:
    """2020-02-28의 분리도는 0.754다. 분리도 게이트는 이것을 통과시킨다."""

    frame = _frame()
    week = "2020-02-28"
    assert float(frame.at[week, "phase_separation"]) > 0.7
    assert str(frame.at[week, "official_phase"]) == "contraction"
    assert str(frame.at[week, "raw_phase"]) == "slowdown"

    # 분리도만: 통과해 버린다.
    only_separation = apply(frame, GateConfig(separation_threshold=0.7))
    assert str(only_separation.at[week, "gated_phase"]) == "contraction"

    # 원시 동의: 막는다.
    only_raw = apply(frame, GateConfig(require_raw_agreement=True))
    assert str(only_raw.at[week, "gated_phase"]) == "slowdown"
    assert str(only_raw.at[week, "gate_reason"]) == "blocked_raw"


def test_the_real_2020_contraction_call_passes_every_gate() -> None:
    frame = _frame()
    week = "2020-04-03"
    assert str(frame.at[week, "raw_phase"]) == "contraction"
    for config in (
        GateConfig(require_raw_agreement=True),
        GateConfig(0.5, True),
        GateConfig(0.7, True),
    ):
        assert str(apply(frame, config).at[week, "gated_phase"]) == "contraction", config.name


def test_raw_disagreement_predicts_reversion_better_than_separation() -> None:
    dis = _summary()["characterisation"]["raw_official_disagreement"]["at_transition"]
    agree = dis["raw_agrees_with_the_new_phase"]["reversion_rate"]
    disagree = dis["raw_does_not_agree"]["reversion_rate"]
    assert disagree > agree * 3, (agree, disagree)

    # 분리도는 지속을 거의 예측하지 못한다. 그 사실을 기록으로 못박는다.
    dur = _summary()["characterisation"]["separation_versus_duration"]
    assert abs(dur["spearman_rank_correlation"]) < 0.3


def test_the_hand_picked_gap_does_not_survive_the_full_record() -> None:
    """0.419와 0.650 사이가 비어 있다는 관찰은 극단만 골라 본 결과였다."""

    dur = _summary()["characterisation"]["separation_versus_duration"]
    bands = {entry["band"]: entry["transitions"] for entry in dur["by_band"]}
    assert bands.get("0.4–0.5", 0) > 0
    assert bands.get("0.5–0.6", 0) > 0
    assert bands.get("0.6–0.7", 0) > 0


# ── NBER 대조 ────────────────────────────────────────────────────────────────


def test_the_gate_removes_a_spurious_call_and_keeps_the_real_one() -> None:
    frame = _frame()
    baseline = N.audit(frame, GateConfig())
    gated = N.audit(frame, GateConfig(0.5, True))

    # 침체 밖 오탐 구간이 줄어야 한다.
    assert len(gated["false_positive_episodes"]) < len(baseline["false_positive_episodes"])
    # 진짜 호출은 남아야 한다.
    assert gated["first_contraction_call"] == "2020-04-03"
    assert gated["recall"] is not None and gated["recall"] > 0


def test_the_recall_cost_is_recorded_not_hidden() -> None:
    """1주짜리 깜빡임이 NBER 구간 안에 있었으므로 재현율이 내려간다. 숨기지 않는다."""

    frame = _frame()
    baseline = N.audit(frame, GateConfig())
    gated = N.audit(frame, GateConfig(0.5, True))
    assert gated["recall"] < baseline["recall"]
    assert gated["single_recession_limitation"]


def test_the_post_trough_latency_is_not_fixed_by_the_gate() -> None:
    """게이트는 채터링만 다룬다. 회복 지연은 다른 다이얼이다."""

    frame = _frame()
    baseline = N.audit(frame, GateConfig())
    gated = N.audit(frame, GateConfig(0.5, True))
    assert (
        gated["recovery_lag_weeks_from_trough_month_end"]
        == (baseline["recovery_lag_weeks_from_trough_month_end"])
    )


# ── 현재 판정 ────────────────────────────────────────────────────────────────


def test_the_current_call_is_reported_for_every_threshold() -> None:
    rows = {row["gate"]: row["final_week_phase"] for row in _summary()["sweep"]}
    assert rows["sep:off · raw:off"] == "expansion"
    # 0.6 이상에서 현재 판정이 바뀐다. 그 사실이 표에 남아 있어야 한다.
    assert rows["sep>=0.6 · raw:on"] != rows["sep:off · raw:off"]


def test_two_runs_of_the_gate_are_identical() -> None:
    frame = _frame()
    first = apply(frame, GateConfig(0.5, True))["gated_phase"].tolist()
    second = apply(frame, GateConfig(0.5, True))["gated_phase"].tolist()
    assert first == second


def test_the_gate_blocks_more_whipsaw_than_real_transitions() -> None:
    """권고 게이트의 존재 이유. 지운 것 중 왕복이 진짜보다 많아야 한다.

    전이 수만 세면 매끄럽지만 틀린 경로가 좋아 보인다. 처음에 그 함정에 빠져
    `sep>=0.5`를 권고했다가, 막은 것을 지속 기간으로 갈라 세고 뒤집었다.
    """

    rec = _summary()["recommended"]
    assert rec["whipsaw_to_real_ratio"] is not None
    assert rec["whipsaw_to_real_ratio"] > 1.0, rec["whipsaw_to_real_ratio"]


def test_a_separation_threshold_blocks_more_real_transitions_than_whipsaw() -> None:
    """분리도 문턱을 빼기로 한 근거. 켜는 순간 비가 1 아래로 내려간다."""

    for row in _summary()["sweep"]:
        if row["separation_threshold"] is None:
            continue
        ratio = row["whipsaw_to_real_ratio"]
        if ratio is None:
            continue
        assert ratio < 1.0, (row["gate"], ratio)


def test_a_long_correct_transition_is_blocked_by_separation_but_not_by_raw() -> None:
    """2018-10-19은 분리도 0.241인데 65주 갔다. 분리도 게이트는 이것을 막는다."""

    frame = _frame()
    week = "2018-10-19"
    assert float(frame.at[week, "phase_separation"]) < 0.5
    assert str(frame.at[week, "raw_phase"]) == str(frame.at[week, "official_phase"])

    by_separation = apply(frame, GateConfig(separation_threshold=0.5))
    assert str(by_separation.at[week, "gate_reason"]).startswith("blocked")

    by_raw = apply(frame, GateConfig(require_raw_agreement=True))
    assert str(by_raw.at[week, "gate_reason"]) == "accepted"


def test_the_report_states_the_trade_not_a_fit() -> None:
    body = (_root() / "outputs/transition_gate/transition_gate_report.md").read_text(
        encoding="utf-8"
    )
    assert "적합도가 아니라 교환" in body
    assert "0.754" in body, "분리도 게이트가 놓치는 사례가 보고서에 있어야 한다"
    assert "65주" in body, "분리도 게이트가 막는 긴 전이가 보고서에 있어야 한다"
    assert "회복 인식 지연" in body
    for word in ("매수", "매도", "추천", "목표가"):
        assert word not in body, word


@pytest.mark.parametrize("threshold", [0.4, 0.5, 0.6, 0.7])
def test_every_requested_threshold_is_swept(threshold: float) -> None:
    rows = _summary()["sweep"]
    assert any(
        row["separation_threshold"] == threshold and row["require_raw_agreement"] for row in rows
    )
    assert any(
        row["separation_threshold"] == threshold and not row["require_raw_agreement"]
        for row in rows
    )
