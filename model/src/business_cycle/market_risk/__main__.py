"""시계열 질문 실행기.

    python -m business_cycle.market_risk

시장 요인 하나와 기간 스프레드 하나면 된다. 업종 포트폴리오도 격자도 없다.

동결 v1.1은 건드리지 않는다. persist17w 라벨은 트랙 23이 만들어 둔 것을 그대로 읽는다 —
같은 라벨 위에서 두 질문을 물어야 답이 견줘진다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import load_settings
from ..phase_returns import labels as L
from ..phase_returns.french import load_daily, to_weekly
from ..phase_returns.labels import PHASES
from ..phase_value.data import load_term_spread
from ..rotation_rerun import labels17 as L17
from . import leaveout as LO
from . import market as M
from . import prespec
from . import spread as SP
from .report import build_report

OUTPUT_NAME = "market_risk"
FRENCH_CACHE = "data/cache/famafrench"
RATE_CACHE = "data/cache/rates"

COVID = ("2020-01-01", "2020-12-31")
GFC = ("2008-01-01", "2009-12-31")


def _weekly_market(root: Path, weeks: list[str]) -> pd.Series:
    """주간 시장 초과수익. Mkt-RF는 이미 무위험 초과다.

    트랙 17의 `to_weekly`를 그대로 쓴다 — 주 F의 수익률은 (F-7, F] 구간이고, 여기서
    정렬을 새로 짜면 같은 라벨 위에서 잰 두 결과가 견줘지지 않는다.
    """

    _, factors = load_daily(str(root / FRENCH_CACHE))
    weekly = to_weekly(factors[["Mkt-RF"]].astype(float), weeks)
    series = weekly["Mkt-RF"]
    series.name = "market_excess"
    return series


def _weekly_spread(root: Path, weeks: list[str]) -> pd.Series:
    """주간 기간 스프레드. 그 주까지의 마지막 관측을 쓴다 — 그때 알 수 있었던 값이다."""

    daily = load_term_spread(str(root / RATE_CACHE)).astype(float)
    aligned = daily.reindex(daily.index.union(pd.to_datetime(weeks))).ffill()
    values = aligned.reindex(pd.to_datetime(weeks)).to_numpy(dtype=float)
    return pd.Series(values, index=pd.Index(weeks, name="week"), name="term_spread")


def _without(weeks: list[str], span: tuple[str, str]) -> list[str]:
    return [week for week in weeks if not span[0] <= week <= span[1]]


def build_payload(settings: Any) -> dict[str, Any]:
    root = settings.root

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

    horizon = prespec.DECISION_HORIZON_WEEKS
    forward_by_horizon = {str(h): M.forward(phase, market, h) for h in prespec.HORIZONS}
    ratios = {
        str(h): M.downside_ratio(rows)
        for h, rows in zip(prespec.HORIZONS, forward_by_horizon.values(), strict=False)
    }
    decision_ratio = ratios[str(horizon)]

    riskiest = decision_ratio["riskiest"]
    safest = decision_ratio["safest"]
    overlaps = [M.overlap(phase, market, h, str(riskiest), str(safest)) for h in prespec.HORIZONS]
    decision_overlap = next(row for row in overlaps if row.get("horizon_weeks") == horizon)

    null = LO.shift_null(phase, market, horizon)
    separation = prespec.separation_gate(
        decision_ratio["ratio"],
        decision_overlap["overlap_coefficient"],
        null["p_value"],
    )

    leave = LO.run(phase, market, horizon)
    ex_covid_weeks = _without(weeks, COVID)
    ex_covid_ratio = M.downside_ratio(
        M.forward(phase.loc[ex_covid_weeks], market.loc[ex_covid_weeks], horizon)
    )
    ex_gfc_weeks = _without(weeks, GFC)
    ex_gfc_ratio = M.downside_ratio(
        M.forward(phase.loc[ex_gfc_weeks], market.loc[ex_gfc_weeks], horizon)
    )
    robustness = prespec.robustness_gate(
        leave["block_only_summary"]["lowest"],
        leave["event_including_summary"]["lowest"],
        ex_covid_ratio["ratio"],
        decision_ratio["ratio"],
    )

    forward_returns = M.forward_sum(market, horizon)
    control = SP.read(
        SP.compare(phase, spread, forward_returns, "forward return"),
        SP.compare(phase, spread, forward_returns.pow(2), "forward squared return (variance)"),
    )
    spread_verdict = prespec.spread_gate(
        control["returns"].get("incremental_r_squared_of_phase"),
        control["returns"].get("null_p"),
    )

    # 트랙 17의 표제가 2020년 하나에 얹혀 있었다. 스프레드 대조에도 같은 검사를 건다 —
    # 분산 설명력이 그 한 해에서 나온 것이면 그것은 국면의 성질이 아니다.
    spread_without: dict[str, Any] = {}
    for name, span in (("ex_covid", COVID), ("ex_gfc", GFC)):
        kept = _without(weeks, span)
        ahead = M.forward_sum(market.loc[kept], horizon)
        spread_without[name] = {
            "returns": SP.compare(
                phase.loc[kept], spread.loc[kept], ahead, f"forward return ({name})"
            ),
            "variance": SP.compare(
                phase.loc[kept], spread.loc[kept], ahead.pow(2), f"forward variance ({name})"
            ),
        }

    other: dict[str, Any] = {
        "v1_1_revised": _profile(v11.phase.reindex(v11.weeks), market.reindex(v11.weeks), horizon),
    }
    if real_time is not None:
        overlap_weeks = [week for week in real_time.weeks if week in set(weeks)]
        other["real_time_overlap"] = _profile(
            real_time.phase.reindex(overlap_weeks), market.reindex(overlap_weeks), horizon
        )
        other["revised_overlap"] = _profile(
            phase.reindex(overlap_weeks), market.reindex(overlap_weeks), horizon
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
        "contemporaneous": M.contemporaneous(phase, market),
        "forward": forward_by_horizon,
        "downside_ratio_by_horizon": ratios,
        "overlap_by_horizon": overlaps,
        "random_label_null": null,
        "separation_gate": separation,
        "leave_one_episode_out": leave,
        "ex_covid_ratio": ex_covid_ratio,
        "ex_gfc_ratio": ex_gfc_ratio,
        "robustness_gate": robustness,
        "term_spread": control,
        "term_spread_gate": spread_verdict,
        "term_spread_without_episode": spread_without,
        "other_labellings": other,
        "real_time_available": real_time is not None,
    }
    payload["verdict"] = _verdict(payload)
    return payload


def _profile(phase: pd.Series, market: pd.Series, horizon: int) -> dict[str, Any]:
    """보고만 하는 라벨링의 요약. 판정에는 쓰지 않는다."""

    rows = M.forward(phase, market, horizon)
    return {
        "weeks": int(len(phase)),
        "episodes": {name: sum(1 for _ in M._blocks(phase, name)) for name in PHASES},
        "forward": rows,
        "downside_ratio": M.downside_ratio(rows),
    }


def _verdict(payload: dict[str, Any]) -> dict[str, Any]:
    separation = payload["separation_gate"]["passes"]
    robust = payload["robustness_gate"]["passes"]
    spread_ok = payload["term_spread_gate"]["passes"]
    usable = bool(separation and robust and spread_ok)
    control = payload["term_spread"]

    if usable:
        statement = (
            "국면은 시장 수준의 위험 정보를 담고 있고, 기간 스프레드가 이미 주는 것 "
            "이상을 준다. 노출 조절에 쓸 근거가 된다."
        )
    elif not spread_ok:
        statement = (
            "**국면은 기간 스프레드를 넘어서지 못한다.** 사전 명세가 이 조건을 결정적으로 "
            "두었으므로 여기서 결론이 난다 — 이 용도에서도 매일 공짜로 받는 계열 하나가 "
            "모델을 대신한다. 이로써 **이 모델의 쓸모는 서술과 상태 인식으로 확정된다.** "
            "물러선 것이 아니라 정해진 것이다."
        )
    else:
        failed = payload["separation_gate"]["failed"] + payload["robustness_gate"]["failed"]
        statement = (
            "국면이 기간 스프레드 위에 무언가를 얹지만 분리 자체가 사전 조건을 채우지 "
            f"못한다({', '.join(failed)}). 노출 조절의 근거로 삼기에는 이르다."
        )

    return {
        "usable_for_exposure": usable,
        "separation_passes": separation,
        "robustness_passes": robust,
        "beats_the_term_spread": spread_ok,
        "phase_adds_on_variance": control["phase_adds_beyond_the_spread_on_variance"],
        "statement": statement,
    }


def write(output: Path, payload: dict[str, Any], report: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "market_risk_report.md").write_text(report, encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    pd.DataFrame(payload["contemporaneous"]).to_csv(output / "contemporaneous.csv", index=False)
    pd.DataFrame([row for rows in payload["forward"].values() for row in rows]).to_csv(
        output / "forward.csv", index=False
    )
    pd.DataFrame(payload["leave_one_episode_out"]["rows"]).to_csv(
        output / "leave_one_episode_out.csv", index=False
    )
    pd.DataFrame([payload["term_spread"]["returns"], payload["term_spread"]["variance"]]).to_csv(
        output / "term_spread_control.csv", index=False
    )
    pd.DataFrame(payload["overlap_by_horizon"]).to_csv(output / "overlap.csv", index=False)


def main() -> int:
    settings = load_settings()
    payload = build_payload(settings)
    write(settings.root / "outputs" / OUTPUT_NAME, payload, build_report(payload))
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
