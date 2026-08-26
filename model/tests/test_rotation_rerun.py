"""재실행의 계약.

트랙 17 기계를 **그대로** 쓴다는 것이 첫 계약이다. 새 절차를 만들면 전후 비교가
"라벨이 달라져서"인지 "재는 법이 달라져서"인지 갈리지 않는다.

두 번째는 천장이 실제로 관문 노릇을 하는가다. 관문이 이름만이면 사전 명세가 없는 것과
같다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from business_cycle.phase_returns import rotation as R
from business_cycle.rotation_rerun import ceiling as CE
from business_cycle.rotation_rerun import labels17 as L17
from business_cycle.rotation_rerun import leaveout as LO
from business_cycle.rotation_rerun import prespec
from business_cycle.rotation_rerun import rerun as RR

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "rotation_rerun"


def _weekly(weeks: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.Index(
        [str(day.date()) for day in pd.date_range("2000-01-07", periods=weeks, freq="W-FRI")],
        name="week",
    )
    frame = pd.DataFrame(
        rng.normal(0.001, 0.02, (weeks, 12)),
        index=index,
        columns=list(R.INDUSTRIES),
    )
    frame["MKT"] = rng.normal(0.001, 0.02, weeks)
    return frame


def _phase(weeks: int) -> pd.Series:
    names = ["expansion", "slowdown", "contraction", "recovery"]
    values = [names[(position // 40) % 4] for position in range(weeks)]
    index = pd.Index(
        [str(day.date()) for day in pd.date_range("2000-01-07", periods=weeks, freq="W-FRI")],
        name="week",
    )
    return pd.Series(values, index=index)


# ── 트랙 17 기계를 그대로 쓰는가 ────────────────────────────────────────────


def test_the_rerun_uses_the_same_horizons_as_track17() -> None:
    from business_cycle.phase_returns import forward as F

    assert tuple(prespec.HORIZONS) == tuple(F.HORIZONS)


def test_the_rerun_uses_the_same_null_stride_and_minimum_history() -> None:
    """이동 간격이나 최소 이력이 달라지면 전후 비교가 라벨 차이를 재지 않는다."""

    assert RR.ROTATION_NULL_STRIDE == 4
    assert RR.SHORT_WINDOW_MINIMUM_HISTORY == 26


def test_the_gate_under_test_is_the_one_track22_recommended() -> None:
    """여기서 게이트를 다시 고르면 그것은 재실행이 아니라 또 한 번의 탐색이다."""

    assert L17.GATE.persistence_weeks == 17
    assert L17.GATE.name == "persist17w"


# ── 천장이 관문 노릇을 하는가 ───────────────────────────────────────────────


def test_the_oracle_ceiling_beats_the_phase_ceiling_by_construction() -> None:
    """국면을 알고 고르는 것보다 매주 답을 보고 고르는 쪽이 반드시 낫다."""

    weekly = _weekly(300)
    read = CE.measure(_phase(300), weekly)
    assert (
        read["oracle_ceiling"]["annualised_relative_return"]
        > read["ranking_ceiling"]["annualised_relative_return"]
    )
    assert 0.0 < read["phase_share_of_the_oracle"] < 1.0


def test_the_oracle_beats_equal_weight_every_single_week() -> None:
    """상위 3개의 평균은 12개의 평균 위다 — 정의상 그렇다.

    "매주 양수"는 성립하지 않는다. 12개 산업이 모두 시장을 밑도는 주에는 상위 3개도
    음수다. 성립하는 불변식은 동일가중과의 비교 쪽이다.
    """

    weekly = _weekly(200, seed=3)
    values = R.weekly_relative(weekly).dropna(how="any").to_numpy(dtype=float)
    oracle = (CE._weekly_oracle(values, R.TOP_K) * values).sum(axis=1)
    assert bool((oracle >= values.mean(axis=1) - 1e-12).all())


def test_a_low_ceiling_stops_the_rotation_question() -> None:
    low = prespec.ceiling_gate(0.05, 0.68)
    assert low["passes"] is False
    assert "순환매 질문은 여기서 끝난다" in low["verdict"]


def test_the_compare_reads_a_ceiling_that_did_not_move() -> None:
    weekly = _weekly(300)
    same = CE.measure(_phase(300), weekly)
    read = CE.compare(same, same)
    assert read["moved_annual"] == 0.0
    assert "사실상 그대로다" in read["reading"]


# ── 에피소드 제외, 두 강도 ──────────────────────────────────────────────────


def test_the_stronger_exclusion_removes_more_weeks() -> None:
    """사건 포함 쪽이 더 많이 지우지 않으면 두 강도가 같은 것이다."""

    weekly = _weekly(400, seed=1)
    read = LO.run(_phase(400), weekly, minimum=52, forward=26)
    for row in read["rows"]:
        assert row["event_including_weeks_removed"] >= row["block_only_weeks_removed"]
    widened = [
        row
        for row in read["rows"]
        if row["event_including_weeks_removed"] > row["block_only_weeks_removed"]
    ]
    assert widened, "어떤 에피소드도 넓혀지지 않았다"


def test_the_deciding_strength_is_recorded_in_the_result() -> None:
    """어느 쪽이 판정했는지가 산출물에 없으면 나중에 약한 쪽을 읽게 된다."""

    read = LO.run(_phase(300), _weekly(300, seed=2), minimum=52)
    assert read["deciding_strength"] == "event_including"
    assert "event_including_summary" in read and "block_only_summary" in read


def test_every_phase_contributes_its_episode_count() -> None:
    """주 수만 세면 표본 크기가 부풀려진다. 에피소드 수를 함께 남긴다."""

    read = LO.run(_phase(400), _weekly(400, seed=4), minimum=52)
    assert sum(read["episodes_by_phase"].values()) == read["episodes"]
    assert all(count > 0 for count in read["episodes_by_phase"].values())


def test_a_sign_flip_is_counted_against_the_full_sample_sign() -> None:
    rows = [
        {"phase": "expansion", "episode": 1, "start": "a", "event_including": -0.02},
        {"phase": "expansion", "episode": 2, "start": "b", "event_including": 0.03},
    ]
    read = LO._summarise(rows, "event_including", 0.02)
    assert read["episodes_that_flip_the_sign"] == 1
    assert read["stays_positive_everywhere"] is False


# ── 2020 의존성 ─────────────────────────────────────────────────────────────


def _analysis(values: dict[int, float]) -> dict[str, object]:
    return {
        "horizons": {
            str(h): {
                "shift_test": {
                    "taxonomy_dispersion_ratio_to_null_median": v,
                    "taxonomy_dispersion_p_value": 0.2,
                }
            }
            for h, v in values.items()
        }
    }


def test_covid_dependence_flags_a_result_that_rides_on_2020() -> None:
    full = _analysis({4: 3.0, 13: 3.0, 26: 3.0})
    ex_covid = _analysis({4: 1.0, 13: 1.0, 26: 1.0})
    read = RR.covid_dependence(full, ex_covid, full)
    assert read["average_retained_without_covid"] < 0.6
    assert "얹혀 있다" in read["reading"]


def test_covid_dependence_passes_a_result_that_does_not() -> None:
    full = _analysis({4: 3.0, 13: 3.0, 26: 3.0})
    ex_covid = _analysis({4: 2.8, 13: 2.7, 26: 2.9})
    read = RR.covid_dependence(full, ex_covid, full)
    assert read["average_retained_without_covid"] >= 0.6


# ── 산출물 ──────────────────────────────────────────────────────────────────


def _summary() -> dict[str, object] | None:
    path = OUTPUT / "validation_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_report_carries_the_prespecified_rule_verbatim() -> None:
    """규칙을 산출물에 실어야 나중에 결과와 대조할 수 있다."""

    summary = _summary()
    if summary is None:
        return
    assert summary["prespecified_rule"] == prespec.rule()  # type: ignore[index]


def test_the_report_states_where_it_stopped() -> None:
    summary = _summary()
    if summary is None:
        return
    verdict = summary["verdict"]  # type: ignore[index]
    if not verdict["usable_for_rotation"]:
        assert verdict["stopped_at"] in {"ceiling", "rotation"}


def test_the_selection_dependence_is_in_the_report_text() -> None:
    """'재실행 통과'만 보고 맥락을 못 본 독자가 결과를 과대평가하면 안 된다."""

    path = OUTPUT / "rotation_rerun_report.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    assert "선택 의존성" in text
    assert "판별력 기계로 골랐다" in text
