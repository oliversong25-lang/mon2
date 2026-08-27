"""분절 검증. 하나의 재현율 숫자에 서로 다른 실패를 섞지 않는다.

§4의 이유는 이렇다. NBER 침체 주를 전부 같은 의미로 다루면, 침체 말기에 현재
모멘텀이 이미 넓게 양수로 돌아선 주까지 "침체를 놓쳤다"로 세게 된다. 현재상태 정의
아래에서 그런 주는 회복기에 속하는 것이 옳을 수 있다. 그래서 전체 호환성과 **핵심**
침체 탐지를 따로 재고, 저점 인접 회복 분류를 진입 실패와 구분해 남긴다.

에피소드 분할은 **NBER 날짜만으로** 결정된다. 후보의 검증 결과를 들여다보지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from .contract import PHASES

ORDER: Final[dict[str, int]] = {name: index for index, name in enumerate(PHASES)}

#: 저점 인접으로 보는 구간. 에피소드 마지막 8주.
TROUGH_WEEKS: Final[int] = 8


def episodes(truth: pd.Series) -> list[tuple[int, int]]:
    """참인 주가 이어지는 최대 구간. 반열린 [시작, 끝)."""

    values = truth.to_numpy(dtype=bool)
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for position, value in enumerate(values):
        if value and start is None:
            start = position
        elif not value and start is not None:
            spans.append((start, position))
            start = None
    if start is not None:
        spans.append((start, len(values)))
    return spans


def segments(truth: pd.Series) -> dict[str, np.ndarray]:
    """각 에피소드를 주 수로 3등분·2등분한다.

    **핵심 침체 주는 각 에피소드의 가운데 3분의 1**이다. 이 정의는 NBER 날짜만 쓰며
    검증 전에 고정된다. 후보의 성적을 보고 고르지 않는다.
    """

    length = len(truth)
    masks = {
        name: np.zeros(length, dtype=bool)
        for name in ("first_third", "middle_third", "final_third", "first_half")
    }
    for start, end in episodes(truth):
        span = end - start
        first_cut = start + span // 3
        second_cut = start + (2 * span) // 3
        midpoint = start + span // 2
        masks["first_third"][start:first_cut] = True
        masks["middle_third"][first_cut:second_cut] = True
        masks["final_third"][second_cut:end] = True
        masks["first_half"][start:midpoint] = True
    masks["core"] = masks["middle_third"].copy()
    return masks


def trough_mask(truth: pd.Series) -> np.ndarray:
    """각 에피소드의 마지막 몇 주. 저점 인접 판정을 진입 실패와 분리하기 위한 것."""

    mask = np.zeros(len(truth), dtype=bool)
    for start, end in episodes(truth):
        mask[max(start, end - TROUGH_WEEKS) : end] = True
    return mask


def false_positive_episodes(predicted: np.ndarray, truth: np.ndarray) -> dict[str, int]:
    """고립된 몇 주와 지속된 오탐 구간을 같은 것으로 취급하지 않는다."""

    spans = episodes(pd.Series(predicted & ~truth))
    lengths = [end - start for start, end in spans]
    return {
        "false_positive_episodes": len(spans),
        "longest_false_positive_episode": int(max(lengths)) if lengths else 0,
        "four_week_confirmed_false_positive_episodes": int(
            sum(1 for length in lengths if length >= 4)
        ),
    }


def recession_metrics(phase: pd.Series, truth: pd.Series) -> dict[str, Any]:
    """분절 재현율. 하나의 숨은 가중 점수로 합치지 않는다."""

    predicted = phase.eq("contraction").to_numpy(dtype=bool)
    actual = truth.to_numpy(dtype=bool)
    true_positive = int((predicted & actual).sum())
    false_positive = int((predicted & ~actual).sum())
    false_negative = int((~predicted & actual).sum())
    true_negative = int((~predicted & ~actual).sum())
    recall = true_positive / max(true_positive + false_negative, 1)
    precision = true_positive / max(true_positive + false_positive, 1)
    out: dict[str, Any] = {
        "overall_recall": round(recall, 6),
        "false_positive_rate": round(false_positive / max(false_positive + true_negative, 1), 6),
        "precision": round(precision, 6),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-12), 6),
        "recession_weeks": int(actual.sum()),
    }
    for name, mask in segments(truth).items():
        total = int(mask.sum())
        out[f"{name}_weeks"] = total
        out[f"{name}_recall"] = (
            round(float((predicted & mask).sum() / total), 6) if total else float("nan")
        )

    # 저점 인접 회복 분류는 별도로 남긴다. 진입을 놓친 것도, 핵심을 놓친 것도,
    # 금융위기를 30주 늦게 잡은 것도 아니다. 서로 다른 실패다.
    recovering = phase.eq("recovery").to_numpy(dtype=bool)
    trough = trough_mask(truth)
    out["recession_weeks_classified_recovery"] = int((recovering & actual).sum())
    out["trough_adjacent_weeks"] = int(trough.sum())
    out["trough_adjacent_classified_recovery"] = int((recovering & trough).sum())
    out["non_trough_recession_weeks_classified_recovery"] = int(
        (recovering & actual & ~trough).sum()
    )
    out.update(false_positive_episodes(predicted, actual))
    return out


def stability(phase: pd.Series) -> dict[str, Any]:
    """전이·왕복·점유. 안정성이 어디서 왔는지 갈라 볼 수 있어야 한다."""

    values = [str(value) for value in phase]
    one = two = 0
    for previous, current in zip(values[:-1], values[1:], strict=True):
        if previous == current:
            continue
        distance = abs(ORDER[previous] - ORDER[current])
        if min(distance, len(PHASES) - distance) >= 2:
            two += 1
        else:
            one += 1
    whipsaws = sum(
        1
        for position in range(2, len(values))
        if values[position] == values[position - 2] and values[position] != values[position - 1]
    )
    runs: list[int] = []
    entries = {name: 0 for name in PHASES}
    current_value, length = values[0], 1
    entries[current_value] += 1
    for value in values[1:]:
        if value == current_value:
            length += 1
        else:
            runs.append(length)
            current_value, length = value, 1
            entries[current_value] += 1
    runs.append(length)

    # 들어갔는데 한 번도 나오지 못한 국면. 마지막 주를 차지한 국면은 아직 나올 기회가
    # 없었을 뿐이므로 뺀다. 끝난 표본에서는 이 목록이 비어 있는 것이 정상이며, 비어
    # 있지 않다면 계산이 어딘가 어긋난 것이다 — 건전성 불변식이지 흡수의 증거가 아니다.
    #
    # 후보 H를 133주 가둔 것은 진입 횟수가 적어서도, 나가지 못해서도 아니었다.
    # **원시 관측이 다른 국면을 가리키는 동안** 공식 국면이 버틴 것이었다. 그래서
    # 실질적 흡수는 `longest_disagreement_run`으로 재고, 그 값을 게이트에 건다.
    final = values[-1]
    exits = {name: entries[name] - (1 if name == final else 0) for name in PHASES}
    unexited = sorted(
        name for name in PHASES if entries[name] > 0 and exits[name] == 0 and name != final
    )
    return {
        "one_step_transitions": one,
        "two_step_transitions": two,
        "three_week_whipsaws": whipsaws,
        "longest_run_weeks": int(max(runs)),
        "phase_entries": entries,
        "minimum_phase_entries": int(min(entries.values())),
        "phase_exits": exits,
        "unexited_phases": unexited,
        "phase_occupancy": {
            name: round(float(values.count(name) / len(values)), 6) for name in PHASES
        },
        "phases_reached": sorted(set(values)),
    }


def signal_timing(
    phase: pd.Series,
    alert: pd.Series,
    raw_phase: pd.Series,
    start: pd.Timestamp,
    confirmation_weeks: int = 4,
) -> dict[str, Any]:
    """에피소드 시작 대비 경보·원시·공식·4주 확인 시점.

    보조 경보가 제때 울렸는데 공식 국면이 폭 확인을 기다린 것인지, 아니면 아무것도
    보지 못한 것인지가 이 네 숫자로 갈린다.
    """

    index = pd.DatetimeIndex(phase.index)
    located = int(index.get_indexer(pd.DatetimeIndex([pd.Timestamp(start)]), method="bfill")[0])
    if located < 0:
        return {"episode_start": str(pd.Timestamp(start).date()), "covered": False}

    def _first(mask: pd.Series) -> tuple[int | None, str | None]:
        values = mask.to_numpy(dtype=bool)
        for offset in range(located, len(values)):
            if values[offset]:
                return offset - located, str(index[offset].date())
        return None, None

    def _confirmed(mask: pd.Series) -> tuple[int | None, str | None]:
        values = mask.to_numpy(dtype=bool)
        run = 0
        for offset in range(located, len(values)):
            run = run + 1 if values[offset] else 0
            if run >= confirmation_weeks:
                begin = offset - confirmation_weeks + 1
                return begin - located, str(index[begin].date())
        return None, None

    alert_weeks, alert_date = _first(alert.isin(("elevated", "high")))
    raw_weeks, raw_date = _first(raw_phase.eq("contraction"))
    official_weeks, official_date = _first(phase.eq("contraction"))
    confirmed_weeks, confirmed_date = _confirmed(phase.eq("contraction"))
    return {
        "episode_start": str(index[located].date()),
        "covered": True,
        "first_recession_alert_weeks": alert_weeks,
        "first_recession_alert_date": alert_date,
        "first_raw_contraction_weeks": raw_weeks,
        "first_raw_contraction_date": raw_date,
        "first_official_contraction_weeks": official_weeks,
        "first_official_contraction_date": official_date,
        "four_week_confirmed_contraction_weeks": confirmed_weeks,
        "four_week_confirmed_contraction_date": confirmed_date,
    }


def longest_disagreement(raw_phase: pd.Series, official_phase: pd.Series) -> int:
    """원시 관측과 공식 국면이 **연속으로** 어긋난 최장 주 수.

    후보 H의 133주 고착이 정확히 이 숫자였다. 현재상태 판정이 현재 관측과 반년 넘게
    어긋나 있다면 그것은 더 이상 현재상태 판정이 아니다.
    """

    longest = streak = 0
    for value in raw_phase.ne(official_phase):
        streak = streak + 1 if bool(value) else 0
        longest = max(longest, streak)
    return longest


def certainty_monotonicity(separation: pd.Series, reasons: pd.Series) -> dict[str, Any]:
    """저증거 확신 역전이 없는지. 증거가 약할수록 확신도 약해야 한다."""

    high = separation[reasons.eq(0)]
    medium = separation[reasons.eq(1)]
    low = separation[reasons.ge(2)]
    means = {
        "high": round(float(high.mean()), 6) if len(high) else float("nan"),
        "medium": round(float(medium.mean()), 6) if len(medium) else float("nan"),
        "low": round(float(low.mean()), 6) if len(low) else float("nan"),
    }
    ordered = [means[name] for name in ("high", "medium", "low")]
    present = [value for value in ordered if value == value]
    return {
        "mean_separation": means,
        "weeks": {"high": int(len(high)), "medium": int(len(medium)), "low": int(len(low))},
        "no_inversion": all(a >= b for a, b in zip(present[:-1], present[1:], strict=True)),
    }


def transition_delays(filtered_winner: pd.Series, official: pd.Series) -> list[dict[str, Any]]:
    """공식 국면이 바뀔 때, 새 국면이 그전에 몇 주나 필터 승자였는지.

    §8의 확인 규칙이 실제로 얼마나 늦추는지를 재는 값이다. 0이면 즉시 전환이고, 그
    이상이면 확인을 기다린 것이다. 이 분포가 확인 기간을 넘으면 규칙 밖의 지연이
    생겼다는 뜻이므로 그 자체가 결함 신호다.
    """

    winners = [str(value) for value in filtered_winner]
    phases = [str(value) for value in official]
    weeks = list(official.index)
    out: list[dict[str, Any]] = []
    for position in range(1, len(phases)):
        if phases[position] == phases[position - 1]:
            continue
        entered = phases[position]
        delay = 0
        back = position - 1
        while back >= 0 and winners[back] == entered and phases[back] != entered:
            delay += 1
            back -= 1
        out.append(
            {
                "date": str(pd.Timestamp(str(weeks[position])).date()),
                "from": phases[position - 1],
                "to": entered,
                "delay_weeks": delay,
                "immediate": delay == 0,
            }
        )
    return out


def delay_summary(delays: list[dict[str, Any]]) -> dict[str, Any]:
    """전이 지연 분포. 왕복 수 하나로 규칙을 판정하지 않기 위한 것."""

    values = [int(item["delay_weeks"]) for item in delays]
    per_phase: dict[str, list[int]] = {name: [] for name in PHASES}
    for item in delays:
        per_phase[str(item["to"])].append(int(item["delay_weeks"]))
    return {
        "official_transitions": len(delays),
        "immediate_transitions": sum(1 for value in values if value == 0),
        "confirmed_transitions": sum(1 for value in values if value > 0),
        "median_delay_weeks": round(float(np.median(values)), 4) if values else 0.0,
        "p90_delay_weeks": round(float(np.quantile(values, 0.9)), 4) if values else 0.0,
        "maximum_delay_weeks": int(max(values)) if values else 0,
        "entry_delay_by_phase": {
            name: {
                "entries": len(items),
                "median": round(float(np.median(items)), 4) if items else None,
                "maximum": int(max(items)) if items else None,
            }
            for name, items in per_phase.items()
        },
    }
