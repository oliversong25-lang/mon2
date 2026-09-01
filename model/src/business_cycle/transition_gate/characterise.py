"""게이트를 만들기 **전에** 688주 전체를 특징짓는다.

손으로 고른 13주는 가설이지 보정 집합이 아니다. 그 표본에서 관찰된 0.419와 0.650 사이의
빈틈이 전체 분포에서도 남는지부터 본다 — 극단만 골라 본 결과라면 빈틈은 선택의 산물이다.

세 가지를 잰다.

1. `separation`의 전체 분포
2. 전이 시점의 `separation`과 그 뒤 국면 **지속 기간**의 관계
3. `official`과 `raw`가 어긋나는 빈도, 그리고 그 어긋남이 되돌림을 예고하는지
"""

from __future__ import annotations

import statistics
from typing import Any, Final

import pandas as pd

PHASES: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")

#: 폭·지속 게이트를 이미 가진 국면. 자연 실험의 대조군이다.
GATED_PHASES: Final[frozenset[str]] = frozenset({"contraction", "recovery"})

#: "짧은 국면"의 기준. 4주 미만이면 월 단위 자료 위에서 의미를 갖기 어렵다.
SHORT_PHASE_WEEKS: Final[int] = 4

#: 되돌림 판정 창. 전이 뒤 이 주 수 안에 원래 국면으로 돌아오면 왕복으로 센다.
REVERSION_WINDOW_WEEKS: Final[int] = 2


def load_path(path: str) -> pd.DataFrame:
    """v1.1이 쓴 주간 경로. 여기서 값을 다시 계산하지 않는다."""

    frame = pd.read_csv(path)
    frame["official_phase"] = frame["official_phase"].fillna("").astype(str)
    frame["raw_phase"] = frame["raw_phase"].fillna("").astype(str)
    return frame.set_index("as_of")


def runs(phases: list[str], weeks: list[str]) -> list[dict[str, Any]]:
    """연속 같은 국면 구간. 보류 주는 국면이 없으므로 구간을 끊는다."""

    out: list[dict[str, Any]] = []
    start = 0
    for position in range(1, len(phases) + 1):
        if position == len(phases) or phases[position] != phases[start]:
            if phases[start] in PHASES:
                out.append(
                    {
                        "phase": phases[start],
                        "start": weeks[start],
                        "end": weeks[position - 1],
                        "weeks": position - start,
                    }
                )
            start = position
    return out


def transitions(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """공식 국면이 실제로 바뀐 지점과 그 주의 증거."""

    weeks = list(frame.index)
    official = frame["official_phase"].tolist()
    raw = frame["raw_phase"].tolist()
    separation = frame["phase_separation"].astype(float).tolist()

    out: list[dict[str, Any]] = []
    for position in range(1, len(weeks)):
        before, after = official[position - 1], official[position]
        if not (before in PHASES and after in PHASES and before != after):
            continue

        # 이 전이가 만든 국면이 얼마나 갔는지. 끝까지 이어지면 절단으로 표시한다 —
        # 마지막 국면의 짧은 길이를 "금방 뒤집혔다"로 읽으면 안 된다.
        duration = 0
        for ahead in range(position, len(weeks)):
            if official[ahead] != after:
                break
            duration += 1
        censored = position + duration >= len(weeks)

        # 되돌림: 창 안에서 직전 국면으로 복귀했는가.
        reverted_to_previous = False
        for ahead in range(position + 1, min(position + 1 + REVERSION_WINDOW_WEEKS, len(weeks))):
            if official[ahead] == before:
                reverted_to_previous = True
                break

        out.append(
            {
                "week": weeks[position],
                "from": before,
                "to": after,
                "separation": separation[position],
                "raw": raw[position],
                "raw_agrees": raw[position] == after,
                "raw_stayed_on_previous": raw[position] == before,
                "duration_weeks": duration,
                "censored": censored,
                "short": duration < SHORT_PHASE_WEEKS and not censored,
                "reverted_within_window": reverted_to_previous,
                "target_is_gated_phase": after in GATED_PHASES,
            }
        )
    return out


def _quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def at(fraction: float) -> float:
        if not ordered:
            return float("nan")
        position = fraction * (len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        weight = position - low
        return round(ordered[low] * (1 - weight) + ordered[high] * weight, 4)

    return {
        "min": round(ordered[0], 4),
        "p10": at(0.10),
        "p25": at(0.25),
        "median": at(0.50),
        "p75": at(0.75),
        "p90": at(0.90),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
    }


def separation_distribution(frame: pd.DataFrame) -> dict[str, Any]:
    """전체 분포와, 전이 주 대 비전이 주의 분포. 둘을 갈라 봐야 게이트가 무엇을 자를지 안다."""

    eligible = frame[frame["phase_status"].astype(str).ne("withheld")]
    everything = eligible["phase_separation"].astype(float).tolist()
    moves = {entry["week"] for entry in transitions(frame)}
    at_transition = [
        float(str(eligible.at[week, "phase_separation"]))
        for week in eligible.index
        if week in moves
    ]
    elsewhere = [
        float(str(eligible.at[week, "phase_separation"]))
        for week in eligible.index
        if week not in moves
    ]

    # 후보 임계값 아래에 얼마나 깔려 있는지. 게이트가 자를 표면적이다.
    bands = {}
    for threshold in (0.4, 0.5, 0.6, 0.7):
        bands[f"below_{threshold}"] = {
            "all_weeks": round(sum(1 for v in everything if v < threshold) / len(everything), 4),
            "transition_weeks": (
                round(sum(1 for v in at_transition if v < threshold) / len(at_transition), 4)
                if at_transition
                else None
            ),
        }

    return {
        "eligible_weeks": len(everything),
        "all_weeks": _quantiles(everything),
        "transition_weeks": _quantiles(at_transition) if at_transition else None,
        "non_transition_weeks": _quantiles(elsewhere) if elsewhere else None,
        "share_below": bands,
    }


def separation_versus_duration(frame: pd.DataFrame) -> dict[str, Any]:
    """전이 시점 분리도와 그 뒤 지속 기간의 관계. 어디서 관계가 깨지는지도 적는다."""

    rows = [entry for entry in transitions(frame) if not entry["censored"]]
    if not rows:
        return {"transitions": 0}

    separations = [entry["separation"] for entry in rows]
    durations = [float(entry["duration_weeks"]) for entry in rows]

    # 순위 상관. 관계가 선형이라고 가정하지 않는다 — 지속 기간은 한쪽으로 길게 끌린다.
    def rank(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        position = 0
        while position < len(order):
            end = position
            while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
                end += 1
            average = (position + end) / 2 + 1
            for index in range(position, end + 1):
                out[order[index]] = average
            position = end + 1
        return out

    rs, rd = rank(separations), rank(durations)
    n = len(rs)
    mean_s, mean_d = statistics.fmean(rs), statistics.fmean(rd)
    cov = sum((rs[i] - mean_s) * (rd[i] - mean_d) for i in range(n))
    var_s = sum((value - mean_s) ** 2 for value in rs)
    var_d = sum((value - mean_d) ** 2 for value in rd)
    spearman = cov / ((var_s * var_d) ** 0.5) if var_s and var_d else float("nan")

    # 분리도 구간별 결과. 관계가 단조인지, 어디서 무너지는지 눈으로 보게 만든다.
    buckets: list[dict[str, Any]] = []
    edges = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 1.01]
    for low, high in zip(edges[:-1], edges[1:], strict=True):
        inside = [entry for entry in rows if low <= entry["separation"] < high]
        if not inside:
            continue
        durations_here = [entry["duration_weeks"] for entry in inside]
        buckets.append(
            {
                "band": f"{low:.1f}–{high:.1f}",
                "transitions": len(inside),
                "median_duration_weeks": round(statistics.median(durations_here), 1),
                "short_phases": sum(1 for entry in inside if entry["short"]),
                "short_rate": round(sum(1 for entry in inside if entry["short"]) / len(inside), 3),
                "reverted": sum(1 for entry in inside if entry["reverted_within_window"]),
            }
        )

    # 관계가 깨지는 곳: 높은 분리도인데 짧았던 전이, 낮은 분리도인데 오래간 전이.
    high_but_short = [
        {
            "week": e["week"],
            "to": e["to"],
            "separation": e["separation"],
            "weeks": e["duration_weeks"],
        }
        for e in rows
        if e["separation"] >= 0.6 and e["short"]
    ]
    low_but_long = [
        {
            "week": e["week"],
            "to": e["to"],
            "separation": e["separation"],
            "weeks": e["duration_weeks"],
        }
        for e in rows
        if e["separation"] < 0.4 and e["duration_weeks"] >= 13
    ]

    return {
        "transitions": len(rows),
        "spearman_rank_correlation": round(spearman, 4),
        "by_band": buckets,
        "where_it_fails": {
            "high_separation_but_short": high_but_short,
            "low_separation_but_lasted_13_weeks_or_more": low_but_long,
        },
    }


def raw_official_disagreement(frame: pd.DataFrame) -> dict[str, Any]:
    """원시와 공식이 어긋나는 빈도, 그리고 그 어긋남이 되돌림을 예고하는지."""

    eligible = frame[frame["phase_status"].astype(str).ne("withheld")]
    official = eligible["official_phase"].tolist()
    raw = eligible["raw_phase"].tolist()
    disagree = sum(1 for a, b in zip(official, raw, strict=True) if a != b)

    rows = [entry for entry in transitions(frame) if not entry["censored"]]
    agree_rows = [entry for entry in rows if entry["raw_agrees"]]
    disagree_rows = [entry for entry in rows if not entry["raw_agrees"]]

    def summarise(group: list[dict[str, Any]]) -> dict[str, Any]:
        if not group:
            return {"transitions": 0}
        return {
            "transitions": len(group),
            "reverted_within_window": sum(1 for e in group if e["reverted_within_window"]),
            "reversion_rate": round(
                sum(1 for e in group if e["reverted_within_window"]) / len(group), 3
            ),
            "short_phases": sum(1 for e in group if e["short"]),
            "short_rate": round(sum(1 for e in group if e["short"]) / len(group), 3),
            "median_duration_weeks": round(
                statistics.median([e["duration_weeks"] for e in group]), 1
            ),
        }

    return {
        "eligible_weeks": len(official),
        "weeks_where_raw_differs_from_official": disagree,
        "disagreement_rate": round(disagree / len(official), 4),
        "at_transition": {
            "raw_agrees_with_the_new_phase": summarise(agree_rows),
            "raw_does_not_agree": summarise(disagree_rows),
        },
        "raw_stayed_on_the_previous_phase": sum(
            1 for entry in rows if entry["raw_stayed_on_previous"]
        ),
    }


def gated_versus_ungated_phases(frame: pd.DataFrame) -> dict[str, Any]:
    """자연 실험. 폭·지속 게이트가 있는 두 국면과 없는 두 국면을 갈라 센다."""

    weeks = list(frame.index)
    official = frame["official_phase"].tolist()
    spans = runs(official, weeks)
    out: dict[str, Any] = {}
    for phase in PHASES:
        mine = [span for span in spans if span["phase"] == phase]
        if not mine:
            continue
        out[phase] = {
            "has_breadth_or_persistence_gate": phase in GATED_PHASES,
            "episodes": len(mine),
            "total_weeks": sum(span["weeks"] for span in mine),
            "median_episode_weeks": round(statistics.median([s["weeks"] for s in mine]), 1),
            "episodes_shorter_than_four_weeks": sum(
                1 for span in mine if span["weeks"] < SHORT_PHASE_WEEKS
            ),
        }
    return out


def run(frame: pd.DataFrame) -> dict[str, Any]:
    moves = transitions(frame)
    return {
        "weeks": int(len(frame)),
        "window": [str(frame.index[0]), str(frame.index[-1])],
        "transitions": len(moves),
        "transition_pairs": {
            f"{entry['from']}->{entry['to']}": sum(
                1 for e in moves if e["from"] == entry["from"] and e["to"] == entry["to"]
            )
            for entry in moves
        },
        "separation_distribution": separation_distribution(frame),
        "separation_versus_duration": separation_versus_duration(frame),
        "raw_official_disagreement": raw_official_disagreement(frame),
        "gated_versus_ungated_phases": gated_versus_ungated_phases(frame),
        "transition_rows": moves,
    }
