"""듀레이션 축 실행기.

    python -m business_cycle.duration_axis

천장이 먼저다. 그 결과를 보고서 맨 앞에 놓고, 관문을 통과했는지에 따라 아래 단계에
결론을 달지 말지 정한다.

동결 v1.1은 건드리지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import load_settings
from ..market_risk.__main__ import _weekly_spread
from ..rotation_rerun import labels17 as L17
from . import axes as AX
from . import ceiling as CE
from . import control as C
from . import leaveout as LO
from . import prespec
from .report import build_report

OUTPUT_NAME = "duration_axis"
FRENCH_CACHE = "data/cache/famafrench"

#: 이동 간격. 월간 관측이 384개뿐이라 촘촘히 해도 정보가 늘지 않는다.
SHIFT_STRIDE = 1


def _monthly_spread(root: Path, weeks: list[str], months: list[str]) -> pd.Series:
    """주간 스프레드를 그 달 마지막 주 값으로 내린다."""

    weekly = _weekly_spread(root, weeks)
    frame = pd.DataFrame({"spread": weekly.astype(float)})
    frame["month"] = [str(week)[:7] for week in weekly.index]
    last = frame.groupby("month")["spread"].last()
    return last.reindex(months)


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root
    cache = str(root / FRENCH_CACHE)
    horizon = prespec.DECISION_HORIZON_MONTHS

    new, _ = L17.build_revised(settings)
    phase_monthly = AX.monthly_phase(new.phase)
    market = AX.market_monthly(cache)

    # ── 1단계. 다른 무엇보다 먼저. ─────────────────────────────────────────
    industry_raw, industry_columns = AX.industry_axis(cache, new.weeks)
    industry_axis, industry_market, industry_phase = AX.align(industry_raw, market, phase_monthly)
    industry = CE.measure(industry_phase, industry_axis, industry_market)

    proxies: dict[str, Any] = {}
    for proxy in prespec.PROXIES:
        entry: dict[str, Any] = {}
        for buckets, key in (("deciles", "primary"), ("quintiles", "secondary")):
            raw, columns = AX.duration_axis(proxy, cache, buckets)
            axis, axis_market, axis_phase = AX.align(raw, market, phase_monthly)
            entry[key] = {
                "axis": AX.describe(axis, columns),
                **CE.measure(axis_phase, axis, axis_market),
            }
        proxies[proxy] = entry

    primary = proxies[prespec.PRIMARY_PROXY]["primary"]
    gate = prespec.ceiling_gate(
        primary["ranking_ceiling"]["annualised_relative_return"],
        primary["ranking_ceiling"]["information_ratio"],
        industry["ranking_ceiling"]["annualised_relative_return"],
    )
    axis_comparison = CE.compare(primary, industry)

    # ── 2·3단계. 관문이 막혀도 숫자는 남긴다. 결론은 달지 않는다. ─────────
    raw, _ = AX.duration_axis(prespec.PRIMARY_PROXY, cache, "deciles")
    axis, axis_market, axis_phase = AX.align(raw, market, phase_monthly)
    months = list(axis.index)
    spread = _monthly_spread(root, new.weeks, months)
    target = C.long_minus_short(axis, horizon)

    table = C.lookback_table(axis_phase, axis_market, spread, target, SHIFT_STRIDE)
    choice = C.choose_lookback(table)
    lookback = int(choice["chosen"])
    control = C.build_control(axis_market, spread, lookback)
    decision = C.compare(axis_phase, control, target, "long minus short, forward 3m", SHIFT_STRIDE)

    exclusions: dict[str, Any] = {}
    for name, start, end in prespec.EPISODE_EXCLUSIONS:
        kept = [month for month in months if not start <= month <= end]
        exclusions[name] = C.compare(
            axis_phase.loc[kept],
            control.loc[kept],
            target.loc[kept],
            name,
            SHIFT_STRIDE,
        )

    leave = LO.run(axis_phase, control, target, horizon, stride=4)

    multiplicity = prespec.multiplicity(decision.get("null_p"))

    payload: dict[str, Any] = {
        "stage": OUTPUT_NAME,
        "frozen_model_modified": False,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate_under_test": L17.GATE.name,
        "prespecified_rule": prespec.rule(),
        "months": len(months),
        "first_month": months[0],
        "last_month": months[-1],
        "industry_axis": {
            "axis": AX.describe(industry_axis, industry_columns),
            **industry,
        },
        "duration_axes": proxies,
        "ceiling_gate": gate,
        "axis_comparison": axis_comparison,
        "control_target": "long minus short duration bucket, forward 3 months",
        "control_target_was_prespecified": False,
        "lookback_table": table,
        "lookback_choice": choice,
        "chosen_lookback": lookback,
        "control_comparison": decision,
        "exclusions": exclusions,
        "leave_one_episode_out": leave,
        "multiplicity": multiplicity,
        "record_only_below_the_ceiling": not gate["passes"],
    }
    payload["verdict"] = _verdict(payload)
    return payload


def _verdict(payload: dict[str, Any]) -> dict[str, Any]:
    gate = payload["ceiling_gate"]
    comparison = payload["axis_comparison"]
    return {
        "axis_was_wrong": gate["passes"],
        "failed_conditions": gate["failed"],
        "duration_ceiling": gate["duration_ceiling_annual"],
        "industry_ceiling": gate["industry_ceiling_annual"],
        "ceiling_ratio": gate["ratio_to_industry"],
        "share_organised_duration": comparison["share_organised_duration"],
        "share_organised_industry": comparison["share_organised_industry"],
        "statement": (
            gate["verdict"]
            + " "
            + (
                ""
                if gate["passes"]
                else "**순환매 질문은 어떤 축으로 잘라도 닫히고, 이 계열은 여기서 끝난다.**"
            )
        ).strip(),
    }


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "duration_axis_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_plain),
        encoding="utf-8",
        newline="\n",
    )
    rows = [
        {
            "axis": "industry (FF12, monthly)",
            "buckets": payload["industry_axis"]["buckets"],
            **_flat(payload["industry_axis"]),
        }
    ]
    for proxy, entry in payload["duration_axes"].items():
        for key in ("primary", "secondary"):
            rows.append(
                {
                    "axis": f"{proxy} ({key})",
                    "buckets": entry[key]["buckets"],
                    **_flat(entry[key]),
                }
            )
    pd.DataFrame(rows).to_csv(output / "ceilings.csv", index=False)
    pd.DataFrame(payload["lookback_table"]).to_csv(output / "lookback_table.csv", index=False)
    pd.DataFrame([payload["control_comparison"], *payload["exclusions"].values()]).to_csv(
        output / "control_comparison.csv", index=False
    )
    pd.DataFrame(payload["leave_one_episode_out"]["rows"]).to_csv(
        output / "leave_one_episode_out.csv", index=False
    )


def _flat(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "periods": entry["periods"],
        "ranking_ceiling_annual": entry["ranking_ceiling"]["annualised_relative_return"],
        "ranking_ceiling_ir": entry["ranking_ceiling"]["information_ratio"],
        "oracle_annual": entry["oracle_ceiling"]["annualised_relative_return"],
        "share_organised_by_phase": entry["phase_share_of_the_oracle"],
        "achievable_annual": entry["achievable_rotation"]["annualised_relative_return"],
        "equal_weight_annual": entry["equal_weight"]["annualised_relative_return"],
    }


def _plain(value: Any) -> Any:
    if isinstance(value, np.integer | np.floating):
        return value.item()
    raise TypeError(f"{type(value)!r}는 JSON으로 옮길 수 없다")


def main() -> int:
    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload, build_report(payload))
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
