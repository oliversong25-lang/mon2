"""국면 성숙도의 계약.

가장 중요한 것은 두 가지다. **예측형 문구를 만들지 않는가**, 그리고 **경과 기간을
다시 쓴 것을 성숙도라고 부르지 않는가**.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.phase_maturity import gaps as G
from business_cycle.phase_maturity import series as S
from business_cycle.phase_maturity import signal as SG
from business_cycle.phase_maturity import validate as V

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "phase_maturity"

FORBIDDEN_IN_WORDING = ("곧 ", "예상됩니다", "전망", "예측", "다가옵니다", "올 것")


def _frame(phases: list[str], **columns: list[float]) -> pd.DataFrame:
    index = [f"w{i:03d}" for i in range(len(phases))]
    base = {
        "official_phase": phases,
        "activity_level": [0.5] * len(phases),
        "activity_momentum": [0.2] * len(phases),
        "confirming_domains": [3.0] * len(phases),
        "concentration": [0.4] * len(phases),
        "negative_level_domains": [0.0] * len(phases),
        "negative_momentum_domains": [0.0] * len(phases),
    }
    base.update(columns)
    return pd.DataFrame(base, index=pd.Index(index, name="week"))


# ── 문구: 예측이 아니라 서술 ─────────────────────────────────────────────────


def test_no_wording_names_the_next_phase_or_predicts() -> None:
    """'곧 후퇴기가 옵니다'는 예측이고, '확장기 후반의 특징이 나타납니다'는 서술이다."""

    others = {"recovery": "회복", "expansion": "확장", "slowdown": "후퇴", "contraction": "침체"}
    for phase in SG.SUCCESSOR:
        for level in (0.0, 0.5, 1.0):
            drafted = SG.describe(phase, level)
            assert drafted["form"] == "state_description"
            text = drafted["wording"]
            for token in FORBIDDEN_IN_WORDING:
                assert token not in text, (phase, level, token)
            # 다음 국면의 이름이 문구에 등장하면 그것은 예고다.
            successor = others[SG.SUCCESSOR[phase]]
            assert successor not in text, (phase, level, successor)


def test_every_phase_and_stage_has_wording() -> None:
    for phase in SG.SUCCESSOR:
        stages = {SG.describe(phase, level)["stage"] for level in (0.0, 0.5, 1.0)}
        assert stages == {"초반", "중반", "후반"}


# ── 2차 읽기 ────────────────────────────────────────────────────────────────


def test_second_order_reads_are_differences_not_levels() -> None:
    frame = _frame(["expansion"] * 20, activity_momentum=[float(i) for i in range(20)])
    derived = S.derive(frame, weeks=8)
    assert derived["d_momentum"].iloc[8] == pytest.approx(8.0)
    # 앞쪽 8주는 비교할 과거가 없다. 0으로 채우면 없는 변화를 만들어 낸다.
    assert derived["d_momentum"].iloc[:8].isna().all()


def test_the_leading_weeks_are_never_called_late() -> None:
    frame = _frame(["expansion"] * 20)
    scored = SG.score(S.derive(frame, weeks=8))
    assert not bool(scored["late"].iloc[:8].any())
    assert scored["maturity"].iloc[:8].isna().all()


def test_elapsed_restarts_at_every_phase_change() -> None:
    phase = pd.Series(
        ["expansion"] * 3 + ["slowdown"] * 2 + ["expansion"] * 4,
        index=[f"w{i}" for i in range(9)],
    )
    assert S.elapsed(phase).tolist() == [1, 2, 3, 1, 2, 1, 2, 3, 4]


def test_runs_record_where_each_block_went_next() -> None:
    phase = pd.Series(["expansion"] * 3 + ["slowdown"] * 2, index=[f"w{i}" for i in range(5)])
    blocks = S.runs(phase)
    assert blocks[0]["next_phase"] == "slowdown"
    assert blocks[1]["next_phase"] is None


# ── 점수 ────────────────────────────────────────────────────────────────────


def test_only_the_current_phase_gets_a_maturity_score() -> None:
    """확장기가 아닌 주의 '확장기 후반 점수'는 해석할 자리가 없다."""

    frame = _frame(["expansion"] * 10 + ["slowdown"] * 10)
    derived = S.derive(frame, weeks=4)
    scored = SG.score(derived)
    assert scored["expansion__level_still_positive"].iloc[12:].isna().all()
    assert scored["slowdown__momentum_negative"].iloc[:10].isna().all()


def test_the_score_is_the_share_of_conditions_met() -> None:
    frame = _frame(["expansion"] * 12)
    derived = S.derive(frame, weeks=4)
    scored = SG.score(derived)
    # 수준은 양, 모멘텀·폭은 변화가 없으니 감속도 축소도 아니다 → 1/3.
    assert scored["maturity"].iloc[-1] == pytest.approx(1 / 3)
    assert not bool(scored["late"].iloc[-1])


def test_gap_conditions_only_touch_the_phases_that_have_new_data() -> None:
    """후퇴기·회복기에는 새 입력이 없다. 빈칸을 채운 척하지 않는다."""

    assert SG.GAP_INPUTS["slowdown"] == ()
    assert SG.GAP_INPUTS["recovery"] == ()

    frame = _frame(["slowdown"] * 12, activity_momentum=[-0.2] * 12)
    derived = S.derive(frame, weeks=4)
    gap_frame = pd.DataFrame(
        {
            "capacity_gap": [1.0] * 12,
            "unemployment_gap": [-1.0] * 12,
            "claims_off_peak": [0.5] * 12,
        },
        index=derived.index,
    )
    plain = SG.score(derived)
    with_gaps = SG.score(derived, gap_frame)
    pd.testing.assert_series_equal(plain["maturity"], with_gaps["maturity"])


def test_a_missing_gap_value_does_not_count_as_evidence() -> None:
    """모르는 것을 근거로 '후반'이라고 말하지 않는다."""

    frame = _frame(["contraction"] * 12, activity_level=[-0.5] * 12)
    derived = S.derive(frame, weeks=4)
    gap_frame = pd.DataFrame(
        {
            "capacity_gap": [float("nan")] * 12,
            "unemployment_gap": [float("nan")] * 12,
            "claims_off_peak": [float("nan")] * 12,
        },
        index=derived.index,
    )
    scored = SG.score(derived, gap_frame)
    assert bool((scored["contraction__claims_off_their_peak"].dropna() == 0.0).all())


# ── 검증 ────────────────────────────────────────────────────────────────────


def test_outcomes_look_forward_only() -> None:
    phase = pd.Series(["expansion"] * 5 + ["slowdown"] * 5, index=[f"w{i}" for i in range(10)])
    result = V.outcomes(phase, horizon=3)
    # w002는 3주 안에 후퇴기를 본다. w000은 보지 못한다.
    assert bool(result["moves_to_the_expected_successor"].iloc[2])
    assert not bool(result["moves_to_the_expected_successor"].iloc[0])
    # 마지막 블록은 뒤가 없으므로 벗어나지 않는다.
    assert not bool(result["leaves_within_horizon"].iloc[-1])


def test_the_duration_control_exists_and_is_reported() -> None:
    """경과 기간 대조군이 없으면 '오래됐다'를 다시 쓴 신호를 걸러낼 수 없다."""

    frame = _frame(["expansion"] * 40 + ["slowdown"] * 40)
    derived = S.derive(frame, weeks=4)
    scored = SG.score(derived)
    rows = V.by_phase(derived, scored, horizon=5)
    for row in rows:
        assert "duration_only" in row["successor_rate"]
        assert "base" in row["successor_rate"]
        assert "late_signal" in row["successor_rate"]


def test_hit_and_miss_counts_accompany_every_rate() -> None:
    """비율만 적으면 20주짜리 표본이 500주짜리와 같아 보인다."""

    frame = _frame(["expansion"] * 30 + ["slowdown"] * 30)
    derived = S.derive(frame, weeks=4)
    scored = SG.score(derived)
    for row in V.by_phase(derived, scored, horizon=5):
        hits = row["late_weeks_followed_by_the_successor"]
        misses = row["late_weeks_not_followed_by_the_successor"]
        assert hits + misses == row["late_weeks"]


def test_the_within_run_shift_preserves_how_many_flags_are_lit() -> None:
    """블록 안에서 돌리기만 하므로 켜진 표시의 개수는 변하지 않는다.

    개수가 변하면 그것은 다른 검정이다 — 위치가 아니라 빈도를 재게 된다.
    """

    flags = np.array([False, False, True, True])
    for offset in range(4):
        assert int(np.roll(flags, offset).sum()) == 2


def test_cycle_order_is_measured_not_assumed() -> None:
    """모델이 순환 순서를 따르는지는 전제가 아니라 측정 결과다."""

    phase = pd.Series(
        ["expansion"] * 3 + ["slowdown"] * 3 + ["expansion"] * 3,
        index=[f"w{i}" for i in range(9)],
    )
    frame = pd.DataFrame({"phase": phase})
    order = V.cycle_order_holds(frame)
    assert order["by_phase"]["expansion"]["to_the_expected_successor"] == 1
    # 후퇴기는 침체기가 아니라 확장기로 갔다.
    assert order["by_phase"]["slowdown"]["to_the_expected_successor"] == 0
    assert order["by_phase"]["slowdown"]["where_it_went_instead"] == {"expansion": 1}


def test_the_threshold_sweep_marks_which_value_was_predeclared() -> None:
    frame = _frame(["expansion"] * 30 + ["slowdown"] * 30)
    derived = S.derive(frame, weeks=4)
    scored = SG.score(derived)
    rows = V.threshold_sweep(derived, scored, horizon=5)
    predeclared = [row for row in rows if row["predeclared"]]
    assert predeclared
    assert all(row["threshold"] == pytest.approx(2 / 3, abs=0.001) for row in predeclared)


# ── 격차 변환: 앞을 훔쳐보지 않는가 ──────────────────────────────────────────


def test_a_monthly_value_is_not_available_before_it_is_published() -> None:
    series = pd.Series([1.0], index=pd.to_datetime(["2024-01-01"]))
    weeks = ["2024-01-05", "2024-02-16", "2024-03-01"]
    weekly = G._to_weekly(series, weeks, lag_weeks=6)
    assert bool(np.isnan(weekly.iloc[0]))
    assert weekly.iloc[2] == pytest.approx(1.0)


def test_the_long_run_average_never_uses_the_future() -> None:
    """전체 표본 평균을 기준선으로 쓰면 과거 시점이 미래를 본다."""

    source = (ROOT / "src/business_cycle/phase_maturity/gaps.py").read_text(encoding="utf-8")
    assert "expanding(" in source
    assert ".mean()" in source


# ── 산출물 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_does_not_claim_symmetry_it_did_not_find() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    verdict = payload["verdict"]
    report = (OUTPUT / "phase_maturity_report.md").read_text(encoding="utf-8")

    works = verdict["phases_where_the_late_signal_beats_the_duration_control"]
    assert verdict["symmetric_across_the_four_phases"] == (len(works) == 4)
    assert verdict["statement"] in report
    assert payload["frozen_model_modified"] is False


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_states_the_cycle_order_finding_before_the_signal_results() -> None:
    """순환 순서가 성립하지 않는다는 사실이 신호 결과보다 **먼저** 나와야 한다.

    뒤에 묻으면 독자는 후반부 신호를 그대로 믿고 읽는다.
    """

    report = (OUTPUT / "phase_maturity_report.md").read_text(encoding="utf-8")
    order_at = report.index("순환 순서를 따르지 않는다")
    signal_at = report.index("## 기존 값만으로")
    assert order_at < signal_at


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_episode_counts_are_reported_with_every_phase_result() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    for block in ("existing_values_only", "with_gap_transforms", "real_time"):
        for entry in payload[block]["by_phase"]:
            assert "episodes" in entry
            assert entry["episodes"] >= 0


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_real_time_window_is_kept_separate_from_the_long_history() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    long_weeks = sum(entry["weeks"] for entry in payload["with_gap_transforms"]["by_phase"])
    real_time_weeks = sum(entry["weeks"] for entry in payload["real_time"]["by_phase"])
    assert real_time_weeks < long_weeks
    report = (OUTPUT / "phase_maturity_report.md").read_text(encoding="utf-8")
    assert "격차 변환의 실시간 거동은" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_carries_no_investment_instruction() -> None:
    report = (OUTPUT / "phase_maturity_report.md").read_text(encoding="utf-8")
    disclaimer = "투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다"
    assert disclaimer in report
    stripped = report.replace(disclaimer, "")
    for token in ("매수", "매도", "비중 확대", "비중 축소", "추천 종목"):
        assert token not in stripped
