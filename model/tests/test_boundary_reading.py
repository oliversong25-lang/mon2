"""해석 확인의 계약.

이 단계는 권고를 다시 고르지 않는다. 그래서 계약도 "무엇이 최선인가"를 묻지 않고
**적는 방식이 정직한가**를 묻는다.

1. 보정이 보정으로 작동하는가 — 격자를 쓸면 p가 커지는가.
2. 반쪽이 반쪽으로 읽히는가 — 우연과 구분되지 않는 값을 "우연 위"라고 적지 않는가.
3. 후퇴기 정의가 한쪽만 보고 정해지지 않는가 — 정밀도와 재현율을 함께 쓰는가.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from business_cycle.boundary_reading import episodes as E
from business_cycle.boundary_reading import multiplicity as MU
from business_cycle.boundary_reading import regimes as R

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "boundary_reading"


def _entry(observed: float, draws: list[float]) -> dict[str, object]:
    return {"observed": observed, "draws": np.array(draws, dtype=float)}


# ── 1: 보정이 보정으로 작동하는가 ───────────────────────────────────────────


def test_the_family_wise_p_is_never_smaller_than_the_best_nominal_p() -> None:
    """격자를 쓸어 고른 값의 보정이 명목보다 **작으면** 그것은 보정이 아니다.

    원 통계량의 최댓값을 그대로 쓰면 실제로 이 일이 일어난다 — 설정마다 후퇴기 주수가
    달라 척도가 다르기 때문이다. min-P가 그 함정을 피하는지 본다.
    """

    rng = np.random.default_rng(0)
    entries = {
        # 척도가 서로 크게 다른 두 설정. 작은 쪽이 구조적으로 큰 값을 낸다.
        "small_scale": _entry(1.2, list(rng.normal(0.0, 0.1, 500))),
        "large_scale": _entry(30.0, list(rng.normal(0.0, 25.0, 500))),
    }
    smallest_nominal = min(
        float(MU._p_of(entries[name]["draws"], np.array([entries[name]["observed"]]))[0])
        for name in entries
    )
    family = MU.max_statistic(entries)
    assert float(family["family_wise_p"]) >= smallest_nominal


def test_a_single_configuration_grid_leaves_the_p_alone() -> None:
    """격자가 하나면 쓸 것이 없으므로 보정도 없어야 한다."""

    rng = np.random.default_rng(1)
    draws = list(rng.normal(0.0, 1.0, 400))
    entries = {"only": _entry(2.5, draws)}
    nominal = float(MU._p_of(np.array(draws), np.array([2.5]))[0])
    assert abs(float(MU.max_statistic(entries)["family_wise_p"]) - nominal) < 0.01


def test_bonferroni_is_an_upper_bound_and_says_so() -> None:
    read = MU.bonferroni(0.0458, 13)
    assert read["bonferroni_p"] == 0.5954
    assert read["survives_at_five_percent"] is False


def test_the_statement_refuses_to_claim_significance_after_a_failed_correction() -> None:
    """보정이 문턱을 넘지 못했는데 '유의'라고 적으면 이 단계가 한 일이 없어진다."""

    read = MU.read(
        {"nominal_p": 0.0458, "ratio_to_chance": 3.078},
        {"family_wise_p": 0.0936, "grid_size": 13, "note": ""},
        {"bonferroni_p": 0.5954, "grid_size": 13},
        "persist17w",
    )
    assert read["survives_correction"] is False
    assert "단정해서는 안 되고" in read["statement"]
    assert len(read["defences"]) == 3


def test_the_reported_nominal_entry_carries_no_raw_draw_array() -> None:
    """수천 개짜리 배열을 산출물에 실으면 JSON이 깨지고, 남길 것은 요약이다."""

    read = MU.read(
        {"nominal_p": 0.05, "draws": np.zeros(10)},
        {"family_wise_p": 0.2, "grid_size": 2, "note": ""},
        {"bonferroni_p": 0.1, "grid_size": 2},
        "gate",
    )
    assert "draws" not in read["nominal"]
    json.dumps(read["nominal"])


# ── 2: 반쪽이 반쪽으로 읽히는가 ─────────────────────────────────────────────


def _panel(weeks: list[str], seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        rng.normal(0.0, 0.05, (len(weeks), 4)),
        index=pd.Index(weeks, name="week"),
        columns=["A", "B", "C", "D"],
    )


def test_a_half_at_chance_is_not_written_up_as_above_chance() -> None:
    """1.015는 숫자로는 1보다 크지만 우연과 구분되지 않는다. 말이 숫자를 따라가면 안 된다."""

    assert R.CHANCE_MARGIN > 0.015


def test_the_split_lands_where_the_consumption_domain_changes() -> None:
    """단절 시점을 임의로 고르면 그것은 구조적 단절이 아니라 또 하나의 격자다."""

    from business_cycle.boundary_verification.extended import CONSUMPTION_SPLIT

    assert R.SPLIT.startswith(CONSUMPTION_SPLIT)


def test_the_split_covers_six_recessions_three_on_each_side() -> None:
    assert len(R.NBER_RECESSIONS) == 6
    assert len(R.recessions_in("1976-07-16", R.SPLIT)) == 3
    assert len(R.recessions_in(R.SPLIT, "2026-08-14")) == 3


def test_a_thin_half_is_flagged_rather_than_read_as_a_state() -> None:
    weeks = [str(day.date()) for day in pd.date_range("1976-07-16", periods=200, freq="W-FRI")]
    phase = pd.Series(["expansion"] * 200, index=pd.Index(weeks, name="week"))
    phase.iloc[50:60] = "slowdown"
    entry = R.half(phase, _panel(weeks, 2), weeks[0], weeks[-1], "pre_1992")
    assert entry["slowdown_blocks"] == 1
    assert entry["enough_blocks_to_call_it_a_state"] is False


# ── 3: 후퇴기 정의가 한쪽만 보고 정해지지 않는가 ────────────────────────────


def _rows(spec: list[tuple[str, str, str]]) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "start": start,
            "end": end,
            "weeks": 4,
            "came_from": "expansion",
            "went_to": "contraction" if outcome == "progressed" else "expansion",
            "outcome": outcome,
            **E._nearest_recession(end),
        }
        for index, (start, end, outcome) in enumerate(spec, start=1)
    ]


def test_recession_coverage_counts_recessions_not_blocks() -> None:
    """정밀도만 세면 후퇴기는 잡음처럼 보인다. 재현율은 반대쪽에서 세야 한다."""

    rows = _rows([("2000-07-21", "2000-12-29", "progressed")])
    coverage = E.recession_coverage(rows, "1976-07-16", "2026-08-14")
    assert coverage["recessions_in_window"] == 6
    assert coverage["preceded_by_a_slowdown_block"] == 1
    assert coverage["detail"][3]["recession"] == "2001-03"
    assert coverage["detail"][3]["preceded_by_slowdown"] is True


def test_recession_coverage_respects_the_window() -> None:
    rows = _rows([("2000-07-21", "2000-12-29", "progressed")])
    assert E.recession_coverage(rows, "1994-07-15", "2026-08-14")["recessions_in_window"] == 3


def test_low_precision_with_high_recall_is_called_a_necessary_condition() -> None:
    """이 조합이 이 모델의 실제 모양이다. '전조'로도 '잡음'으로도 적으면 안 된다."""

    read = E.define(
        {"forewarning_share": 0.438, "closed_blocks": 16, "forewarning_blocks": 7},
        {"coverage": 0.667, "preceded_by_a_slowdown_block": 4, "recessions_in_window": 6},
    )
    assert read["reads_as_a_transition_phase"] is False
    assert "필요조건에 가깝고 충분조건이 아니다" in read["statement"]


def test_low_precision_with_low_recall_is_called_a_deceleration() -> None:
    read = E.define(
        {"forewarning_share": 0.2, "closed_blocks": 16, "forewarning_blocks": 3},
        {"coverage": 0.2, "preceded_by_a_slowdown_block": 1, "recessions_in_window": 6},
    )
    assert "침체와 잇대어 읽으면 안 된다" in read["statement"]


def test_a_block_is_open_until_the_next_phase_is_known() -> None:
    """마지막 블록을 '확장 복귀'로 세면 진행률이 조용히 낮아진다."""

    weeks = [str(day.date()) for day in pd.date_range("2020-01-03", periods=10, freq="W-FRI")]
    phase = pd.Series(["expansion"] * 6 + ["slowdown"] * 4, index=pd.Index(weeks, name="week"))
    rows = E.listing(phase)
    assert rows[-1]["outcome"] == "open"
    assert E.summarise(rows)["closed_blocks"] == 0


# ── 산출물에 실제로 실렸는가 ────────────────────────────────────────────────


def _summary() -> dict[str, object] | None:
    path = OUTPUT / "validation_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_report_carries_every_slowdown_block() -> None:
    summary = _summary()
    if summary is None:
        return
    rows = summary["extended_episodes"]  # type: ignore[index]
    assert len(rows) == summary["extended_episode_summary"]["blocks"]  # type: ignore[index]
    assert all(row["start"] <= row["end"] for row in rows)


def test_the_report_carries_both_halves_of_the_split() -> None:
    summary = _summary()
    if summary is None:
        return
    halves = summary["consumption_split"]["halves"]  # type: ignore[index]
    assert [entry["half"] for entry in halves] == ["pre_1992", "post_1992"]


def test_the_report_names_the_grid_it_corrected_over() -> None:
    """무엇을 쓸었는지 없으면 보정 숫자를 나중에 다시 만들 수 없다."""

    summary = _summary()
    if summary is None:
        return
    assert len(summary["grid"]) == 13  # type: ignore[index]
    assert summary["chosen_gate"] in summary["grid"]  # type: ignore[index,operator]
