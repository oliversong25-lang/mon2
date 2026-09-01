"""변동성 대조 실행기.

    python -m business_cycle.variance_control

트랙 24의 자료와 라벨을 그대로 쓴다. 바뀌는 것은 대조 하나다 — 기간 스프레드 옆에
**과거 실현변동성**이 들어간다.

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
from ..market_risk.__main__ import COVID, GFC, _weekly_market, _weekly_spread, _without
from ..phase_returns import labels as L
from ..rotation_rerun import labels17 as L17
from . import control as C
from . import leaveout as LO
from . import prespec
from .report import build_report

OUTPUT_NAME = "variance_control"


def _squared_forward(weekly: pd.Series, horizon: int) -> pd.Series:
    """제곱 전방수익. 트랙 24가 쓴 목표와 정확히 같다."""

    from ..market_risk.market import forward_sum

    return forward_sum(weekly, horizon).pow(2)


def _forward_realised_variance(weekly: pd.Series, horizon: int) -> pd.Series:
    """전방 h주 동안의 주간 제곱합. 두 번째 정의이며 판정을 뒤집지 않는다."""

    squared = pd.Series(weekly.to_numpy(dtype=float) ** 2, index=weekly.index)
    rolled = squared.rolling(horizon).sum().shift(-horizon)
    return pd.Series(rolled.to_numpy(), index=weekly.index)


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root
    horizon = prespec.DECISION_HORIZON_WEEKS

    new, _ = L17.build_revised(settings)
    v11 = L.load_revised(str(root / L.REVISED_PATH))
    try:
        real_time = L17.load_real_time(settings)
    except (FileNotFoundError, ValueError):
        real_time = None

    weeks = new.weeks
    market = _weekly_market(root, weeks)
    spread = _weekly_spread(root, weeks)
    phase = new.phase.reindex(weeks)
    target = _squared_forward(market, horizon)

    # ── 대조를 고른다. 규칙이 고르지 내가 고르지 않는다. ────────────────────
    table = C.lookback_table(phase, market, spread, target, prespec.LOOKBACKS)
    choice = prespec.lookback_choice(table)
    lookback = int(choice["chosen"])
    control = C.build_control(market, spread, lookback)

    decision = C.compare(phase, control, target, prespec.DECISION_TARGET)

    # 두 번째 정의. 미리 이름을 적어 두었고 판정을 뒤집을 수 없다.
    secondary = C.compare(
        phase,
        control,
        _forward_realised_variance(market, horizon),
        prespec.SECONDARY_TARGET,
    )

    # 대조에서 기간 스프레드를 뺀 판본. 실현변동성 하나만으로도 충분한지 보인다.
    without_spread = C.compare(
        phase,
        C.build_control(market, spread, lookback, keep_spread=False),
        target,
        "decision target, volatility only (no term spread)",
    )
    # 트랙 24가 쓴 대조 그대로. 무엇이 달라졌는지 나란히 놓기 위한 것이다.
    spread_only_control = C.compare(
        phase,
        pd.DataFrame({"term_spread": spread.astype(float)}),
        target,
        "decision target, term spread only (Track 24)",
    )

    exclusions: dict[str, Any] = {}
    for name, span in (("ex_covid", COVID), ("ex_gfc", GFC)):
        kept = _without(weeks, span)
        exclusions[name] = C.compare(
            phase.loc[kept], control.loc[kept], target.loc[kept], f"{name}"
        )

    leave = LO.run(phase, control, target, horizon)

    other: dict[str, Any] = {
        "v1_1_revised": C.compare(v11.phase.reindex(weeks), control, target, "v1.1 labels"),
    }
    if real_time is not None:
        overlap = [week for week in real_time.weeks if week in set(weeks)]
        other["real_time_overlap"] = C.compare(
            real_time.phase.reindex(overlap),
            control.loc[overlap],
            target.loc[overlap],
            "real-time labels",
        )

    gate = prespec.decision_gate(
        decision.get("incremental_r_squared_of_phase"),
        decision.get("null_p"),
        leave["block_only_summary"].get("lowest"),
        leave["event_including_summary"].get("lowest"),
        exclusions["ex_covid"].get("null_p"),
        exclusions["ex_gfc"].get("null_p"),
    )

    payload: dict[str, Any] = {
        "stage": OUTPUT_NAME,
        "frozen_model_modified": False,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate_under_test": L17.GATE.name,
        "prespecified_rule": prespec.rule(),
        "weeks": len(weeks),
        "first_week": weeks[0],
        "last_week": weeks[-1],
        "episodes": L.episodes(phase),
        "lookback_table": table,
        "lookback_choice": choice,
        "chosen_lookback_weeks": lookback,
        "decision": decision,
        "secondary": secondary,
        "volatility_only_control": without_spread,
        "track24_spread_only_control": spread_only_control,
        "exclusions": exclusions,
        "leave_one_episode_out": leave,
        "other_labellings": other,
        "decision_gate": gate,
        "real_time_available": real_time is not None,
    }
    payload["verdict"] = _verdict(payload)
    payload["display_wording"] = _display_wording(payload, market, phase, horizon)
    return payload


def _verdict(payload: dict[str, Any]) -> dict[str, Any]:
    gate = payload["decision_gate"]
    decision = payload["decision"]
    track24 = payload["track24_spread_only_control"]
    return {
        "phase_adds_over_realised_volatility": gate["passes"],
        "failed_conditions": gate["failed"],
        "track24_increment_over_the_spread_alone": track24.get("incremental_r_squared_of_phase"),
        "increment_over_the_volatility_control": decision.get("incremental_r_squared_of_phase"),
        "statement": (
            gate["verdict"]
            + " "
            + (
                "제품 형태는 사전 명세가 이미 좁게 묶어 두었다."
                if gate["passes"]
                else "**이 모델의 쓸모는 서술과 상태 인식으로 확정되고, 이 계열의 검정은 "
                "여기서 끝난다.**"
            )
        ),
    }


def _display_wording(
    payload: dict[str, Any], market: pd.Series, phase: pd.Series, horizon: int
) -> dict[str, Any]:
    """통과했을 때만 문구를 낸다. 실패하면 왜 내지 않는지를 적는다."""

    if not payload["decision_gate"]["passes"]:
        return {
            "drafted": False,
            "why_not": (
                "사전 명세가 통과했을 때만 문구를 만들게 했다. 실패한 결과 위에 문구를 "
                "얹으면 그 문구가 근거 없는 것이 된다."
            ),
        }

    from ..market_risk.market import forward as forward_rows

    rows = forward_rows(phase, market, horizon)
    label = {
        "recovery": "회복기",
        "expansion": "확장기",
        "slowdown": "후퇴기",
        "contraction": "침체기",
    }
    lines = [
        {
            "phase": row["phase"],
            "wording": (
                f"{label[row['phase']]}: 역사적으로 이 국면에서 전방 {horizon}주 음수 주 "
                f"비율은 {row['share_negative']:.0%}였습니다 "
                f"(에피소드 {row['episodes']}개, {payload['first_week'][:4]}~"
                f"{payload['last_week'][:4]})."
            ),
        }
        for row in rows
    ]
    return {
        "drafted": True,
        "lines": lines,
        "constraint": payload["prespecified_rule"]["product_form_if_it_passes"],
    }


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "variance_control_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_plain),
        encoding="utf-8",
        newline="\n",
    )
    pd.DataFrame(payload["lookback_table"]).to_csv(output / "lookback_table.csv", index=False)
    pd.DataFrame(
        [
            payload["decision"],
            payload["secondary"],
            payload["volatility_only_control"],
            payload["track24_spread_only_control"],
            *payload["exclusions"].values(),
            *payload["other_labellings"].values(),
        ]
    ).to_csv(output / "comparisons.csv", index=False)
    pd.DataFrame(payload["leave_one_episode_out"]["rows"]).to_csv(
        output / "leave_one_episode_out.csv", index=False
    )


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
