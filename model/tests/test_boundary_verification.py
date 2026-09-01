"""세 검증의 계약.

이 단계는 새 모델을 만들지 않는다. 트랙 21이 고른 값이 **이 표본에 맞춘 값인지**를
묻고, 그 답이 보고서와 한계 문구에 남는지를 본다. 그래서 계약도 두 종류다.

1. 재는 방법이 옳은가 — 판정 함수가 실패를 실패로 읽는가.
2. 결과가 어디에 남는가 — 보고서에 실제로 실렸는가. 대화에만 남으면 다음 사람은
   커밋과 보고서만 보므로 이 확인이 있었다는 것을 모른다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from business_cycle.boundary_verification import checks as C
from business_cycle.boundary_verification import extended as X
from business_cycle.boundary_verification import verdicts as VD
from business_cycle.slowdown_boundary import scoring as SC

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "boundary_verification"


def _series(values: list[str], start: str = "2000-01-07") -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="W-FRI").strftime("%Y-%m-%d")
    return pd.Series(values, index=pd.Index(list(index), name="week"))


def _cell(ratio: float | None, p: float = 0.5) -> dict[str, object]:
    return {"ratio_to_chance": ratio, "p_value": p}


def _grid(values: dict[str, float]) -> dict[str, dict[str, object]]:
    return {str(h): {phase: _cell(v) for phase, v in values.items()} for h in C.HORIZONS}


# ── A: 실패를 실패로 읽는가 ──────────────────────────────────────────────────


def test_a_fails_when_expansion_collapses_toward_chance() -> None:
    """확장기가 1.0 쪽으로 내려가면 통을 옮긴 것이다. 그때 통과라고 하면 안 된다."""

    before = _grid({"recovery": 1.8, "expansion": 2.6, "slowdown": 0.4, "contraction": 2.0})
    after = _grid({"recovery": 1.8, "expansion": 1.05, "slowdown": 3.0, "contraction": 2.0})
    read = VD.read_a(before, after)
    assert read["passes"] is False
    assert "찌꺼기 통" in read["reading"]


def test_a_records_every_phase_that_degraded() -> None:
    """한 국면을 고치며 다른 국면을 깎았으면 그것은 교환이고, 교환은 적어야 한다."""

    before = _grid({"recovery": 1.8, "expansion": 2.6, "slowdown": 0.4, "contraction": 2.0})
    after = _grid({"recovery": 1.2, "expansion": 3.0, "slowdown": 3.0, "contraction": 2.0})
    read = VD.read_a(before, after)
    assert read["no_trade"] is False
    assert len(read["phases_that_degraded"]) == len(C.HORIZONS)
    assert all(item.startswith("recovery@") for item in read["phases_that_degraded"])


def test_a_covers_all_four_phases_at_every_horizon() -> None:
    """확장기만 보면 옮겨간 곳을 놓친다. 과제가 네 국면 전부를 요구했다."""

    grid = _grid({"recovery": 1.8, "expansion": 2.6, "slowdown": 0.4, "contraction": 2.0})
    rows = VD.read_a(grid, grid)["rows"]
    assert len(rows) == 4 * len(C.HORIZONS)
    assert {row["phase"] for row in rows} == {"recovery", "expansion", "slowdown", "contraction"}


# ── B: 봉우리와 평탄역을 가르는가 ───────────────────────────────────────────


def _curve(pairs: list[tuple[int, float, int]]) -> list[dict[str, object]]:
    return [
        {
            "persistence_weeks": weeks,
            "persistence_band": 1.0,
            "slowdown_discrimination": value,
            "slowdown_blocks": blocks,
            "enough_blocks_to_call_it_a_state": blocks >= C.MINIMUM_BLOCKS,
        }
        for weeks, value, blocks in pairs
    ]


def test_b_calls_a_single_spike_a_narrow_plateau() -> None:
    """한 점만 솟아 있으면 그 값은 표본에 맞춘 값일 위험이 있다."""

    curve = _curve([(5, 1.0, 20), (9, 1.0, 16), (13, 3.5, 12), (17, 1.0, 11), (21, 1.0, 7)])
    read = VD.read_b(curve, 13)
    assert read["plateau_is_wide"] is False
    assert read["peak_at_weeks"] == 13


def test_b_finds_the_plateau_when_neighbours_agree() -> None:
    """이웃이 서로 비슷하면 평탄역이고, 그 안에서 고른 값은 적합이 아니다."""

    curve = _curve([(5, 1.2, 28), (13, 2.4, 12), (15, 3.1, 11), (17, 3.3, 11), (21, 3.4, 7)])
    read = VD.read_b(curve, 17)
    assert read["plateau_is_wide"] is True
    assert read["chosen_is_in_the_plateau"] is True
    assert read["chosen_has_enough_blocks"] is True


def test_b_marks_a_choice_with_too_few_blocks() -> None:
    """블록이 적으면 판별력이 높아도 그것은 상태의 성질이 아니다."""

    curve = _curve([(13, 2.4, 12), (17, 3.3, 11), (21, 3.4, 7)])
    assert VD.read_b(curve, 21)["chosen_has_enough_blocks"] is False


# ── C: 유의를 유의로만 읽는가 ───────────────────────────────────────────────


def test_c_does_not_claim_significance_above_the_threshold() -> None:
    read = VD.read_c({}, {"slowdown_p": 0.104, "slowdown_discrimination": 2.08}, {"agreement": 0.9})
    assert read["significance_reached"] is False
    assert "확립되지 않았다" in read["reading"]


def test_c_reads_significance_below_the_threshold() -> None:
    read = VD.read_c(
        {}, {"slowdown_p": 0.0458, "slowdown_discrimination": 3.08}, {"agreement": 0.9}
    )
    assert read["significance_reached"] is True


# ── 확장 역사 ───────────────────────────────────────────────────────────────


def test_the_extended_cache_is_separate_from_the_frozen_cache() -> None:
    """동결 캐시를 덮어쓰면 v1.1 재현이 깨진다."""

    assert X.CACHE_DIR != "data/cache"
    assert X.CACHE_DIR.startswith("data/cache/")


def test_the_extended_run_uses_seven_series_and_no_more() -> None:
    """계열을 바꾸면 그것은 같은 모델의 더 긴 입력이 아니라 다른 모델이다."""

    assert len(X.SERIES) == 7
    assert "CMRMTSPL" in X.SERIES and "RRSFS" in X.SERIES


def test_overlap_is_reported_even_when_it_is_not_perfect() -> None:
    """100%가 아니라는 것을 감추면 A를 통과시킨 라벨과 C의 라벨이 같아 보인다."""

    a = _series(["expansion", "expansion", "slowdown"])
    b = _series(["expansion", "slowdown", "slowdown"])
    overlap = X.overlap_with_frozen(a, b)
    assert overlap["identical"] is False
    assert overlap["weeks_agreeing"] == 2
    assert overlap["overlapping_weeks"] == 3


# ── 한계 문구 ───────────────────────────────────────────────────────────────


def _payload(reached: bool) -> dict[str, object]:
    return {
        "a": {"phases_that_degraded": []},
        "b": {"chosen_weeks": 17, "plateau_weeks": [15, 17, 21]},
        "c": {
            "significance_reached": reached,
            "extended": {
                "weeks": 2614,
                "slowdown_blocks": 16,
                "slowdown_discrimination": 3.078,
                "slowdown_p": 0.0458 if reached else 0.104,
            },
            "overlap_with_frozen": {"agreement": 0.9654},
        },
    }


def test_limitations_name_the_consumption_break() -> None:
    """1992년 단절은 확장 실행이 균질하지 않다는 뜻이고, 그것은 한계다."""

    text = " ".join(VD.limitations(_payload(True)))
    assert X.CONSUMPTION_SPLIT.split("-")[0] in text
    assert "CMRMTSPL" in text


def test_limitations_say_it_plainly_when_significance_is_not_reached() -> None:
    text = " ".join(VD.limitations(_payload(False)))
    assert "확립되지 않았다" in text


def test_limitations_carry_the_overlap_number() -> None:
    text = " ".join(VD.limitations(_payload(True)))
    assert "96.5" in text


# ── 보고서에 실제로 실렸는가 ────────────────────────────────────────────────


def _summary() -> dict[str, object] | None:
    path = OUTPUT / "validation_summary.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_the_report_carries_all_four_phases() -> None:
    summary = _summary()
    if summary is None:
        return
    rows = summary["a"]["rows"]  # type: ignore[index]
    assert len(rows) == 4 * len(C.HORIZONS)


def test_the_report_says_which_gate_it_recommends() -> None:
    summary = _summary()
    if summary is None:
        return
    assert summary["revised_gate"] == SC.SlowdownGate(persistence_weeks=17).name  # type: ignore[index]


def test_the_frozen_config_hash_is_recorded() -> None:
    """어떤 임계값 위에서 잰 것인지 없으면 나중에 다시 확인할 수 없다."""

    summary = _summary()
    if summary is None:
        return
    assert len(str(summary["frozen_config_sha256"])) == 64  # type: ignore[index]
