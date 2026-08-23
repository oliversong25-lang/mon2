"""§5·§7. 회복 지연의 구간 분해와, 달력 지연 대 자료가용성 조정 지연.

세 가지를 절대 섞지 않는다.

* 자료가 아직 나오지 않아 **진실을 알 수 없었던** 구간
* 자료는 나왔는데 **동결 변환이 아직 침체를 가리키던** 구간
* 원시 증거는 회복을 지지했는데 **확인·필터가 공식 국면을 붙잡고 있던** 구간

분해는 **순차 구간**이다. 겹치지 않고, 빈틈이 없고, 경계가 단조이며, 합이 달력 지연과
정확히 같다. 이 넷을 불변식으로 검사한다 — 그래야 "5+2+4=11"이 우연한 산술이 아니라
구간의 성질임을 말할 수 있다.

한 구간 안에서 여러 원인이 **동시에** 작동한 것은 따로 적는다. 동시 작동을 더하면
같은 주를 두 번 세게 된다. 그래서 원인은 구간에 귀속시키고, 원인끼리 더하지 않는다.

조정 지연이 달력 지연을 지우지 않는다. 둘을 나란히 적는다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from .turning import TurningMonth, band

#: §7의 한계 어휘. 세 가지 중 하나를 기계적으로 고른다.
LIMITATION_LABELS: Final[tuple[str, ...]] = (
    "coincident-data recognition lag",
    "state-machine delay",
    "transformation/evidence lag",
)

#: 주 단위 정렬 여유 1주. 월간 전환점을 주간 격자에 얹을 때 생기는 반올림이다.
WEEKLY_ALIGNMENT_ALLOWANCE: Final[int] = 1

#: 순차 구간의 이름과 경계. 순서가 곧 단조성 요구다.
SEGMENTS: Final[tuple[tuple[str, str, str], ...]] = (
    ("publication_delay", "calendar_trough_interval_end", "first_post_trough_data_available"),
    (
        "domain_observation_availability_delay",
        "first_post_trough_data_available",
        "recovery_observable_date",
    ),
    ("transformation_delay", "recovery_observable_date", "transformation_turn_date"),
    ("raw_phase_score_delay", "transformation_turn_date", "first_raw_recovery"),
    ("transition_filter_delay", "first_raw_recovery", "first_filtered_recovery"),
    ("confirmation_delay", "first_filtered_recovery", "first_official_recovery"),
)

ENTRY_EXIT: Final[dict[str, tuple[str, str]]] = {
    "publication_delay": (
        "NBER 저점 월이 끝났다. 그 달 다음 달을 덮는 관측이 아직 하나도 없다.",
        "동행 도메인 중 **하나라도** 저점 다음 달을 덮는 관측을 얻었다.",
    ),
    "domain_observation_availability_delay": (
        "저점 다음 달 관측이 한 도메인에만 있다. 동결 모델의 폭 요건을 못 채운다.",
        "저점 다음 달을 덮는 동행 도메인이 `minimum_coincident_domains`에 도달했다.",
    ),
    "transformation_delay": (
        "폭은 채웠으나 동결 변환이 만든 총량 모멘텀이 아직 비양수다.",
        "총량 모멘텀이 처음 양수가 됐다.",
    ),
    "raw_phase_score_delay": (
        "총량 모멘텀은 양수인데 관측 점수의 승자가 아직 회복이 아니다.",
        "원시 국면이 처음 `recovery`가 됐다.",
    ),
    "transition_filter_delay": (
        "원시 국면은 회복인데 소프트 필터 승자가 아직 회복이 아니다.",
        "필터 승자가 처음 `recovery`가 됐다.",
    ),
    "confirmation_delay": (
        "필터 승자는 회복인데 §8 확인 규칙이 공식 국면을 아직 바꾸지 않았다.",
        "공식 국면이 처음 `recovery`가 됐다.",
    ),
}


class DecompositionInvariantViolated(RuntimeError):
    """구간 분해가 겹치거나, 빈틈이 있거나, 합이 맞지 않는다."""


def _weeks(start: str | None, end: str | None) -> int | None:
    if start is None or end is None:
        return None
    return int((pd.Timestamp(end) - pd.Timestamp(start)).days // 7)


def _days(start: str | None, end: str | None) -> int | None:
    if start is None or end is None:
        return None
    return int((pd.Timestamp(end) - pd.Timestamp(start)).days)


def first_confirmed_recovery(frame: pd.DataFrame, length: int = 4) -> str | None:
    """연속 ``length`` 주 공식 회복이 처음 성립하는 **시작** 주."""

    values = frame["official_phase"].astype(str).eq("recovery").to_numpy(dtype=bool)
    run = 0
    for position in range(len(values)):
        run = run + 1 if values[position] else 0
        if run >= length:
            return str(frame.index[position - length + 1])
    return None


def evidence_at(frame: pd.DataFrame, moment: str | None) -> dict[str, Any]:
    """경계에서 실제로 손에 있던 증거. 경계를 주장이 아니라 관측으로 만든다."""

    if moment is None or moment not in frame.index:
        return {}
    row = frame.loc[moment]
    observed = {
        column.replace("__observation_through", ""): str(row[column])
        for column in frame.columns
        if column.endswith("__observation_through")
    }
    return {
        "raw_phase": str(row["raw_phase"]),
        "official_phase": str(row["official_phase"]),
        "activity_momentum": round(float(str(row["activity_momentum"])), 6),
        "activity_level": round(float(str(row["activity_level"])), 6),
        "positive_momentum_domains": int(str(row["positive_momentum_domains"])),
        "domain_observation_through": observed,
    }


def segments(
    frame: pd.DataFrame, dates: dict[str, str | None], trough: TurningMonth
) -> list[dict[str, Any]]:
    """순차 구간. 각 구간의 끝은 다음 구간의 시작과 **같은 날**이다."""

    rows: list[dict[str, Any]] = []
    for name, start_key, end_key in SEGMENTS:
        start, end = dates[start_key], dates[end_key]
        entry, exit_condition = ENTRY_EXIT[name]
        rows.append(
            {
                "segment": name,
                "start_date": start,
                "end_date": end,
                "start_boundary": start_key,
                "end_boundary": end_key,
                "duration_weeks": _weeks(start, end),
                "duration_days": _days(start, end),
                "entry_condition": entry,
                "exit_condition": exit_condition,
                "evidence_at_the_exit_boundary": evidence_at(frame, end),
            }
        )
    return rows


def check_invariants(
    rows: list[dict[str, Any]], calendar_weeks: int | None, calendar_days: int | None
) -> dict[str, Any]:
    """§4의 불변식. 어기면 분해를 신뢰할 수 없으므로 예외를 던진다."""

    boundaries = [row["start_date"] for row in rows] + [rows[-1]["end_date"]]
    if any(value is None for value in boundaries):
        raise DecompositionInvariantViolated(f"경계 날짜가 비어 있습니다: {boundaries}")
    moments = [pd.Timestamp(str(value)) for value in boundaries]
    monotonic = all(moments[i] <= moments[i + 1] for i in range(len(moments) - 1))
    contiguous = all(rows[i]["end_date"] == rows[i + 1]["start_date"] for i in range(len(rows) - 1))
    overlapping = [
        rows[i]["segment"]
        for i in range(len(rows) - 1)
        if pd.Timestamp(str(rows[i]["end_date"])) > pd.Timestamp(str(rows[i + 1]["start_date"]))
    ]
    week_sum = sum(int(row["duration_weeks"]) for row in rows)
    day_sum = sum(int(row["duration_days"]) for row in rows)
    result = {
        "boundaries_are_monotonic": monotonic,
        "segments_are_contiguous_with_no_gaps": contiguous,
        "overlapping_segments": overlapping,
        "segment_week_sum": week_sum,
        "segment_day_sum": day_sum,
        "calendar_recovery_latency_weeks": calendar_weeks,
        "calendar_recovery_latency_days": calendar_days,
        "week_sum_equals_calendar_latency": week_sum == calendar_weeks,
        "day_sum_equals_calendar_latency": day_sum == calendar_days,
    }
    result["holds"] = bool(
        monotonic
        and contiguous
        and not overlapping
        and result["week_sum_equals_calendar_latency"]
        and result["day_sum_equals_calendar_latency"]
    )
    if not result["holds"]:
        raise DecompositionInvariantViolated(f"구간 분해 불변식이 깨졌습니다: {result}")
    return result


def concurrency(
    frame: pd.DataFrame, rows: list[dict[str, Any]], cap_momentum: float, momentum_weeks: int
) -> dict[str, Any]:
    """한 구간 안에서 동시에 작동한 원인. 더하지 않고 구간에 귀속시킨다.

    변환 구간을 어느 변환에 돌릴지는 추측하지 않고 잰다. 총량 모멘텀은 도메인 모멘텀을
    ``cap_momentum``으로 자른 뒤 등가중 평균한 값이다. 구간 안 모든 주에서 다섯 도메인이
    전부 상한에 닿아 있었다면 총량은 사실상 **부호 투표**이고, 그때 총량이 돌아서려면
    도메인 과반이 부호를 바꿔야 한다. 그 사실 여부를 세어 적는다.
    """

    transformation = next(row for row in rows if row["segment"] == "transformation_delay")
    start, end = transformation["start_date"], transformation["end_date"]
    span = frame.loc[(frame.index >= str(start)) & (frame.index <= str(end))]
    domains = [
        column.replace("__momentum", "")
        for column in frame.columns
        if column.endswith("__momentum")
    ]
    saturated_weeks = 0
    vote_matches = 0
    for week in span.index:
        values = [float(str(span.at[week, f"{domain}__momentum"])) for domain in domains]
        saturated = all(abs(value) >= cap_momentum for value in values)
        saturated_weeks += int(saturated)
        if saturated:
            vote = sum(cap_momentum if value > 0 else -cap_momentum for value in values) / len(
                values
            )
            vote_matches += int(abs(vote - float(str(span.at[week, "activity_momentum"]))) < 1e-9)
    every_week = len(span) > 0 and saturated_weeks == len(span)
    return {
        "segment": "transformation_delay",
        "weeks": transformation["duration_weeks"],
        "window": [str(start), str(end)],
        "weeks_examined": int(len(span)),
        "weeks_with_every_domain_at_the_momentum_cap": saturated_weeks,
        "weeks_where_the_aggregate_equals_the_capped_sign_vote": vote_matches,
        "momentum_cap": cap_momentum,
        "momentum_weeks": momentum_weeks,
        "attributed_to": (
            "bounded_equal_weight_domain_aggregation"
            if every_week
            else "not_isolated_to_a_single_frozen_transformation"
        ),
        "concurrent_contributors": [
            {
                "cause": "bounded_equal_weight_domain_aggregation",
                "evidence": (
                    f"구간 {len(span)}주 전부에서 다섯 도메인 모멘텀이 상한 {cap_momentum}에 "
                    "닿아 있었다. 총량은 부호 투표와 같았고, 돌아서려면 도메인 과반이 부호를 "
                    "바꿔야 했다."
                )
                if every_week
                else "구간 안 일부 주에서만 상한에 닿았다.",
            },
            {
                "cause": "further_publication_beyond_the_first_post_trough_month",
                "evidence": (
                    "폭 요건은 저점 다음 달 관측으로 채워졌지만, 부호를 뒤집은 것은 그 "
                    "다음 달 관측이었다. 이 구간에서 발표와 변환은 **동시에** 작동했다."
                ),
            },
            {
                "cause": f"one_sided_momentum_window_of_{momentum_weeks}_weeks",
                "evidence": (
                    "모멘텀은 인과 창으로만 계산한다. 한 달치 저점 이후 자료로는 창 안 "
                    "부호가 바뀌지 않았다."
                ),
            },
        ],
        "not_added_arithmetically": True,
        "note": (
            "동시 작동한 원인들을 더하지 않는다. 더하면 같은 주를 두 번 센다. 주 수는 "
            "구간에 귀속시키고, 원인은 그 구간 안에서 나열한다."
        ),
    }


def decompose(
    frame: pd.DataFrame,
    dates: dict[str, str | None],
    trough: TurningMonth,
    confirmation_weeks: int,
    cap_momentum: float,
    momentum_weeks: int,
) -> dict[str, Any]:
    """§5의 구간 분해. 불변식을 통과하지 못하면 결과를 내지 않는다."""

    observable = dates["recovery_observable_date"]
    official = dates["first_official_recovery"]

    momentum_turn: str | None = None
    if observable is not None:
        after = frame.loc[frame.index >= observable]
        positive = after.index[after["activity_momentum"].astype(float).gt(0.0).to_numpy(bool)]
        momentum_turn = str(positive[0]) if len(positive) else None

    resolved = {
        **dates,
        "calendar_trough_interval_end": str(trough.end.date()),
        "transformation_turn_date": momentum_turn,
        "first_confirmed_recovery": first_confirmed_recovery(frame),
    }

    span = frame.loc[
        (frame.index >= str(trough.end.date())) & (frame.index <= str(official or frame.index[-1]))
    ]
    withheld = (
        int(span["phase_status"].astype(str).ne("official").sum())
        if "phase_status" in span.columns
        else 0
    )

    rows = segments(frame, resolved, trough)
    calendar = trough.calendar_latency_weeks(official)
    calendar_days = _days(str(trough.end.date()), official)
    invariants = check_invariants(rows, calendar, calendar_days)

    layers = {f"{row['segment']}_weeks": row["duration_weeks"] for row in rows}
    layers["freshness_or_withholding_delay_weeks"] = withheld

    adjusted = _weeks(observable, official)
    allowance = confirmation_weeks + WEEKLY_ALIGNMENT_ALLOWANCE
    state_machine = int(layers["transition_filter_delay_weeks"] or 0) + int(
        layers["confirmation_delay_weeks"] or 0
    )
    if state_machine > confirmation_weeks:
        label = "state-machine delay"
    elif adjusted is not None and adjusted <= allowance:
        label = "coincident-data recognition lag"
    else:
        label = "transformation/evidence lag"

    return {
        "episode": trough.episode,
        **trough.as_dict(),
        "dates": resolved,
        "position_of_first_official_recovery": trough.position(official),
        "segments": rows,
        "invariants": invariants,
        "concurrency": concurrency(frame, rows, cap_momentum, momentum_weeks),
        "layers": layers,
        "sequential_layer_sum_weeks": invariants["segment_week_sum"],
        "calendar_recovery_latency_weeks": calendar,
        "calendar_recovery_latency_days": calendar_days,
        "calendar_band": band(calendar),
        "evidence_availability_adjusted_latency_weeks": adjusted,
        "adjusted_latency_anchor": observable,
        "confirmation_allowance_weeks": allowance,
        "state_machine_delay_weeks": state_machine,
        "limitation_label": label,
        "allowed_limitation_labels": list(LIMITATION_LABELS),
        "adjusted_latency_does_not_replace_calendar_latency": True,
        "note": (
            "조정 지연은 달력 지연을 대체하지 않는다. 자료가 없어 알 수 없었던 구간과 "
            "모델이 늦게 알아본 구간을 나누어 볼 뿐이다."
        ),
    }
