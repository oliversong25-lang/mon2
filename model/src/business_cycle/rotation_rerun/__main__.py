"""트랙 17 재실행 실행기.

    python -m business_cycle.rotation_rerun

순서가 규칙이다. 천장을 먼저 재고, 그 결과를 보고서 맨 앞에 놓고, 관문을 통과했는지에
따라 순환매 결론을 달지 말지 정한다. 판별력은 다른 질문이라 관문과 무관하게 낸다.

동결 v1.1은 건드리지 않는다. persist17w 라벨은 `outputs/rotation_rerun/labels/`에
따로 쓴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import load_settings
from ..phase_returns import forward as F
from ..phase_returns import labels as L
from ..phase_returns import rotation as ROT
from ..phase_returns import samples as SA
from ..phase_returns.french import load_daily, to_weekly
from . import ceiling as CE
from . import labels17 as L17
from . import leaveout as LO
from . import prespec
from . import rerun as RR
from .report import build_report

OUTPUT_NAME = "rotation_rerun"
FRENCH_CACHE = "data/cache/famafrench"

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}


def _weekly(root: Path, weeks: list[str]) -> pd.DataFrame:
    industries, factors = load_daily(str(root / FRENCH_CACHE))
    market = (factors["Mkt-RF"] + factors["RF"]).to_frame(F.MARKET)
    return to_weekly(industries.join(market, how="inner"), weeks)


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root

    v11 = L.load_revised(str(root / L.REVISED_PATH))
    new, frame = L17.build_revised(settings)
    L17.write(settings, L17.REVISED_FILE, frame)
    weekly = _weekly(root, v11.weeks)

    # ── 1단계. 다른 무엇보다 먼저. ──────────────────────────────────────────
    ceiling = CE.compare(
        CE.measure(v11.phase.reindex(v11.weeks), weekly),
        CE.measure(new.phase.reindex(v11.weeks), weekly),
    )

    real_time_new: L.Labelling | None
    try:
        real_time_new = L17.load_real_time(settings)
    except (FileNotFoundError, ValueError):
        real_time_new = None
    real_time_old = L.load_real_time(str(root / L.REAL_TIME_PATH))

    overlap = L.overlap(new, real_time_new or real_time_old)
    before_samples = SA.build(v11, real_time_old, overlap, None)
    after_samples = SA.build(new, real_time_new or real_time_old, overlap, None)

    before = {sample.name: RR.analyse(sample, weekly) for sample in before_samples}
    after = {sample.name: RR.analyse(sample, weekly) for sample in after_samples}

    def _minimum(sample: SA.Sample) -> int:
        return (
            RR.SHORT_WINDOW_MINIMUM_HISTORY
            if len(sample.weeks) < 1000
            else ROT.MINIMUM_PHASE_HISTORY
        )

    rotations_before = [
        RR.rotate(sample, weekly, _minimum(sample))
        for sample in before_samples
        if sample.contiguous
    ]
    rotations_after = [
        RR.rotate(sample, weekly, _minimum(sample)) for sample in after_samples if sample.contiguous
    ]

    decision = next(sample for sample in after_samples if sample.name == prespec.DECISION_SAMPLE)
    decision_rotation = next(
        row for row in rotations_after if row["sample"] == prespec.DECISION_SAMPLE
    )
    ex_covid = RR.rotate(
        SA.Sample(
            name="revised_long_ex_covid_contiguous",
            question="taxonomy",
            phase=new.phase,
            weeks=[week for week in new.weeks if not week.startswith("2020")],
            note="2020년을 뺀 순환매. 복리 경로가 한 번 끊기는 것을 감수한다.",
        ),
        weekly,
        ROT.MINIMUM_PHASE_HISTORY,
    )

    leave = LO.run(decision.phase.reindex(decision.weeks), weekly, ROT.MINIMUM_PHASE_HISTORY)
    rotation_gate = prespec.rotation_gate(
        decision_rotation["excess_over_equal_weight"],
        decision_rotation["null"]["p_value"],
        ex_covid["excess_over_equal_weight"],
        leave["event_including_summary"].get("stays_positive_everywhere"),
    )

    payload: dict[str, Any] = {
        "stage": OUTPUT_NAME,
        "frozen_model_modified": False,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate_under_test": L17.GATE.name,
        "prespecified_rule": prespec.rule(),
        "label_counts": {
            "v1_1": {"weeks": v11.counts(), "episodes": L.episodes(v11.phase)},
            "persist17w": {"weeks": new.counts(), "episodes": L.episodes(new.phase)},
        },
        "ceiling": ceiling,
        "ceiling_gate_passes": ceiling["gate"]["passes"],
        "rotation": {
            "v1_1": rotations_before,
            "persist17w": rotations_after,
            "ex_covid_contiguous": ex_covid,
            "gate": rotation_gate,
        },
        "leave_one_episode_out": leave,
        "taxonomy": RR.taxonomy_rows(before, after),
        "per_phase": RR.per_phase_rows(before, after),
        "covid_dependence": RR.covid_dependence(
            after["revised_long"],
            after["revised_long_ex_covid"],
            after["revised_long_ex_gfc"],
        ),
        "real_time_path_regenerated": real_time_new is not None,
    }
    payload["analysis"] = {"v1_1": before, "persist17w": after}
    payload["verdict"] = _verdict(payload)
    return payload


def _verdict(payload: dict[str, Any]) -> dict[str, Any]:
    ceiling = payload["ceiling"]
    passes = bool(payload["ceiling_gate_passes"]) and bool(payload["rotation"]["gate"]["passes"])
    annual = ceiling["persist17w"]["ranking_ceiling"]["annualised_relative_return"]
    share = ceiling["persist17w"]["phase_share_of_the_oracle"]
    return {
        "usable_for_rotation": passes,
        "stopped_at": (
            "ceiling"
            if not payload["ceiling_gate_passes"]
            else ("rotation" if not payload["rotation"]["gate"]["passes"] else None)
        ),
        "statement": (
            "국면 모델은 순환매를 받쳐 준다. 사전 명세의 네 조건과 천장 관문을 모두 통과했다."
            if passes
            else f"**경계를 바르게 잡아도 국면 모델은 순환매를 받쳐 주지 않는다.** "
            f"완전예지 천장이 연 {annual:.2%}로 사전에 정한 8% 관문 아래이고, "
            f"국면이 조직하는 몫은 주간 신탁 대비 {share:.1%}에 그친다. 이것은 미완의 "
            "결과가 아니라 깨끗한 부정이다 — 국면 정확도를 아무리 올려도 이 위로 갈 수 "
            "없다는 뜻이기 때문이다."
        ),
    }


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "rotation_rerun_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    pd.DataFrame(payload["taxonomy"]).to_csv(output / "taxonomy_dispersion.csv", index=False)
    pd.DataFrame(payload["per_phase"]).to_csv(output / "per_phase_discrimination.csv", index=False)
    pd.DataFrame(payload["leave_one_episode_out"]["rows"]).to_csv(
        output / "leave_one_episode_out.csv", index=False
    )
    pd.DataFrame(payload["covid_dependence"]["rows"]).to_csv(
        output / "covid_dependence.csv", index=False
    )
    pd.DataFrame(payload["rotation"]["v1_1"] + payload["rotation"]["persist17w"]).to_csv(
        output / "rotation.csv", index=False
    )


def main() -> int:
    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload, build_report(payload))
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
