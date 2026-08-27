"""세 검증의 계산. 판정 문장은 `verdicts`가 만들고 여기서는 재기만 한다."""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..four_phase.engine import FourPhaseConfig, PreparedInputs
from ..phase_returns.french import INDUSTRIES, to_weekly
from ..phase_returns.french import load_daily as load_french
from ..phase_value.conditional import forward_value
from ..slowdown_boundary import metrics as M
from ..slowdown_boundary import natural as N
from ..slowdown_boundary import scoring as SC
from ..slowdown_boundary import variants as V

#: 트랙 17이 쓴 세 지평선. 확장기 판별력을 하나의 지평선에서만 보면 그 지평선의
#: 성질인지 국면의 성질인지 갈리지 않는다.
HORIZONS: Final[tuple[int, ...]] = (4, 13, 26)

#: B의 지속 길이 격자. 과제가 지정한 값에 5와 21을 양끝으로 둔다.
PERSISTENCE_LENGTHS: Final[tuple[int, ...]] = (5, 7, 9, 11, 13, 15, 17, 21)

#: B의 중립대 배수 격자. 1.0이 모델이 원래 쓰는 값이다.
BANDS: Final[tuple[float, ...]] = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

#: 후퇴기가 "상태"라고 불릴 최소 블록 수. 이보다 적으면 판별력이 높아도 그것은 몇몇
#: 구간의 성질이지 상태의 성질이 아니다.
MINIMUM_BLOCKS: Final[int] = 8


def panels(weeks: list[str], cache_dir: str) -> dict[int, pd.DataFrame]:
    """지평선별 전방 상대수익률. 한 번 만들어 모든 변형이 나눠 쓴다."""

    industries, factors = load_french(cache_dir)
    market = (factors["Mkt-RF"] + factors["RF"]).to_frame("MKT")
    weekly = to_weekly(industries.join(market, how="inner"), weeks)
    out: dict[int, pd.DataFrame] = {}
    for horizon in HORIZONS:
        forward_market = forward_value(weekly["MKT"], horizon)
        out[horizon] = pd.DataFrame(
            {name: forward_value(weekly[name], horizon) - forward_market for name in INDUSTRIES},
            index=pd.Index(weeks, name="week"),
        )
    return out


def all_phase_discrimination(
    phase: pd.Series, panel_by_horizon: dict[int, pd.DataFrame]
) -> dict[str, dict[str, Any]]:
    """네 국면 x 세 지평선. 한 국면만 좋아지고 다른 국면이 나빠지면 그것은 교환이다."""

    return {
        str(horizon): M.discrimination(phase, panel) for horizon, panel in panel_by_horizon.items()
    }


def profile(
    frame: pd.DataFrame,
    panel_by_horizon: dict[int, pd.DataFrame],
    baseline_phase: pd.Series,
    label: str,
) -> dict[str, Any]:
    """한 변형의 전체 기록."""

    phase = frame["official_phase"]
    blocks = {row["phase"]: row for row in N.by_phase(phase)}
    return {
        "gate": label,
        "weeks": int(len(phase)),
        "discrimination": all_phase_discrimination(phase, panel_by_horizon),
        "shape": M.shape(phase),
        "progression": M.progression(phase),
        "recognition": M.recognition(phase, baseline_phase),
        "nber": M.nber(phase),
        "breadth_gate_holds": M.breadth_gate_holds(frame),
        "blocks": {name: int(blocks[name]["episodes"]) for name in blocks},
        "current_call": str(phase.iloc[-1]),
    }


def sensitivity(
    prepared: PreparedInputs,
    config: FourPhaseConfig,
    panel_by_horizon: dict[int, pd.DataFrame],
    baseline_phase: pd.Series,
    gates: list[SC.SlowdownGate],
) -> list[dict[str, Any]]:
    """격자 위의 곡선. 평탄역인지 봉우리인지는 `verdicts`가 읽는다."""

    rows: list[dict[str, Any]] = []
    for gate in gates:
        frame = V.path(prepared, config, V.Variant("candidate", gate, False))
        entry = profile(frame, panel_by_horizon, baseline_phase, gate.name)
        slowdown = entry["discrimination"]["13"]["slowdown"]
        rows.append(
            {
                "gate": gate.name,
                "persistence_weeks": gate.persistence_weeks,
                "persistence_band": gate.persistence_band,
                "slowdown_blocks": entry["blocks"]["slowdown"],
                "slowdown_discrimination": slowdown["ratio_to_chance"],
                "slowdown_p": slowdown["p_value"],
                "slowdown_share": entry["shape"]["phase_shares"]["slowdown"],
                "progression_rate": entry["progression"]["progression_rate"],
                "transitions": entry["shape"]["transitions"],
                "phases_shorter_than_four_weeks": entry["shape"]["phases_shorter_than_four_weeks"],
                "contraction_delay_weeks": entry["recognition"]["contraction"]["max_delay_weeks"],
                "recovery_delay_weeks": entry["recognition"]["recovery"]["max_delay_weeks"],
                "nber_false_positive_episodes": entry["nber"]["false_positive_episodes"],
                "expansion_discrimination": entry["discrimination"]["13"]["expansion"][
                    "ratio_to_chance"
                ],
                "enough_blocks_to_call_it_a_state": entry["blocks"]["slowdown"] >= MINIMUM_BLOCKS,
            }
        )
    return rows
