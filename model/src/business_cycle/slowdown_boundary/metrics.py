"""결정적 지표 — 후퇴기가 **드문 라벨**이 아니라 **실제 상태**가 됐는가.

전이 건수만 줄이고 판별력이 1.0 아래로 남아 있으면 문제를 푼 것이 아니라 이름을 바꾼
것이다. 그래서 세 가지를 함께 본다.

``discrimination``  트랙 17의 국면별 분산 비율. 현재 0.37배 — 우연보다 낮다.
``week_share``      전체 주에서 차지하는 비율. 현재 49%(실시간) / 40%(장기).
``progression``     후퇴기 블록이 침체기로 나아가는 비율. 현재 44건 중 4건.

그리고 깨지면 안 되는 것들을 같은 표에 둔다 — 침체·회복 인식 지연, NBER 오탐, 침체
폭 게이트.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.french import INDUSTRIES, to_weekly
from ..phase_returns.french import load_daily as load_french
from ..phase_returns.significance import MINIMUM_SHIFT
from ..phase_value.conditional import forward_value
from .natural import PHASES, SHORT_PHASE_WEEKS, blocks

#: 판별력을 재는 지평선. 트랙 17이 국면별 검정에 쓴 것과 같다.
DISCRIMINATION_HORIZON: Final[int] = 13

#: NBER 침체 구간. 최신 빈티지 경로가 덮는 세 번 모두.
NBER_RECESSIONS: Final[tuple[tuple[str, str], ...]] = (
    ("2001-03-01", "2001-11-30"),
    ("2007-12-01", "2009-06-30"),
    ("2020-02-01", "2020-04-30"),
)


def industry_panel(weeks: list[str], cache_dir: str) -> pd.DataFrame:
    """주간 산업 상대수익률의 전방 누적. 트랙 17과 같은 자료, 같은 정렬."""

    industries, factors = load_french(cache_dir)
    market = (factors["Mkt-RF"] + factors["RF"]).to_frame("MKT")
    weekly = to_weekly(industries.join(market, how="inner"), weeks)
    forward_market = forward_value(weekly["MKT"], DISCRIMINATION_HORIZON)
    return pd.DataFrame(
        {
            name: forward_value(weekly[name], DISCRIMINATION_HORIZON) - forward_market
            for name in INDUSTRIES
        },
        index=pd.Index(weeks, name="week"),
    )


def discrimination(phase: pd.Series, relative: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """국면별 관측 분산 ÷ 우연 분산. 1보다 크면 그 국면이 산업을 가른다."""

    aligned = phase.reindex(relative.index).to_numpy()
    values = relative.to_numpy(dtype=float)
    valid = np.isfinite(values).astype(float)
    filled = np.nan_to_num(values)
    masks = np.vstack([(aligned == name) for name in PHASES]).astype(float)
    weeks = masks.shape[1]

    with np.errstate(invalid="ignore", divide="ignore"):
        overall = np.where(valid.sum(axis=0) > 0, filled.sum(axis=0) / valid.sum(axis=0), np.nan)

    def per_phase(current: np.ndarray) -> np.ndarray:
        total = current @ filled
        count = current @ valid
        with np.errstate(invalid="ignore", divide="ignore"):
            means = np.where(count > 0, total / np.where(count == 0, 1.0, count), np.nan)
        deviation = (means - overall) ** 2
        return np.array(
            [
                float(np.nanmean(row)) if np.isfinite(row).any() else float("nan")
                for row in deviation
            ]
        )

    observed = per_phase(masks)
    offsets = [k for k in range(weeks) if MINIMUM_SHIFT <= k <= weeks - MINIMUM_SHIFT]
    draws = np.vstack([per_phase(np.roll(masks, offset, axis=1)) for offset in offsets])
    extreme = (draws >= observed).sum(axis=0)

    out: dict[str, dict[str, Any]] = {}
    for row, name in enumerate(PHASES):
        column = draws[:, row]
        median = float(np.nanmedian(column)) if np.isfinite(column).any() else float("nan")
        absent = int(masks[row].sum()) == 0 or not np.isfinite(observed[row])
        out[name] = {
            "weeks": int(masks[row].sum()),
            "ratio_to_chance": (
                round(float(observed[row] / median), 3)
                if not absent and np.isfinite(median) and median > 0
                else None
            ),
            "p_value": (
                None if absent else round(float((extreme[row] + 1) / (len(offsets) + 1)), 4)
            ),
        }
    return out


def progression(phase: pd.Series) -> dict[str, Any]:
    """후퇴기 블록이 어디로 갔는가. 침체기로 나아갔는가, 확장기로 되돌아갔는가."""

    spans = [
        span
        for span in blocks(phase)
        if span["phase"] == "slowdown" and span["next"] is not None
    ]
    destinations: dict[str, int] = {}
    for span in spans:
        key = str(span["next"])
        destinations[key] = destinations.get(key, 0) + 1
    progressed = destinations.get("contraction", 0)
    return {
        "closed_slowdown_blocks": len(spans),
        "progressed_to_contraction": progressed,
        "reverted_to_expansion": destinations.get("expansion", 0),
        "progression_rate": round(progressed / len(spans), 3) if spans else None,
        "where_they_went": destinations,
    }


def shape(phase: pd.Series) -> dict[str, Any]:
    """전이 건수, 4주 미만 블록, 국면별 주 수."""

    values = [str(item) for item in phase.tolist()]
    moves = sum(
        1
        for i in range(1, len(values))
        if values[i - 1] in PHASES and values[i] in PHASES and values[i - 1] != values[i]
    )
    spans = [span for span in blocks(phase) if span["next"] is not None]
    short = [span for span in spans if int(span["weeks"]) < SHORT_PHASE_WEEKS]
    counts = {name: int((phase == name).sum()) for name in PHASES}
    total = len(phase)
    return {
        "transitions": moves,
        "phases_shorter_than_four_weeks": len(short),
        "phase_weeks": counts,
        "phase_shares": {
            name: round(value / total, 4) for name, value in counts.items()
        },
        "withheld_weeks": int(sum(1 for value in values if value not in PHASES)),
    }


def _first_week(phase: pd.Series, name: str, after: str) -> str | None:
    for week in phase.index:
        if str(week) >= after and str(phase[week]) == name:
            return str(week)
    return None


def recognition(phase: pd.Series, baseline: pd.Series) -> dict[str, Any]:
    """침체·회복을 기준선보다 늦게 부르지 않았는가. 늦으면 몇 주인가."""

    out: dict[str, Any] = {}
    for name in ("contraction", "recovery"):
        rows = []
        for start, _ in NBER_RECESSIONS:
            reference = _first_week(baseline, name, start)
            variant = _first_week(phase, name, start)
            delay = (
                int((pd.Timestamp(variant) - pd.Timestamp(reference)).days // 7)
                if reference and variant
                else None
            )
            rows.append(
                {
                    "recession_start": start,
                    "baseline_call": reference,
                    "variant_call": variant,
                    "delay_weeks": delay,
                }
            )
        delays = [row["delay_weeks"] for row in rows if row["delay_weeks"] is not None]
        out[name] = {
            "calls": rows,
            "max_delay_weeks": max(delays) if delays else None,
            "never_called_somewhere": any(row["variant_call"] is None for row in rows),
        }
    return out


def nber(phase: pd.Series) -> dict[str, Any]:
    """침체 라벨이 NBER 침체 밖에서 몇 번 켜졌는가. 구간 단위로 센다."""

    inside = pd.Series(False, index=phase.index)
    for start, end in NBER_RECESSIONS:
        inside |= (phase.index >= start) & (phase.index <= end)
    called = phase.eq("contraction")
    outside = called & ~inside
    episodes = 0
    previous = False
    for value in outside.tolist():
        if value and not previous:
            episodes += 1
        previous = bool(value)
    recall = float((called & inside).sum() / inside.sum()) if inside.sum() else float("nan")
    return {
        "false_positive_weeks": int(outside.sum()),
        "false_positive_episodes": episodes,
        "recession_weeks_called_contraction": int((called & inside).sum()),
        "recall": round(recall, 3),
    }


def breadth_gate_holds(frame: pd.DataFrame) -> bool:
    """공식 침체 주가 여전히 동행 도메인 2개 이상을 만족하는가."""

    weeks = frame[frame["official_phase"].eq("contraction")]
    if weeks.empty:
        return True
    return bool((weeks["confirming_domains"].astype(int) >= 2).all())


def negative_level_expansion(frame: pd.DataFrame) -> int:
    """수준이 음인 확장기 주 수. 성숙도 결함이 사는 자리다."""

    return int(
        (frame["official_phase"].eq("expansion") & frame["activity_level"].lt(0)).sum()
    )
