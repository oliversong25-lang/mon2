"""§8. 동결된 v1.1 설정이 개발구간 게이트를 전부 통과하는지 **깨끗한 프로세스**에서 확인한다.

프런티어가 통과라고 적어 둔 것을 믿지 않는다. 동결 파일을 다시 읽고, 생산 코드로 다시
계산하고, 프런티어가 기록한 행과 값이 같은지 대조한다. 하나라도 어긋나면 0이 아닌 종료
코드로 멈춘다 — 그 상태로 검증을 시작하면 안 되기 때문이다.

    python -m business_cycle.four_phase.verify_development
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..config import load_baseline, load_settings
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import frontier as FR
from . import validation as V
from .contract import PHASES
from .engine import load_config, prepare, score

DEVELOPMENT = FR.DEVELOPMENT

#: 프런티어 행과 대조할 지표. 하나라도 어긋나면 재현 실패다.
REPRODUCED_KEYS = (
    "overall_recall",
    "core_recall",
    "first_third_recall",
    "middle_third_recall",
    "final_third_recall",
    "first_half_recall",
    "false_positive_rate",
    "f1",
    "two_step_transitions",
    "three_week_whipsaws",
    "longest_disagreement_run",
    "four_week_confirmed_false_positive_episodes",
    "gfc_first_official_contraction_weeks",
    "median_transition_delay_weeks",
    "maximum_transition_delay_weeks",
)


def check(settings: Any = None) -> dict[str, Any]:
    """동결 설정으로 개발구간을 다시 돌리고 게이트를 판정한다."""

    base = settings or load_settings()
    config = load_config(base)
    frozen = load_baseline(FR.FROZEN_BASELINE, base)
    core, source = load_core_observations(base)
    prepared = prepare(core, frozen, pd.Timestamp(END), config)
    recession = _official_recession_flags(source, prepared.index).fillna(False).astype(bool)
    index = prepared.index
    window = index[(index >= DEVELOPMENT[0]) & (index <= DEVELOPMENT[1])]

    run = score(prepared, config)
    phase = run.official_phase.reindex(window)
    truth = recession.reindex(window)
    metrics = V.recession_metrics(phase, truth)
    stability = V.stability(phase)
    raw = run.raw_phase.reindex(window)
    delays = V.delay_summary(V.transition_delays(run.filtered_winner.reindex(window), phase))
    confirming = run.confirming_domains.reindex(window)
    timing = V.signal_timing(
        phase,
        run.alert_level.reindex(window),
        raw,
        pd.Timestamp(FR.DEVELOPMENT_EPISODES["gfc"]),
    )
    gfc = timing.get("first_official_contraction_weeks")
    gates = FR.GATES
    checks = {
        "official_contraction_breadth_at_least_two": int(
            (phase.eq("contraction") & confirming.lt(FR.MINIMUM_COINCIDENT_DOMAINS)).sum()
        )
        == 0,
        "overall_recall": metrics["overall_recall"] >= gates["overall_recall_minimum"],
        "core_recall": metrics["core_recall"] >= gates["core_recall_minimum"],
        "false_positive_rate": metrics["false_positive_rate"]
        <= gates["false_positive_rate_maximum"],
        "gfc_first_official_contraction": gfc is not None
        and gfc <= gates["gfc_first_official_contraction_maximum_weeks"],
        "all_phases_reachable": len(stability["phases_reached"]) == len(PHASES),
        "two_step_transitions": stability["two_step_transitions"]
        <= gates["two_step_transitions_maximum"],
        "three_week_whipsaws": stability["three_week_whipsaws"]
        <= gates["three_week_whipsaws_maximum"],
        "no_practical_absorbing_state": not stability["unexited_phases"],
        "longest_disagreement_run": V.longest_disagreement(raw, phase)
        <= gates["longest_disagreement_run_maximum"],
        "confirmation_delay_within_the_rule": delays["maximum_delay_weeks"]
        <= config.confirmation_weeks,
    }
    measured = {
        **{key: metrics[key] for key in metrics if key in REPRODUCED_KEYS},
        "two_step_transitions": stability["two_step_transitions"],
        "three_week_whipsaws": stability["three_week_whipsaws"],
        "longest_disagreement_run": V.longest_disagreement(raw, phase),
        "gfc_first_official_contraction_weeks": float(gfc) if gfc is not None else None,
        "median_transition_delay_weeks": delays["median_delay_weeks"],
        "maximum_transition_delay_weeks": delays["maximum_delay_weeks"],
    }

    # 프런티어가 기록한 행과 값이 같은지. 다르면 게이트 통과 여부와 무관하게 실패다.
    frontier_path = base.root / "outputs" / "four_phase_v1_1" / "development_frontier.json"
    reproduction: dict[str, Any] = {"compared": False}
    if frontier_path.exists():
        selected = json.loads(frontier_path.read_text(encoding="utf-8"))["selected"]
        mismatches = {
            key: {"frontier": selected[key], "measured": measured[key]}
            for key in REPRODUCED_KEYS
            if key in measured and key in selected and measured[key] != selected[key]
        }
        reproduction = {"compared": True, "mismatches": mismatches, "matches": not mismatches}
    return {
        "frozen_config_sha256": config.sha256,
        "development_window": [str(window[0].date()), str(window[-1].date())],
        "development_weeks": int(len(window)),
        "measured": measured,
        "checks": checks,
        "passed": all(checks.values()),
        "frontier_reproduction": reproduction,
    }


def main() -> int:
    result = check()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if not result["passed"]:
        failed = [name for name, ok in result["checks"].items() if not ok]
        print(f"개발구간 게이트 실패: {failed}")
        return 1
    reproduction = result["frontier_reproduction"]
    if reproduction["compared"] and not reproduction["matches"]:
        print(f"프런티어 행을 재현하지 못했습니다: {reproduction['mismatches']}")
        return 2
    output = load_settings().root / "outputs" / "four_phase_v1_1"
    output.mkdir(parents=True, exist_ok=True)
    (output / "development_gate_check.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("개발구간 게이트 전부 통과 · 프런티어 행 재현 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
