"""상태 타당성 지표. 침체 재현율만으로는 이번 결함을 잡지 못했다.

후보 H는 재현율 88.4%·오탐률 2.22%로 좋아 보였지만 133주 동안 한 국면에 갇혀 있었다.
침체/비침체 이분법만 보는 지표는 그 사실을 볼 수 없다. 그래서 여기서는 **상태가 현재
증거에 반응하는가**를 따로 잰다.

점프가 적은 것이 곧 안정은 아니다. 흡수 상태는 점프가 0이다.
"""

# ruff: noqa: E501

from __future__ import annotations

from typing import Any

import pandas as pd

from .classifier import PHASES

BROAD_OF = {name: name.rsplit("_", 1)[0] for name in PHASES}


def _runs(codes: pd.Series) -> list[tuple[str, int]]:
    runs: list[tuple[str, int]] = []
    current, length = str(codes.iloc[0]), 1
    for value in codes.iloc[1:]:
        if str(value) == current:
            length += 1
        else:
            runs.append((current, length))
            current, length = str(value), 1
    runs.append((current, length))
    return runs


def occupancy(codes: pd.Series) -> dict[str, Any]:
    runs = _runs(codes)
    longest: dict[str, int] = {}
    for phase, length in runs:
        longest[phase] = max(longest.get(phase, 0), length)
    share = codes.value_counts(normalize=True)
    return {
        "phase_share": {k: round(float(v), 6) for k, v in share.items()},
        "max_phase_share": round(float(share.max()), 6),
        "longest_run_by_phase": longest,
        "longest_run_overall": max(length for _, length in runs),
        "run_count": len(runs),
    }


def jump_profile(codes: pd.Series) -> dict[str, Any]:
    order = {name: index for index, name in enumerate(PHASES)}
    size = len(PHASES)
    steps: list[int] = []
    for previous, current in zip(codes.iloc[:-1], codes.iloc[1:], strict=True):
        if previous == current:
            continue
        distance = abs(order[str(previous)] - order[str(current)])
        steps.append(min(distance, size - distance))
    counts = pd.Series(steps).value_counts() if steps else pd.Series(dtype=int)
    whipsaws = 0
    values = list(codes)
    for index in range(2, len(values)):
        if values[index] == values[index - 2] and values[index] != values[index - 1]:
            whipsaws += 1
    return {
        "transitions": len(steps),
        "one_step": int(counts.get(1, 0)),
        "two_step": int(counts.get(2, 0)),
        "three_or_more": int(sum(int(v) for k, v in counts.items() if int(str(k)) >= 3)),
        "three_week_whipsaws": whipsaws,
    }


def responsiveness(
    scores: pd.DataFrame, raw: pd.Series, official: pd.Series, margin: float
) -> dict[str, Any]:
    """원시 국면과 공식 국면의 어긋남, 필터가 만든 이득, 도달 가능성."""

    disagree = raw.ne(official)
    longest = current = 0
    for value in disagree:
        current = current + 1 if value else 0
        longest = max(longest, current)
    gains = pd.Series(
        [
            float(scores.loc[week].max() - scores.loc[week, official.loc[week]])
            for week in scores.index
        ],
        index=scores.index,
    )
    reachable = set(official.unique()) | set(raw.unique())
    return {
        "raw_official_disagreement_rate": round(float(disagree.mean()), 6),
        "longest_disagreement_run": int(longest),
        "filter_reversal_count": int(disagree.sum()),
        "max_filter_gain": round(float(gains.max()), 6),
        "filter_gain_within_margin": bool(gains.max() <= margin + 1e-9),
        "minimum_phase_score": float(scores.min().min()),
        "zero_score_weeks": int((scores <= 0).any(axis=1).sum()),
        "phases_ever_reached": len(reachable),
        "phases_never_reached": sorted(set(PHASES) - reachable),
    }


def evidence_vs_separation(
    scores: pd.DataFrame,
    activity_level: pd.Series,
    activity_momentum: pd.Series,
    neutral_level: float,
    neutral_momentum: float,
) -> dict[str, Any]:
    """약한 증거가 강한 분리도를 만들지 않는지 확인한다.

    후보 H에서는 반대였다 — 저반지름 주의 17.6%가 분리도 0.9를 넘었고, 고반지름 주는
    4.8%였다. 증거가 약할수록 확신이 커지는 구조는 필터가 잡음에서 확신을 만든 것이다.
    """

    top = scores.max(axis=1)
    second = scores.apply(lambda row: float(row.nlargest(2).iloc[1]), axis=1)
    gap = top - second
    weak = (activity_level.abs().reindex(gap.index) <= neutral_level) & (
        activity_momentum.abs().reindex(gap.index) <= neutral_momentum
    )
    return {
        "weak_evidence_weeks": int(weak.sum()),
        "weak_evidence_share": round(float(weak.mean()), 6),
        "weak_high_separation_weeks": int((weak & (gap > 0.9)).sum()),
        "weak_high_separation_share": round(float((gap[weak] > 0.9).mean()), 6)
        if weak.any()
        else 0.0,
        "strong_high_separation_share": round(float((gap[~weak] > 0.9).mean()), 6)
        if (~weak).any()
        else 0.0,
        "median_separation_weak": round(float(gap[weak].median()), 6) if weak.any() else None,
        "median_separation_strong": round(float(gap[~weak].median()), 6) if (~weak).any() else None,
    }


def monotonicity(
    codes: pd.Series,
    activity_level: pd.Series,
    activity_momentum: pd.Series,
    negative_level_domains: pd.Series,
    concentration: pd.Series,
) -> pd.DataFrame:
    """하위국면이 현재 심각도에서 단조로운지 잰다. §7의 실제 시험이다."""

    rows: list[dict[str, Any]] = []
    for phase in PHASES:
        mask = codes.eq(phase)
        if not mask.any():
            rows.append({"phase": phase, "broad": BROAD_OF[phase], "weeks": 0})
            continue
        rows.append(
            {
                "phase": phase,
                "broad": BROAD_OF[phase],
                "weeks": int(mask.sum()),
                "level_median": round(float(activity_level[mask].median()), 4),
                "momentum_median": round(float(activity_momentum[mask].median()), 4),
                "negative_domains_median": round(
                    float(str(negative_level_domains[mask].median())), 3
                ),
                "concentration_median": round(float(concentration[mask].median()), 4),
            }
        )
    return pd.DataFrame(rows)


def monotonicity_checks(frame: pd.DataFrame) -> dict[str, Any]:
    """대국면별로 early→middle→late가 경제적으로 단조인지 판정한다."""

    expectations = {
        # (열, 방향) 방향 -1은 단계가 갈수록 낮아져야 함을 뜻한다.
        "slowdown": [("level_median", -1), ("negative_domains_median", +1)],
        "contraction": [("level_median", -1)],
        "recovery": [("level_median", +1)],
        "expansion": [("momentum_median", -1)],
    }
    results: dict[str, Any] = {}
    for broad, rules in expectations.items():
        ordered = [f"{broad}_{s}" for s in ("early", "middle", "late")]
        sub = frame[frame["phase"].isin(ordered)].set_index("phase").reindex(ordered)
        if sub["weeks"].fillna(0).eq(0).any():
            results[broad] = {"monotonic": None, "reason": "빈 하위국면이 있다"}
            continue
        detail = {}
        ok = True
        for column, direction in rules:
            values = [float(str(sub.loc[name, column])) for name in ordered]
            increasing = all(
                (b - a) * direction >= -1e-9 for a, b in zip(values[:-1], values[1:], strict=True)
            )
            detail[column] = {"values": values, "monotonic": bool(increasing)}
            ok = ok and increasing
        results[broad] = {"monotonic": bool(ok), **detail}
    return results


def convergence_test(
    scores: pd.DataFrame, margin: float, windows: tuple[int, ...] = (4, 13, 26, 52)
) -> dict[str, Any]:
    """유한 기억 시험. 먼 과거가 다른 두 경로가 같은 최근 증거에서 만나는가.

    같은 점수 경로를 서로 다른 초기 국면에서 출발시켜 몇 주 만에 일치하는지 본다.
    여유가 유한하므로 답도 유한해야 한다.
    """

    results: dict[str, Any] = {}
    for window in windows:
        tail = scores.tail(window)
        finals: set[str] = set()
        for start in PHASES:
            previous = start
            for _, row in tail.iterrows():
                adjusted = row.copy()
                adjusted[previous] = adjusted[previous] + margin
                previous = str(adjusted.idxmax())
            finals.add(previous)
        results[f"after_{window}_weeks"] = {
            "distinct_final_phases": len(finals),
            "converged": len(finals) == 1,
            "final_phases": sorted(finals),
        }
    return results


def latency_distribution(scores: pd.DataFrame, margin: float) -> dict[str, Any]:
    """모순되는 증거가 계속될 때 국면이 바뀌기까지 걸린 주 수 분포."""

    top = scores.idxmax(axis=1)
    official: list[str] = []
    previous: str | None = None
    latencies: list[int] = []
    pending = 0
    for week, row in scores.iterrows():
        adjusted = row.copy()
        if previous is not None:
            adjusted[previous] = adjusted[previous] + margin
        winner = str(adjusted.idxmax())
        if previous is not None and str(top.at[week]) != previous:
            pending += 1
            if winner != previous:
                latencies.append(pending)
                pending = 0
        else:
            pending = 0
        official.append(winner)
        previous = winner
    series = pd.Series(latencies, dtype=float)
    return {
        "episodes": int(len(series)),
        "median_weeks": float(series.median()) if len(series) else None,
        "p90_weeks": float(series.quantile(0.9)) if len(series) else None,
        "max_weeks": float(series.max()) if len(series) else None,
        "unresolved_at_end": int(pending),
    }


def recession_metrics(codes: pd.Series, actual: pd.Series) -> dict[str, Any]:
    """기존 침체 지표를 같은 정의로 계속 보고한다."""

    predicted = codes.str.startswith("contraction")
    truth = actual.reindex(codes.index).fillna(False).astype(bool)
    tp = int((predicted & truth).sum())
    fp = int((predicted & ~truth).sum())
    fn = int((~predicted & truth).sum())
    tn = int((~predicted & ~truth).sum())
    recall = tp / max(tp + fn, 1)
    precision = tp / max(tp + fp, 1)
    return {
        "recall": round(recall, 6),
        "false_positive_rate": round(fp / max(fp + tn, 1), 6),
        "precision": round(precision, 6),
        "f1": round(2 * precision * recall / max(precision + recall, 1e-12), 6),
        "true_positive_weeks": tp,
        "false_positive_weeks": fp,
        "false_negative_weeks": fn,
    }


def episode_detection(codes: pd.Series, actual: pd.Series) -> dict[str, Any]:
    """사례별 탐지 시점. 2020년 실패를 숨기지 않는다."""

    predicted = codes.str.startswith("contraction")
    truth = actual.reindex(codes.index).fillna(False).astype(bool)
    confirmed = predicted.rolling(4).sum().eq(4)
    episodes: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start: pd.Timestamp | None = None
    for week, flag in truth.items():
        if flag and start is None:
            start = pd.Timestamp(str(week))
        elif not flag and start is not None:
            episodes.append((start, pd.Timestamp(str(week))))
            start = None
    if start is not None:
        episodes.append((start, pd.Timestamp(str(truth.index[-1]))))

    out: dict[str, Any] = {}
    for begin, end in episodes:
        name = str(begin.year)
        window = predicted.loc[begin:end]
        search = predicted.loc[begin - pd.Timedelta(weeks=26) :]
        first = search[search].index.min() if search.any() else pd.NaT
        decision = confirmed.loc[begin - pd.Timedelta(weeks=26) :]
        confirm = decision[decision].index.min() if decision.any() else pd.NaT
        out[name] = {
            "official_start": str(begin.date()),
            "official_end": str(end.date()),
            "official_weeks": int(len(window)),
            "weeks_called_contraction": int(window.sum()),
            "first_signal": str(pd.Timestamp(str(first)).date()) if pd.notna(first) else "",
            "first_signal_lag_weeks": (
                round((pd.Timestamp(str(first)) - begin).days / 7.0, 1) if pd.notna(first) else None
            ),
            "confirmation": str(pd.Timestamp(str(confirm)).date()) if pd.notna(confirm) else "",
            "confirmation_lag_weeks": (
                round((pd.Timestamp(str(confirm)) - begin).days / 7.0, 1)
                if pd.notna(confirm)
                else None
            ),
        }
    late_2019 = confirmed.loc["2019-06-01":"2020-02-29"]
    post_2022 = confirmed.loc["2022-01-01":]
    out["late_2019_confirmed_false_positive_weeks"] = int(late_2019.sum())
    out["post_2022_confirmed_false_positive_weeks"] = int(
        (post_2022 & ~truth.reindex(post_2022.index).fillna(False)).sum()
    )
    return out


def sector_agreement(
    coordinates: pd.DataFrame, codes: pd.Series, bounds: dict[str, tuple[float, float]]
) -> dict[str, Any]:
    """X/Y 구역과 공식 국면의 일치율. **진단으로만** 보고한다."""

    def sector(angle: float) -> str:
        value = float(angle) % 360.0
        for name, (start, end) in bounds.items():
            if start <= value < end:
                return name
        return "?"

    sectors = coordinates["angle"].apply(sector)
    agree = sectors.reindex(codes.index).eq(codes)
    return {
        "agreement_rate": round(float(agree.mean()), 6),
        "note": "진단 전용. 공식 국면은 좌표 구역에 의존하지 않는다.",
    }
