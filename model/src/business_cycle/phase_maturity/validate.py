"""후반부 신호가 실제로 무엇을 예고했는가.

세 가지를 나란히 놓지 않으면 아무것도 알 수 없다.

``base``      그 국면의 모든 주. 아무 신호 없이 찍었을 때의 적중률.
``duration``  경과 기간 상위 3분의 1. **경과 기간만으로 만든 신호**의 적중률.
``late``      후반부 신호가 켜진 주.

``late``가 ``base``보다 높은 것만으로는 부족하다. ``duration``보다 높아야 한다. 그러지
않으면 "이 확장기는 오래됐다"를 어렵게 다시 쓴 것에 불과하다 — 확장기는 늙어서 죽지
않는다는 실증에 정면으로 어긋나는 신호가 된다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from .series import elapsed, runs
from .signal import SUCCESSOR

#: 예고 창. 한 분기다. 미리 정해 두고 결과를 보고 바꾸지 않는다.
HORIZON_WEEKS: Final[int] = 13

#: 경과 기간 대조군의 분위. 상위 3분의 1을 "오래된 주"로 본다.
DURATION_QUANTILE: Final[float] = 2.0 / 3.0

#: 귀무표본을 몇 번 뽑는가. 국면 블록 **안에서만** 순환 이동시킨다.
PERMUTATIONS: Final[int] = 2000


def outcomes(phase: pd.Series, horizon: int = HORIZON_WEEKS) -> pd.DataFrame:
    """주마다: 창 안에서 국면을 벗어났는가, 벗어났다면 어디로 갔는가."""

    values = [str(item) for item in phase.tolist()]
    exits: list[bool] = []
    to_successor: list[bool] = []
    destination: list[str] = []
    for position, current in enumerate(values):
        window = values[position + 1 : position + 1 + horizon]
        nxt = next((value for value in window if value != current), "")
        exits.append(bool(nxt))
        destination.append(nxt)
        to_successor.append(nxt == SUCCESSOR.get(current, ""))
    return pd.DataFrame(
        {
            "phase": values,
            "leaves_within_horizon": exits,
            "moves_to_the_expected_successor": to_successor,
            "destination": destination,
        },
        index=phase.index,
    )


def _rate(mask: pd.Series, target: pd.Series) -> float | None:
    selected = target[mask]
    if selected.empty:
        return None
    return round(float(selected.mean()), 4)


def _within_run_shift_p(
    late: pd.Series, target: pd.Series, phase: pd.Series, name: str, seed: int = 7
) -> float | None:
    """국면 블록 **안에서** 후반 표시를 순환 이동시킨다.

    귀무가설은 "표시가 몇 개 켜지는지는 그대로인데, 블록 안 **어디에** 켜지는지는 상관
    없다"이다. 그것이 성숙도 주장의 정확한 반대다 — 성숙도는 위치가 중요하다고 말한다.

    주를 독립적으로 섞으면 표시가 블록 끝에 몰려 있다는 사실 자체가 깨져서, 실제보다
    훨씬 작은 p가 나온다. 블록 안 순환 이동은 그 뭉침을 보존한다.
    """

    blocks = [block for block in runs(phase) if block["phase"] == name]
    weeks = {week: position for position, week in enumerate(phase.index)}
    segments: list[tuple[np.ndarray, np.ndarray]] = []
    for block in blocks:
        lower = weeks[str(block["start"])]
        upper = weeks[str(block["end"])] + 1
        flags = late.to_numpy(dtype=bool)[lower:upper]
        values = target.to_numpy(dtype=float)[lower:upper]
        if flags.size and np.isfinite(values).all():
            segments.append((flags, values))
    if not segments:
        return None

    def pooled(offsets: list[int]) -> float | None:
        total = 0.0
        count = 0
        for (flags, values), offset in zip(segments, offsets, strict=True):
            rolled = np.roll(flags, offset)
            total += float(values[rolled].sum())
            count += int(rolled.sum())
        return total / count if count else None

    observed = pooled([0] * len(segments))
    if observed is None:
        return None

    rng = np.random.default_rng(seed)
    extreme = 0
    drawn = 0
    for _ in range(PERMUTATIONS):
        offsets = [int(rng.integers(0, len(flags))) for flags, _ in segments]
        value = pooled(offsets)
        if value is None:
            continue
        drawn += 1
        if value >= observed:
            extreme += 1
    return round((extreme + 1) / (drawn + 1), 4) if drawn else None


def threshold_sweep(
    frame: pd.DataFrame, scored: pd.DataFrame, horizon: int = HORIZON_WEEKS
) -> list[dict[str, Any]]:
    """점수가 가질 수 있는 값은 0, 1/3, 2/3, 1 넷뿐이다. **전부** 싣는다.

    미리 정한 문턱은 2/3다. 이 표에서 좋은 값을 골라 문턱을 바꾸지 않는다 — 네 값을 모두
    보이는 것은 고르는 것이 아니라 전체를 드러내는 것이다.
    """

    result = outcomes(frame["phase"], horizon)
    successor_hits = result["moves_to_the_expected_successor"].astype(float)
    rows: list[dict[str, Any]] = []
    for name in SUCCESSOR:
        within = frame["phase"].eq(name)
        if not bool(within.any()):
            continue
        for level in (0.0, 1 / 3, 2 / 3, 1.0):
            mask = within & scored["maturity"].ge(level - 1e-9)
            rows.append(
                {
                    "phase": name,
                    "threshold": round(level, 3),
                    "weeks": int(mask.sum()),
                    "share_of_phase": round(float(mask.sum() / within.sum()), 4),
                    "successor_rate": _rate(mask, successor_hits),
                    "predeclared": abs(level - 2 / 3) < 1e-9,
                }
            )
    return rows


def by_phase(
    frame: pd.DataFrame, scored: pd.DataFrame, horizon: int = HORIZON_WEEKS
) -> list[dict[str, Any]]:
    """국면별 결과. 하나의 점수로 합치지 않는다."""

    result = outcomes(frame["phase"], horizon)
    age = elapsed(frame["phase"])
    blocks = runs(frame["phase"])

    rows: list[dict[str, Any]] = []
    for name, successor in SUCCESSOR.items():
        within = frame["phase"].eq(name)
        if not bool(within.any()):
            continue
        late = scored["late"] & within
        # 경과 기간만으로 만든 대조 신호. 같은 국면 안에서 상위 3분의 1.
        cutoff = float(age[within].quantile(DURATION_QUANTILE))
        old = within & age.ge(cutoff)

        successor_hits = result["moves_to_the_expected_successor"].astype(float)
        exit_hits = result["leaves_within_horizon"].astype(float)

        phase_blocks = [block for block in blocks if block["phase"] == name]
        actual = pd.Series(
            [str(block["next_phase"] or "") for block in phase_blocks]
        ).value_counts()

        rows.append(
            {
                "phase": name,
                "expected_successor": successor,
                "weeks": int(within.sum()),
                "episodes": len(phase_blocks),
                "late_weeks": int(late.sum()),
                "late_share": round(float(late.sum() / within.sum()), 4),
                # 비율만 적으면 20주짜리 표본이 500주짜리와 같아 보인다. 건수도 같이 적는다.
                "late_weeks_followed_by_the_successor": int(successor_hits[late].sum()),
                "late_weeks_not_followed_by_the_successor": int(
                    late.sum() - successor_hits[late].sum()
                ),
                "duration_cutoff_weeks": round(cutoff, 1),
                "successor_rate": {
                    "base": _rate(within, successor_hits),
                    "duration_only": _rate(old, successor_hits),
                    "late_signal": _rate(late, successor_hits),
                },
                "exit_rate": {
                    "base": _rate(within, exit_hits),
                    "duration_only": _rate(old, exit_hits),
                    "late_signal": _rate(late, exit_hits),
                },
                "late_signal_beats_duration_on_successor": _beats(
                    _rate(late, successor_hits), _rate(old, successor_hits)
                ),
                "late_signal_beats_duration_on_exit": _beats(
                    _rate(late, exit_hits), _rate(old, exit_hits)
                ),
                "within_run_shift_p_successor": _within_run_shift_p(
                    scored["late"], successor_hits, frame["phase"], name
                ),
                "within_run_shift_p_exit": _within_run_shift_p(
                    scored["late"], exit_hits, frame["phase"], name
                ),
                "where_the_phase_actually_went": {
                    str(key): int(value) for key, value in actual.items()
                },
            }
        )
    return rows


def _beats(signal: float | None, control: float | None) -> bool | None:
    if signal is None or control is None:
        return None
    return bool(signal > control)


def duration_independence(
    frame: pd.DataFrame, scored: pd.DataFrame, horizon: int = HORIZON_WEEKS
) -> list[dict[str, Any]]:
    """경과 기간을 고정해도 신호가 남는가.

    후반 신호가 켜진 주 중 **오래되지 않은** 주만 따로 본다. 거기서도 적중률이 base보다
    높으면, 신호가 경과 기간을 다시 쓴 것이 아니라는 증거다.
    """

    result = outcomes(frame["phase"], horizon)
    age = elapsed(frame["phase"])
    successor_hits = result["moves_to_the_expected_successor"].astype(float)

    rows: list[dict[str, Any]] = []
    for name in SUCCESSOR:
        within = frame["phase"].eq(name)
        if not bool(within.any()):
            continue
        cutoff = float(age[within].quantile(DURATION_QUANTILE))
        young = within & age.lt(cutoff)
        late_young = scored["late"] & young
        rows.append(
            {
                "phase": name,
                "young_weeks": int(young.sum()),
                "late_and_young_weeks": int(late_young.sum()),
                "successor_rate_all_young": _rate(young, successor_hits),
                "successor_rate_late_and_young": _rate(late_young, successor_hits),
                "signal_survives_holding_duration_fixed": _beats(
                    _rate(late_young, successor_hits), _rate(young, successor_hits)
                ),
                "median_elapsed_weeks_when_late": (
                    round(float(age[scored["late"] & within].median()), 1)
                    if int((scored["late"] & within).sum())
                    else None
                ),
                "median_elapsed_weeks_in_phase": round(float(age[within].median()), 1),
            }
        )
    return rows


def cycle_order_holds(frame: pd.DataFrame) -> dict[str, Any]:
    """이 모델의 실제 경로가 순환 순서를 따르는가.

    Track 18은 "다음 국면이 무엇인지는 이미 안다"를 전제로 문제를 쉽게 만든다. 그 전제가
    이 모델의 경로에서 성립하는지 **먼저** 확인해야 한다. 성립하지 않으면 후반부 신호를
    아무리 잘 만들어도 예고 대상이 틀린 것이 된다.
    """

    blocks = runs(frame["phase"])
    pairs = [
        (str(block["phase"]), str(block["next_phase"])) for block in blocks if block["next_phase"]
    ]
    following: dict[str, dict[str, int]] = {name: {} for name in SUCCESSOR}
    for source, destination in pairs:
        table = following.setdefault(source, {})
        table[destination] = table.get(destination, 0) + 1

    summary: dict[str, Any] = {}
    for name, successor in SUCCESSOR.items():
        table = following.get(name, {})
        total = sum(table.values())
        summary[name] = {
            "transitions": total,
            "to_the_expected_successor": table.get(successor, 0),
            "share": round(table.get(successor, 0) / total, 3) if total else None,
            "where_it_went_instead": {
                key: value for key, value in table.items() if key != successor
            },
        }
    ordered = sum(entry["to_the_expected_successor"] for entry in summary.values())
    total = sum(entry["transitions"] for entry in summary.values())
    return {
        "by_phase": summary,
        "transitions": total,
        "following_the_cycle_order": ordered,
        "share_following_the_cycle_order": round(ordered / total, 3) if total else None,
    }
