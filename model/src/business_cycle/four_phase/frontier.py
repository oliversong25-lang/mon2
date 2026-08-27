"""§7의 폭 제약 개발 프런티어.

**공식 침체는 독립적인 동행 도메인 둘 이상의 확인을 요구한다**를 고정한 채, 이미
정의된 경제적 해석이 가능한 모수 공간만 전수로 훑는다. 새 지표, 자유 가중치, 날짜
규칙, 특정 사건 예외를 만들지 않는다.

두 단계로 나눈다. 1단계는 임계값 공간을 승계 필터(λ=1.8, ε=0.01)와 고정 확인 규칙
아래에서 전수로 본다. 2단계는 1단계를 통과한 상위 임계값 조합에 대해 필터·확인 모수를
전수로 본다. 나누는 이유는 계산량뿐이고, 두 단계 모두 생산 코드(`observation_layer`
→ `decide`)를 그대로 쓴다.

선택 규칙은 **결과를 보기 전에** 아래에 코드로 박아 둔다. 2020년과 2026년은 이
단계에서 보지 않는다.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from ..config import Settings, load_baseline, load_settings
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import evidence as E
from . import validation as V
from .contract import PHASES
from .engine import (
    STOPPED_CONFIG_NAME,
    FourPhaseConfig,
    ObservationLayer,
    PreparedInputs,
    decide,
    load_config,
    observation_layer,
    prepare,
)

FROZEN_BASELINE = "candidate_h_breadth_gate"
AS_OF = pd.Timestamp(END)
DEVELOPMENT = (pd.Timestamp("1995-01-01"), pd.Timestamp("2012-12-31"))

#: §2가 고정한 값. 탐색 대상이 아니다.
MINIMUM_COINCIDENT_DOMAINS: Final[int] = 2

#: 이미 정의된 임계값의 격자. 새 모수를 만들지 않는다.
THRESHOLD_GRID: Final[dict[str, tuple[Any, ...]]] = {
    "level_severity": (1.4, 1.6, 1.7915, 2.0, 2.3, 2.6),
    "momentum_severity": (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.2954),
    "level_breadth": (2, 3, 4),
    "momentum_breadth": (2, 3, 4),
    "contraction_entry": (0.70, 0.75, 0.80, 0.8818, 0.92),
    "corroboration_share": (0.10, 0.15, 0.20),
}

#: 1단계에서 쓰는 승계 필터와 고정 확인 규칙.
STAGE_ONE_FILTER: Final[tuple[float, float, int, float]] = (1.8, 0.01, 2, 0.30)

#: 2단계의 필터·확인 격자.
FILTER_GRID: Final[tuple[tuple[float, float, int, float], ...]] = tuple(
    itertools.product((1.2, 1.8, 2.5), (0.005, 0.01, 0.02), (2, 3), (0.20, 0.30, 0.40))
)

#: 2단계로 넘길 1단계 상위 조합 수.
STAGE_TWO_WIDTH: Final[int] = 100

#: 개발구간 안에 있는 NBER 침체 시작일. 타이밍을 재기 위한 기준일 뿐 모델 로직에
#: 들어가지 않는다. 2020년과 2026년은 여기 없다 — 이 단계에서 보지 않는다.
DEVELOPMENT_EPISODES: Final[dict[str, str]] = {
    "recession_2001": "2001-03-01",
    "gfc": "2007-12-01",
}

#: §7의 필수 기준. 하나라도 어기면 실현 불가능이다.
#:
#: 금융위기 진입 시점을 §7의 목록에 더해 **필수**로 둔다. 이유는 셋이다. §7이 모든
#: 실현 가능 조합에 대해 "금융위기 개발 타이밍"을 계산하라고 이미 요구하고, 금융위기는
#: 개발구간(1995~2012) **안에** 있어 홀드아웃 정보가 아니며, §12가 이것을 필수 게이트로
#: 두므로 여기서 걸러 두지 않으면 반드시 실패할 설정을 골라 동결하게 된다.
GATES: Final[dict[str, float]] = {
    "overall_recall_minimum": 0.80,
    "core_recall_minimum": 0.90,
    "false_positive_rate_maximum": 0.05,
    "two_step_transitions_maximum": 5,
    "three_week_whipsaws_maximum": 5,
    "gfc_first_official_contraction_maximum_weeks": 10,
    # §12의 "원시-공식 불일치는 유계여야 한다"와 "H식 다년 고착 없음"을 하나의 수로
    # 만든다. 반년 넘게 현재 관측과 어긋난 현재상태 판정은 현재상태 판정이 아니다.
    # 후보 H는 이 값이 133주였다.
    "longest_disagreement_run_maximum": 26,
}

#: 선택 규칙. 실현 가능한 조합 중에서 이 순서로 고른다. 결과를 보기 전에 정했다.
#:
#: 첫 번째 키가 지속된 오탐 구간인 이유는 사용자가 §1에서 밝힌 근거 그대로다 — 한
#: 극단 도메인이 만든 거짓 공식 침체가, 폭 확인을 기다리며 경보를 투명하게 내는 것보다
#: 더 해롭다. §6도 고립된 몇 주와 지속된 오탐 구간을 같게 다루지 말라고 요구한다.
SELECTION_ORDER: Final[tuple[tuple[str, bool], ...]] = (
    ("four_week_confirmed_false_positive_episodes", True),
    ("overall_recall", False),
    ("false_positive_rate", True),
    ("changed_parameters", True),
)


@dataclass(frozen=True)
class FrontierInputs:
    prepared: PreparedInputs
    recession: pd.Series
    development: pd.DatetimeIndex
    config: FourPhaseConfig


def load_inputs(settings: Settings | None = None) -> FrontierInputs:
    base = settings or load_settings()
    config = load_config(base, STOPPED_CONFIG_NAME)
    frozen = load_baseline(FROZEN_BASELINE, base)
    core, source = load_core_observations(base)
    prepared = prepare(core, frozen, AS_OF, config)
    recession = _official_recession_flags(source, prepared.index)
    development = prepared.index[
        (prepared.index >= DEVELOPMENT[0]) & (prepared.index <= DEVELOPMENT[1])
    ]
    return FrontierInputs(prepared, recession.fillna(False).astype(bool), development, config)


def thresholds_from(base: E.Thresholds, values: dict[str, Any]) -> E.Thresholds:
    """격자 한 점을 임계값 묶음으로. §2의 최소 도메인 수는 항상 고정이다."""

    document = base.to_dict()
    document.update(values)
    document["minimum_coincident_domains"] = MINIMUM_COINCIDENT_DOMAINS
    return E.Thresholds(**document)


def assess(
    inputs: FrontierInputs,
    observation: ObservationLayer,
    lam: float,
    epsilon: float,
    confirmation_weeks: int,
    immediate_margin: float,
    separation_floor: float,
) -> dict[str, Any]:
    """한 조합의 개발구간 성적 전부. §7이 요구한 항목을 하나도 합치지 않는다."""

    run = decide(
        inputs.prepared,
        observation,
        lam,
        epsilon,
        confirmation_weeks,
        immediate_margin,
        separation_floor,
    )
    window = inputs.development
    phase = run.official_phase.reindex(window)
    truth = inputs.recession.reindex(window)
    row: dict[str, Any] = {
        "lam": lam,
        "epsilon": epsilon,
        "confirmation_weeks": confirmation_weeks,
        "immediate_margin": immediate_margin,
    }
    row.update(V.recession_metrics(phase, truth))
    stability = V.stability(phase)
    row.update(
        {
            k: v
            for k, v in stability.items()
            if k not in ("phase_occupancy", "phase_entries", "phase_exits")
        }
    )
    row.update({f"occupancy_{k}": v for k, v in stability["phase_occupancy"].items()})
    row.update({f"entries_{k}": v for k, v in stability["phase_entries"].items()})
    raw = run.raw_phase.reindex(window)
    row["raw_official_disagreement_rate"] = round(float(raw.ne(phase).mean()), 6)
    row["longest_disagreement_run"] = V.longest_disagreement(raw, phase)

    # §2의 금지가 실제로 지켜졌는지 결과에서 다시 센다. 게이트를 믿지 않고 확인한다.
    confirming = run.confirming_domains.reindex(window)
    row["single_domain_official_contraction_weeks"] = int(
        (phase.eq("contraction") & confirming.lt(MINIMUM_COINCIDENT_DOMAINS)).sum()
    )
    ordered = np.sort(run.filtered_scores.reindex(window).to_numpy(dtype=float), axis=1)
    separation = pd.Series(ordered[:, -1] - ordered[:, -2], index=window)
    # 증거가 약한 사유의 개수. `current_output`의 품질 판정과 같은 넷을 센다.
    reasons = (
        observation.neutral_both.reindex(window).astype(int)
        + observation.crowded.reindex(window).astype(int)
        + observation.stale.reindex(window).astype(int)
        + separation.lt(separation_floor).astype(int)
    )
    monotone = V.certainty_monotonicity(separation, reasons)
    row["certainty_no_inversion"] = bool(monotone["no_inversion"])
    row["mean_separation_high"] = monotone["mean_separation"]["high"]
    row["mean_separation_low"] = monotone["mean_separation"]["low"]

    delays = V.transition_delays(
        run.filtered_winner.reindex(window), run.official_phase.reindex(window)
    )
    summary = V.delay_summary(delays)
    row["median_transition_delay_weeks"] = summary["median_delay_weeks"]
    row["maximum_transition_delay_weeks"] = summary["maximum_delay_weeks"]
    row["immediate_transitions"] = summary["immediate_transitions"]
    row["confirmed_transitions"] = summary["confirmed_transitions"]

    alert = run.alert_level.reindex(window)
    for name, start in DEVELOPMENT_EPISODES.items():
        timing = V.signal_timing(phase, alert, raw, pd.Timestamp(start))
        for key in (
            "first_recession_alert_weeks",
            "first_raw_contraction_weeks",
            "first_official_contraction_weeks",
            "four_week_confirmed_contraction_weeks",
        ):
            weeks = timing.get(key)
            row[f"{name}_{key}"] = float("inf") if weeks is None else float(weeks)
    return row


def failed_gates(row: dict[str, Any]) -> list[str]:
    """이 조합이 어긴 필수 기준 전부. 기각 사유를 게이트별로 세기 위한 것이다."""

    failures: list[str] = []
    if row["single_domain_official_contraction_weeks"] != 0:
        failures.append("single_domain_official_contraction")
    if row["overall_recall"] < GATES["overall_recall_minimum"]:
        failures.append("overall_recall")
    if row["core_recall"] < GATES["core_recall_minimum"]:
        failures.append("core_recall")
    if row["false_positive_rate"] > GATES["false_positive_rate_maximum"]:
        failures.append("false_positive_rate")
    if row["two_step_transitions"] > GATES["two_step_transitions_maximum"]:
        failures.append("two_step_transitions")
    if row["three_week_whipsaws"] > GATES["three_week_whipsaws_maximum"]:
        failures.append("three_week_whipsaws")
    if len(row["phases_reached"]) != len(PHASES):
        failures.append("all_phases_reachable")
    if row["unexited_phases"]:
        failures.append("no_unexited_phase")
    if row["longest_disagreement_run"] > GATES["longest_disagreement_run_maximum"]:
        failures.append("longest_disagreement_run")
    if not row["certainty_no_inversion"]:
        failures.append("low_evidence_certainty_inversion")
    if (
        row["gfc_first_official_contraction_weeks"]
        > GATES["gfc_first_official_contraction_maximum_weeks"]
    ):
        failures.append("gfc_first_official_contraction")
    return failures


def is_feasible(row: dict[str, Any]) -> bool:
    """§7의 필수 기준 전부를 동시에 만족하는가.

    "실질적 흡수 상태 없음"은 두 가지로 본다. 나가지 못한 국면이 없어야 하고(건전성
    불변식), 공식 국면이 원시 관측과 어긋난 채 버틴 최장 구간이 반년을 넘지 않아야
    한다. 후보 H를 가둔 것은 뒤쪽이었다 — 133주였다.
    """

    return not failed_gates(row)


def _sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple((row[name] if ascending else -row[name]) for name, ascending in SELECTION_ORDER)


def search(settings: Settings | None = None, progress: bool = True) -> pd.DataFrame:
    """2단계 프런티어. 개발구간만 본다."""

    inputs = load_inputs(settings)
    base = inputs.config.thresholds
    stopped = base.to_dict()
    names = list(THRESHOLD_GRID)
    points = list(itertools.product(*[THRESHOLD_GRID[name] for name in names]))
    lam, epsilon, confirmation, margin = STAGE_ONE_FILTER
    floor = inputs.config.separation_floor

    # 관측 층을 조합마다 들고 있으면 메모리가 터진다. 2단계에서 필요한 상위 조합만
    # 다시 만든다 — 다시 만드는 값이 같다는 것은 결정적 계산이므로 보장된다.
    stage_one: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    sole_reason: dict[str, int] = {}
    evaluated = 0
    for position, point in enumerate(points):
        values = dict(zip(names, point, strict=True))
        thresholds = thresholds_from(base, values)
        observation = observation_layer(inputs.prepared, thresholds, inputs.config.stale_weeks)
        row = assess(inputs, observation, lam, epsilon, confirmation, margin, floor)
        row.update(values)
        row["changed_parameters"] = sum(
            1 for name, value in values.items() if stopped.get(name) != value
        )
        row["stage"] = 1
        evaluated += 1
        failures = failed_gates(row)
        if failures:
            for name in failures:
                rejected[name] = rejected.get(name, 0) + 1
            if len(failures) == 1:
                sole_reason[failures[0]] = sole_reason.get(failures[0], 0) + 1
        else:
            stage_one.append(row)
        if progress and position % 250 == 0:
            print(f"  1단계 {position}/{len(points)} · 실현가능 {len(stage_one)}", flush=True)
    if progress:
        print(f"  1단계 완료 · {len(points)}개 중 실현가능 {len(stage_one)}", flush=True)

    stage_one.sort(key=_sort_key)
    rows: list[dict[str, Any]] = list(stage_one)
    for row in stage_one[:STAGE_TWO_WIDTH]:
        values = {name: row[name] for name in names}
        observation = observation_layer(
            inputs.prepared, thresholds_from(base, values), inputs.config.stale_weeks
        )
        for lam2, epsilon2, confirmation2, margin2 in FILTER_GRID:
            if (lam2, epsilon2, confirmation2, margin2) == STAGE_ONE_FILTER:
                continue
            candidate = assess(inputs, observation, lam2, epsilon2, confirmation2, margin2, floor)
            candidate.update(values)
            candidate["changed_parameters"] = row["changed_parameters"]
            candidate["stage"] = 2
            evaluated += 1
            failures = failed_gates(candidate)
            if failures:
                for name in failures:
                    rejected[name] = rejected.get(name, 0) + 1
                if len(failures) == 1:
                    sole_reason[failures[0]] = sole_reason.get(failures[0], 0) + 1
            else:
                rows.append(candidate)
    if progress:
        print(f"  2단계 완료 · 실현가능 누계 {len(rows)}", flush=True)
    frame = pd.DataFrame(rows)
    frame["phases_reached"] = frame["phases_reached"].map(lambda names: ",".join(names))
    frame = frame.drop(columns=["unexited_phases"])
    frame.attrs["evaluated"] = evaluated
    frame.attrs["stage_one_points"] = len(points)
    frame.attrs["rejected_by_gate"] = rejected
    frame.attrs["sole_rejection_reason"] = sole_reason
    frame.attrs["warmup"] = [
        str(inputs.prepared.index[0].date()),
        str(inputs.development[0].date()),
    ]
    return frame.sort_values(
        [name for name, _ in SELECTION_ORDER],
        ascending=[ascending for _, ascending in SELECTION_ORDER],
    ).reset_index(drop=True)


#: §3이 나열한 절충 축. 파레토 앞면을 이 축들에서 잰다.
TRADEOFF_AXES: Final[tuple[tuple[str, bool], ...]] = (
    ("overall_recall", False),
    ("core_recall", False),
    ("false_positive_rate", True),
    ("gfc_first_official_contraction_weeks", True),
    ("two_step_transitions", True),
    ("three_week_whipsaws", True),
    ("four_week_confirmed_false_positive_episodes", True),
    ("median_transition_delay_weeks", True),
    ("longest_disagreement_run", True),
)


def source_commit() -> str:
    """이 산출물을 만든 소스 커밋. 얻지 못하면 숨기지 않고 그렇게 적는다."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except Exception:
        return "unavailable"
    return result.stdout.strip()


def selection_rule_digest() -> str:
    """선택 규칙과 게이트의 지문. 결과를 본 뒤 바뀌지 않았음을 확인할 수 있게 한다."""

    payload = json.dumps(
        {
            "selection_order": [[name, ascending] for name, ascending in SELECTION_ORDER],
            "gates": GATES,
            "minimum_coincident_domains": MINIMUM_COINCIDENT_DOMAINS,
            "threshold_grid": {key: list(values) for key, values in THRESHOLD_GRID.items()},
            "filter_grid": [list(item) for item in FILTER_GRID],
            "stage_one_filter": list(STAGE_ONE_FILTER),
            "stage_two_width": STAGE_TWO_WIDTH,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ranks(frame: pd.DataFrame, selected: dict[str, Any]) -> dict[str, Any]:
    """선택된 행이 각 지표에서 몇 등인지. 한 축만 좋아 보이는 것을 막는다."""

    out: dict[str, Any] = {}
    for name, ascending in TRADEOFF_AXES:
        if name not in frame.columns:
            continue
        ordered = frame[name].rank(ascending=ascending, method="min")
        position = frame.index[(frame[name] == selected[name])]
        out[name] = {
            "value": selected[name],
            "rank": int(ordered.loc[position[0]]) if len(position) else None,
            "of": int(len(frame)),
            "best": float(frame[name].min() if ascending else frame[name].max()),
            "worst": float(frame[name].max() if ascending else frame[name].min()),
        }
    return out


def _pareto(frame: pd.DataFrame) -> dict[str, Any]:
    """§3의 절충 축에서 지배되지 않는 조합들."""

    axes = [(name, ascending) for name, ascending in TRADEOFF_AXES if name in frame.columns]
    values = frame[[name for name, _ in axes]].to_numpy(dtype=float)
    signs = np.array([1.0 if ascending else -1.0 for _, ascending in axes])
    scored = values * signs
    keep: list[int] = []
    for index in range(len(scored)):
        dominated = (scored <= scored[index]).all(axis=1) & (scored < scored[index]).any(axis=1)
        if not dominated.any():
            keep.append(index)
    return {
        "axes": [name for name, _ in axes],
        "non_dominated_combinations": len(keep),
        "selected_is_non_dominated": bool(0 in keep),
    }


def summarise(frame: pd.DataFrame) -> dict[str, Any]:
    """선택 규칙이 고른 조합과 프런티어의 모양. §2가 요구한 기록을 모두 남긴다."""

    if frame.empty:
        return {
            "feasible_combinations": 0,
            "contradiction": True,
            "reason": (
                "§2의 폭 요건 아래에서 §7의 필수 기준을 동시에 만족하는 조합이 "
                "개발구간에 존재하지 않는다."
            ),
        }
    selected: dict[str, Any] = {str(key): value for key, value in frame.iloc[0].to_dict().items()}
    return {
        "source_commit": source_commit(),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "configuration_base": STOPPED_CONFIG_NAME,
        "development_window": [str(DEVELOPMENT[0].date()), str(DEVELOPMENT[1].date())],
        "warmup_window": frame.attrs.get("warmup"),
        "threshold_grid": {key: list(values) for key, values in THRESHOLD_GRID.items()},
        "filter_grid": [list(item) for item in FILTER_GRID],
        "stage_one_filter": list(STAGE_ONE_FILTER),
        "stage_two_width": STAGE_TWO_WIDTH,
        "stage_one_points": frame.attrs.get("stage_one_points"),
        "combinations_evaluated": frame.attrs.get("evaluated"),
        "rejected_by_gate": frame.attrs.get("rejected_by_gate"),
        "sole_rejection_reason": frame.attrs.get("sole_rejection_reason"),
        "feasible_combinations": int(len(frame)),
        "contradiction": False,
        "gates": GATES,
        "minimum_coincident_domains": MINIMUM_COINCIDENT_DOMAINS,
        "selection_rule_sha256": selection_rule_digest(),
        "selection_order": [
            {"key": name, "direction": "min" if ascending else "max"}
            for name, ascending in SELECTION_ORDER
        ],
        "selected": {key: value for key, value in selected.items() if not key.startswith("_")},
        "selected_rank_by_metric": _ranks(frame, selected),
        "pareto": _pareto(frame),
        "runner_up": {str(key): value for key, value in frame.iloc[1].to_dict().items()}
        if len(frame) > 1
        else None,
        "range": {
            key: [float(frame[key].min()), float(frame[key].max())]
            for key in (
                "overall_recall",
                "core_recall",
                "false_positive_rate",
                "f1",
                "gfc_first_official_contraction_weeks",
            )
        },
    }


def main() -> int:
    settings = load_settings()
    frame = search(settings)
    output = settings.root / "outputs" / "four_phase_v1_1"
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "development_frontier.csv", index=False)
    summary = summarise(frame)
    summary["frontier_csv_sha256"] = hashlib.sha256(
        (output / "development_frontier.csv").read_bytes()
    ).hexdigest()
    (output / "development_frontier.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
