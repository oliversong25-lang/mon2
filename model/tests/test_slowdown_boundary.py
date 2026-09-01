"""후퇴기 경계 변형의 계약.

첫 번째이자 가장 중요한 계약은 **재현**이다. 경계를 끈 변형이 동결 v1.1과 다르면
이 패키지의 모든 비교가 무의미하다.

두 번째는 **동결 코드 불가침**이다. `four_phase` 아래를 건드리면 v1.1이 v1.1이 아니게
된다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.slowdown_boundary import maturity as MT
from business_cycle.slowdown_boundary import metrics as M
from business_cycle.slowdown_boundary import natural as N
from business_cycle.slowdown_boundary import scoring as SC
from business_cycle.slowdown_boundary import select as SEL

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "slowdown_boundary"


def _phase(values: list[str], start: str = "2000-01-07") -> pd.Series:
    index = pd.date_range(start, periods=len(values), freq="W-FRI").strftime("%Y-%m-%d")
    return pd.Series(values, index=pd.Index(list(index), name="week"))


# ── 동결 불가침 ─────────────────────────────────────────────────────────────


def test_the_frozen_engine_is_not_modified() -> None:
    """`four_phase` 아래가 이 브랜치에서 바뀌었으면 v1.1이 아니다."""

    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "src/business_cycle/four_phase", "configs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git 상태를 읽을 수 없다")
    assert result.stdout.strip() == "", result.stdout


# ── 게이트를 끄면 아무 일도 없어야 한다 ──────────────────────────────────────


def test_a_disabled_gate_is_exactly_one() -> None:
    """1.0이 아니면 감쇠가 0이 아니고, 그러면 v1.1을 재현할 수 없다."""

    from business_cycle.config import load_settings
    from business_cycle.four_phase.engine import load_config

    thresholds = load_config(load_settings()).thresholds
    off = SC.SlowdownGate()
    assert off.enabled is False
    assert off.name == "boundary:off"
    for momentum in (-2.0, -0.1, 0.0, 0.5):
        evidence = SC.slowdown_evidence(momentum, 0, 0, off, thresholds)
        assert evidence["slowdown_evidence"] == 1.0


def test_the_scoring_step_is_a_no_op_when_the_gate_is_off() -> None:
    """감쇠를 끈 관측 점수가 동결 함수와 한 값도 다르지 않아야 한다."""

    from business_cycle.config import load_settings
    from business_cycle.four_phase import evidence as E
    from business_cycle.four_phase.engine import load_config

    thresholds = load_config(load_settings()).thresholds
    breadth = {"expansion": 0.4, "slowdown": 0.2, "recovery": 0.1, "contraction": 0.3}
    rng = np.random.default_rng(5)
    for _ in range(50):
        level = float(rng.normal(0.0, 1.0))
        momentum = float(rng.normal(0.0, 1.0))
        contraction = float(rng.uniform(0.0, 1.0))
        recovery = float(rng.uniform(0.0, 1.0))
        frozen = E.observation_scores(level, momentum, contraction, recovery, breadth, thresholds)
        variant = SC.observation_scores(
            level, momentum, contraction, recovery, 1.0, breadth, thresholds
        )
        for name, value in frozen.items():
            assert variant[name] == pytest.approx(value, abs=1e-15), name


def test_the_withheld_share_goes_to_expansion_only() -> None:
    """회복·침체에 넘기면 그들의 게이트를 우회한다. 2020년 1~3월에 실제로 그랬다."""

    from business_cycle.config import load_settings
    from business_cycle.four_phase.engine import load_config

    thresholds = load_config(load_settings()).thresholds
    breadth = {"expansion": 0.0, "slowdown": 0.0, "recovery": 0.0, "contraction": 0.0}
    strong = SC.observation_scores(1.0, -1.0, 0.0, 0.0, 1.0, breadth, thresholds)
    damped = SC.observation_scores(1.0, -1.0, 0.0, 0.0, 0.0, breadth, thresholds)

    assert damped["slowdown"] < strong["slowdown"]
    assert damped["expansion"] > strong["expansion"]
    # 정규화 때문에 회복·침체의 **비율**은 오를 수 있지만, 확장기가 가져간 몫보다
    # 훨씬 작아야 한다. 후퇴기가 잃은 것이 확장기로 갔는지를 본다.
    lost = strong["slowdown"] - damped["slowdown"]
    gained = damped["expansion"] - strong["expansion"]
    assert gained > lost * 0.5


# ── 자연 실험 ───────────────────────────────────────────────────────────────


def test_reversion_counts_a_return_to_the_previous_phase() -> None:
    phase = _phase(["expansion"] * 5 + ["slowdown"] * 3 + ["expansion"] * 5)
    rows = {row["phase"]: row for row in N.by_phase(phase)}
    assert rows["slowdown"]["episodes_that_revert_to_the_previous_phase"] == 1
    assert rows["slowdown"]["reversion_rate"] == 1.0


def test_the_reading_names_which_metric_it_used_and_why() -> None:
    """갈라 주지 않는 지표로 고르면 뒤 결과를 해석할 수 없다."""

    phase = _phase(["expansion"] * 20 + ["slowdown"] * 10 + ["expansion"] * 20)
    reading = N.read(N.by_phase(phase))
    assert "why_not_short_rate" in reading
    assert "what_this_does_not_say" in reading


# ── 결정적 지표 ─────────────────────────────────────────────────────────────


def test_progression_separates_going_forward_from_reverting() -> None:
    phase = _phase(
        ["expansion"] * 4
        + ["slowdown"] * 3
        + ["contraction"] * 4
        + ["slowdown"] * 3
        + ["expansion"] * 4
    )
    result = M.progression(phase)
    assert result["closed_slowdown_blocks"] == 2
    assert result["progressed_to_contraction"] == 1
    assert result["reverted_to_expansion"] == 1
    assert result["progression_rate"] == 0.5


def test_recognition_is_measured_against_a_sustained_call_not_a_blip() -> None:
    """1주짜리 깜빡임을 기준으로 삼으면, 그것을 막는 것이 '지연'으로 기록된다."""

    weeks = pd.date_range("2020-01-03", periods=20, freq="W-FRI").strftime("%Y-%m-%d")
    blip = ["slowdown"] * 3 + ["contraction"] + ["slowdown"] * 4 + ["contraction"] * 12
    clean = ["slowdown"] * 8 + ["contraction"] * 12
    baseline = pd.Series(blip, index=pd.Index(list(weeks), name="week"))
    variant = pd.Series(clean, index=pd.Index(list(weeks), name="week"))
    result = M.recognition(variant, baseline)
    delays = [row["delay_weeks"] for row in result["contraction"]["calls"]]
    assert all(delay == 0 for delay in delays if delay is not None), delays


def test_a_phase_with_no_weeks_gets_no_discrimination_ratio() -> None:
    weeks = 400
    phase = _phase(["expansion"] * weeks)
    rng = np.random.default_rng(3)
    panel = pd.DataFrame(
        rng.normal(0.0, 0.02, size=(weeks, 3)),
        index=phase.index,
        columns=["A", "B", "C"],
    )
    result = M.discrimination(phase, panel)
    assert result["slowdown"]["ratio_to_chance"] is None
    assert result["slowdown"]["p_value"] is None


# ── 선택 규칙 ───────────────────────────────────────────────────────────────


def _candidate(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "gate": "x",
        "discrimination": {"slowdown": {"ratio_to_chance": 1.5, "p_value": 0.2}},
        "progression": {"progression_rate": 0.2},
        "recognition": {
            "contraction": {"max_delay_weeks": 0, "never_called_somewhere": False},
            "recovery": {"max_delay_weeks": 0, "never_called_somewhere": False},
        },
        "nber": {"false_positive_episodes": 5},
        "breadth_gate_holds": True,
    }
    row.update(overrides)
    return row


def test_a_delayed_recognition_is_excluded_however_good_the_discrimination() -> None:
    good = _candidate(
        gate="delayed",
        discrimination={"slowdown": {"ratio_to_chance": 9.0, "p_value": 0.001}},
        recognition={
            "contraction": {"max_delay_weeks": 3, "never_called_somewhere": False},
            "recovery": {"max_delay_weeks": 0, "never_called_somewhere": False},
        },
    )
    ranked = SEL.rank([good, _candidate(gate="clean")])
    assert [row["gate"] for row in ranked] == ["clean"]
    assert good["admissible"] is False
    assert good["violations"]


def test_more_false_positive_episodes_than_the_baseline_is_excluded() -> None:
    noisy = _candidate(gate="noisy", nber={"false_positive_episodes": 6})
    ranked = SEL.rank([noisy, _candidate(gate="clean")])
    assert [row["gate"] for row in ranked] == ["clean"]


def test_a_broken_breadth_gate_is_excluded() -> None:
    broken = _candidate(gate="broken", breadth_gate_holds=False)
    assert SEL.rank([broken]) == []


def test_discrimination_ranks_first_and_progression_breaks_ties() -> None:
    high = _candidate(
        gate="high",
        discrimination={"slowdown": {"ratio_to_chance": 2.0, "p_value": 0.1}},
        progression={"progression_rate": 0.05},
    )
    low = _candidate(
        gate="low",
        discrimination={"slowdown": {"ratio_to_chance": 1.0, "p_value": 0.5}},
        progression={"progression_rate": 0.9},
    )
    assert [row["gate"] for row in SEL.rank([low, high])] == ["high", "low"]

    tie_a = _candidate(gate="tie_a", progression={"progression_rate": 0.1})
    tie_b = _candidate(gate="tie_b", progression={"progression_rate": 0.3})
    assert [row["gate"] for row in SEL.rank([tie_a, tie_b])] == ["tie_b", "tie_a"]


# ── 성숙도 ──────────────────────────────────────────────────────────────────


def test_the_block_median_never_looks_at_its_own_week() -> None:
    """자기 자신을 포함하면 한 주짜리 블록에서 조건이 항상 거짓이 된다."""

    phase = _phase(["expansion"] * 20)
    level = pd.Series(np.arange(20, dtype=float), index=phase.index)
    reference = MT.causal_block_median(level, phase, "expansion")
    assert bool(reference.iloc[: MT.MINIMUM_PRIOR_WEEKS].isna().all())
    assert float(reference.iloc[MT.MINIMUM_PRIOR_WEEKS]) < float(level.iloc[MT.MINIMUM_PRIOR_WEEKS])


def test_the_block_median_restarts_at_each_expansion() -> None:
    """국면 전체 역사를 쓰면 약한 확장기의 모든 주가 기준선 아래가 된다."""

    values = ["expansion"] * 20 + ["slowdown"] * 3 + ["expansion"] * 20
    phase = _phase(values)
    level = pd.Series([5.0] * 20 + [0.0] * 3 + [-1.0] * 20, index=phase.index, dtype=float)
    reference = MT.causal_block_median(level, phase, "expansion")
    # 두 번째 블록의 기준선은 -1.0 부근이어야 한다. 첫 블록의 5.0을 끌고 오면 안 된다.
    tail = reference.iloc[23 + MT.MINIMUM_PRIOR_WEEKS :].dropna()
    assert tail.max() < 0.0


def test_the_wording_layer_separates_weak_from_early() -> None:
    """점수를 건드리지 않고 서술에서 가른다. 예측력을 깎지 않기 위해서다."""

    weak = MT.stage_with_strength(0.25, -0.25, 2 / 3)
    early = MT.stage_with_strength(0.25, 0.60, 2 / 3)
    assert weak["stage"] == early["stage"] == "초반"
    assert weak["sub_normal"] is True and early["sub_normal"] is False
    assert weak["reads_as_early_but_is_weak"] is True
    assert weak["wording_prefix"] and not early["wording_prefix"]


# ── 산출물 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_artifact_records_an_exact_reproduction_of_v1_1() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    reproduction = payload["reproduction"]
    assert reproduction["reproduces"] is True
    assert reproduction["weeks_agreeing"] == reproduction["weeks_compared"]
    assert payload["frozen_model_modified"] is False


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_every_matrix_cell_keeps_the_must_not_break_conditions() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    for key, cell in payload["matrix"].items():
        assert cell["breadth_gate_holds"] is True, key
        for name in ("contraction", "recovery"):
            delay = cell["recognition"][name]["max_delay_weeks"]
            assert delay is None or delay <= 0, (key, name, delay)
        assert cell["nber"]["false_positive_episodes"] <= SEL.BASELINE_FALSE_POSITIVE_EPISODES, key


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_recommended_cell_lifts_all_three_decisive_metrics() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    baseline = payload["matrix"]["baseline"]
    chosen = payload["matrix"]["boundary_only"]
    assert (
        chosen["discrimination"]["slowdown"]["ratio_to_chance"]
        > baseline["discrimination"]["slowdown"]["ratio_to_chance"]
    )
    assert (
        chosen["shape"]["phase_shares"]["slowdown"] < baseline["shape"]["phase_shares"]["slowdown"]
    )
    assert chosen["progression"]["progression_rate"] > baseline["progression"]["progression_rate"]
    assert chosen["discrimination"]["slowdown"]["ratio_to_chance"] >= SEL.DISCRIMINATION_TARGET


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_states_the_p_value_alongside_the_ratio() -> None:
    """비율이 1을 넘었다는 것과 우연과 구분된다는 것은 다른 말이다."""

    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    report = (OUTPUT / "slowdown_boundary_report.md").read_text(encoding="utf-8")
    p_value = payload["matrix"]["boundary_only"]["discrimination"]["slowdown"]["p_value"]
    assert str(p_value) in report
    assert "우연과 통계적으로 구분되지는 않는다" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_shows_both_axes_separately() -> None:
    """둘을 한꺼번에 걸면 어느 쪽이 일했는지 보이지 않는다."""

    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    assert set(payload["matrix"]) == {
        "baseline",
        "gate_only",
        "boundary_only",
        "boundary_and_gate",
    }
    report = (OUTPUT / "slowdown_boundary_report.md").read_text(encoding="utf-8")
    assert "2x2" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_maturity_section_reports_the_count_before_and_after() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    maturity = payload["maturity"]
    before = maturity["before_boundary"]["negative_level_expansion_weeks"]
    after = maturity["after_boundary"]["negative_level_expansion_weeks"]
    assert before > 0 and after > before
    assert maturity["relative_condition_was_tried_and_rejected"]["why"]


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_carries_no_investment_instruction() -> None:
    report = (OUTPUT / "slowdown_boundary_report.md").read_text(encoding="utf-8")
    disclaimer = "투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다"
    assert disclaimer in report
    stripped = report.replace(disclaimer, "")
    for token in ("매수", "매도", "비중 확대", "추천 종목"):
        assert token not in stripped
