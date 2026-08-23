"""§2·§3의 결정 일관성 감사.

두 가지를 결과를 본 뒤에 조용히 넘기지 않기 위한 모듈이다.

**2001년 red.** 사전 등록된 구간대는 세 에피소드에 모두 적용되는 어휘지만, 채택
게이트는 §9-A·§9-B가 명시적으로 `2020` 실시간 에피소드 하나로 한정했다. 그 범위를
상수로 못박고, 만약 모든 에피소드에 걸었다면 결론이 어떻게 달라지는지 **반사실**로 함께
적는다. 2001년을 결과를 보고 나서 면제하지 않았다는 것을 그렇게 보인다.

**2009년 5월 22일.** 원시·필터 층이 저점 월보다 앞서 4주 회복 열을 만들었다. 그 열을
"공식 국면이 막았으니 무해하다"고 적지 않는다. 동시에 6월과 절반이 겹치는 열을
"저점 이전"이라고 뭉뚱그리지도 않는다. 위치를 네 어휘로 정확히 적는다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from .turning import TurningMonth, band, peak_month_start

#: §3의 4주 열 위치 어휘. 시작과 끝을 모두 보고 정한다.
SEQUENCE_POSITIONS: Final[tuple[str, ...]] = (
    "entirely_pre_trough_month",
    "begins_pre_trough_and_overlaps_turning_month",
    "within_turning_month",
    "post_trough_month",
)

#: 채택 게이트가 "진짜 저점 이전"으로 보는 위치. 하나뿐이다.
GENUINE_PRE_TROUGH_POSITION: Final[str] = "entirely_pre_trough_month"

#: §9-A·§9-B가 red 게이트를 건 에피소드. 결과를 계산하기 **전에** 명세가 정한 범위다.
RED_GATE_EPISODE: Final[str] = "recession_2020"
RED_GATE_SCOPE: Final[str] = "strict_alfred_real_time_episode_only"
RED_GATE_SCOPE_CITATION: Final[str] = (
    "§9-A '2020 calendar recovery delay is green or amber, not red' · "
    "§9-B '2020 recovery latency is red'"
)

LAYERS: Final[tuple[str, ...]] = ("raw_phase", "filtered_winner", "official_phase")

#: 저점 이후 공백을 무엇이 채웠는지. 기계적으로 정한다.
GAP_KINDS: Final[tuple[str, ...]] = (
    "continuous_post_trough_contraction",
    "recovery_label_skipped_on_the_way_out",
    "mixed_post_trough_path",
    "no_gap",
)


def _runs(values: list[bool]) -> list[tuple[int, int]]:
    """연속 True 구간의 (시작, 끝) 위치. 끝은 포함이다."""

    spans: list[tuple[int, int]] = []
    start: int | None = None
    for position, value in enumerate(values):
        if value and start is None:
            start = position
        elif not value and start is not None:
            spans.append((start, position - 1))
            start = None
    if start is not None:
        spans.append((start, len(values) - 1))
    return spans


def sequence_position(start: str | None, end: str | None, trough: TurningMonth) -> str | None:
    """4주 열의 위치. 시작만 보지 않고 끝까지 본다.

    시작이 6월 앞이어도 열이 6월과 겹치면 `entirely_pre_trough_month`가 아니다. 그
    구분이 없으면 5월 22일에 시작해 6월 12일에 끝나는 열이 "저점 이전"으로 뭉뚱그려진다.
    """

    if start is None or end is None:
        return None
    first, last = pd.Timestamp(start), pd.Timestamp(end)
    if last < trough.start:
        return "entirely_pre_trough_month"
    if first < trough.start:
        return "begins_pre_trough_and_overlaps_turning_month"
    if first <= trough.end:
        return "within_turning_month"
    return "post_trough_month"


def layer_recovery_timeline(
    frame: pd.DataFrame, episode: str, trough: TurningMonth, length: int = 4
) -> list[dict[str, Any]]:
    """§3. 원시·필터·공식 세 층을 각각 적는다. 어느 층도 다른 층 뒤에 숨기지 않는다."""

    peak = peak_month_start(episode)
    index = pd.Index([str(value) for value in frame.index])
    moments = pd.to_datetime(index)
    window = moments >= peak
    rows: list[dict[str, Any]] = []
    for layer in LAYERS:
        if layer not in frame.columns:
            continue
        recovery = frame[layer].astype(str).eq("recovery").to_numpy(dtype=bool) & window
        contraction = frame[layer].astype(str).eq("contraction").to_numpy(dtype=bool)
        hits = index[recovery]
        first = str(hits[0]) if len(hits) else None

        start = end = None
        for span_start, span_end in _runs(list(recovery)):
            if span_end - span_start + 1 >= length:
                start = str(index[span_start])
                end = str(index[span_start + length - 1])
                break

        before_june = inside = 0
        if start is not None and end is not None:
            weeks = moments[(moments >= pd.Timestamp(start)) & (moments <= pd.Timestamp(end))]
            before_june = int((weeks < trough.start).sum())
            inside = int(((weeks >= trough.start) & (weeks <= trough.end)).sum())

        row: dict[str, Any] = {
            "episode": episode,
            "layer": layer,
            "first_recovery_week": first,
            "first_four_week_sequence_start": start,
            "first_four_week_sequence_end": end,
            "weeks_of_the_sequence_before_the_trough_month": before_june,
            "weeks_of_the_sequence_inside_the_turning_month": inside,
            "sequence_position": sequence_position(start, end, trough),
            "entire_confirmed_sequence_before_the_trough_month": (
                sequence_position(start, end, trough) == GENUINE_PRE_TROUGH_POSITION
            ),
            "entered_recovery_before_the_trough_month": bool(
                len(hits) and pd.Timestamp(str(hits[0])) < trough.start
            ),
        }
        if first is not None:
            begin = pd.Timestamp(first)
            for ahead in (4, 8, 13):
                span = (moments > begin) & (moments <= begin + pd.Timedelta(weeks=ahead))
                row[f"return_to_contraction_within_{ahead}_weeks"] = int((contraction & span).sum())
        else:
            for ahead in (4, 8, 13):
                row[f"return_to_contraction_within_{ahead}_weeks"] = None
        row["is_the_adoption_gate_layer"] = layer == "official_phase"
        row["role"] = (
            "adoption_gate"
            if layer == "official_phase"
            else "stability_diagnostic_disclosed_not_gated"
        )
        rows.append(row)
    return rows


def post_trough_phase_path(
    frame: pd.DataFrame, episode: str, trough: TurningMonth
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """§2-5. 저점 월말부터 첫 공식 회복까지의 주별 경로와 그 구성.

    31주가 무엇을 잰 수인지 답하기 위한 것이다 — 연속 침체인가, 회복 라벨의 부재인가,
    아니면 회복을 거치지 않고 시계를 반대로 돈 것인가.
    """

    index = pd.Index([str(value) for value in frame.index])
    moments = pd.to_datetime(index)
    official = frame["official_phase"].astype(str).to_numpy()
    recovery = index[(official == "recovery") & (moments > trough.end)]
    first_recovery = str(recovery[0]) if len(recovery) else None
    stop = pd.Timestamp(first_recovery) if first_recovery else moments[-1]
    mask = (moments > trough.end) & (moments <= stop)
    columns = [
        name
        for name in (
            "raw_phase",
            "filtered_winner",
            "official_phase",
            "confirmation_pending",
            "transition_watch",
            "raw_recovery",
            "raw_expansion",
            "raw_slowdown",
            "raw_contraction",
            "filtered_recovery",
            "filtered_expansion",
            "filtered_slowdown",
            "filtered_contraction",
            "phase_separation",
            "evidence_quality_high",
            "activity_level",
            "activity_momentum",
            "negative_level_domains",
            "positive_momentum_domains",
            "confirming_domains",
            "concentration",
        )
        if name in frame.columns
    ]
    freshness = [name for name in frame.columns if name.endswith("__weeks_since_release")]
    path = frame.loc[mask, columns + freshness].copy()
    path.insert(0, "episode", episode)
    path.insert(
        1,
        "weeks_after_trough_month_end",
        [int((pd.Timestamp(str(week)) - trough.end).days // 7) for week in path.index],
    )

    phases = path["official_phase"].astype(str).tolist()
    gap = len(phases)
    contraction_weeks = sum(1 for value in phases if value == "contraction")
    leading = 0
    for value in phases:
        if value != "contraction":
            break
        leading += 1
    tail = phases[leading:]
    only_non_recovery = all(value in ("slowdown", "expansion") for value in tail[:-1] or [])
    if gap == 0:
        kind = "no_gap"
    elif contraction_weeks >= gap * 0.5:
        kind = "continuous_post_trough_contraction"
    elif only_non_recovery:
        kind = "recovery_label_skipped_on_the_way_out"
    else:
        kind = "mixed_post_trough_path"

    exits = [week for week, value in zip(path.index, phases, strict=True) if value != "contraction"]
    summary = {
        "episode": episode,
        **trough.as_dict(),
        "first_official_recovery": first_recovery,
        "calendar_recovery_latency_weeks": trough.calendar_latency_weeks(first_recovery),
        "calendar_band": band(trough.calendar_latency_weeks(first_recovery)),
        "gap_weeks_after_the_trough_month": gap,
        "official_contraction_weeks_in_the_gap": contraction_weeks,
        "leading_contraction_run_weeks": leading,
        "first_week_out_of_official_contraction": str(exits[0]) if exits else None,
        "contraction_exit_latency_weeks": trough.calendar_latency_weeks(
            str(exits[0]) if exits else None
        ),
        "contraction_exit_band": band(
            trough.calendar_latency_weeks(str(exits[0]) if exits else None)
        ),
        "weeks_by_official_phase": {
            name: int(sum(1 for value in phases if value == name))
            for name in ("recovery", "expansion", "slowdown", "contraction")
        },
        "official_recovery_emitted_before_leaving_contraction": bool(
            phases and "recovery" in phases[:leading]
        ),
        "gap_kind": kind,
        "allowed_gap_kinds": list(GAP_KINDS),
        "what_the_latency_measures": (
            "공식 `recovery` 라벨이 나오기까지 걸린 달력 시간이다. 침체가 그만큼 이어졌다는 "
            "뜻이 아니다. 이 에피소드에서는 침체를 먼저 벗어난 뒤 후퇴기·확장기를 지나 "
            "회복기에 닿았다."
        )
        if kind == "recovery_label_skipped_on_the_way_out"
        else "공식 `recovery` 라벨이 나오기까지 걸린 달력 시간이다.",
    }
    return path, summary


def red_scope_audit(turning_audit: list[dict[str, Any]], amber_all_pass: bool) -> dict[str, Any]:
    """§2. 범위를 명시하고, 모든 에피소드에 걸었을 때의 반사실을 함께 적는다."""

    red_episodes = [row["episode"] for row in turning_audit if row["calendar_band"] == "red"]
    gated = [row for row in turning_audit if row["episode"] == RED_GATE_EPISODE]
    gated_band = gated[0]["calendar_band"] if gated else None
    counterfactual = (
        "operational_rejection_confirmed"
        if red_episodes
        else (
            "provisional_operational_adoption"
            if amber_all_pass
            else "operational_rejection_confirmed"
        )
    )
    return {
        "scope": RED_GATE_SCOPE,
        "gate_episode": RED_GATE_EPISODE,
        "declared_before_results": True,
        "citation": RED_GATE_SCOPE_CITATION,
        "applies_to_every_evaluable_episode": False,
        "band_vocabulary_applies_to_every_episode": True,
        "gated_episode_band": gated_band,
        "episodes_in_the_red_band": red_episodes,
        "episodes_in_the_red_band_are_gated": False,
        "counterfactual_if_the_red_gate_applied_to_every_episode": counterfactual,
        "counterfactual_changes_the_decision": bool(red_episodes),
        "why_not_a_gate": (
            "2001년과 금융위기는 진짜 빈티지가 없어 최신 수정치에서만 볼 수 있다. 운영 "
            "게이트는 실시간 거동을 재는 것이고, 최신 수정치 경로는 그 시점에 존재하지 "
            "않았던 정보를 쓴다. 그래서 §9가 게이트를 실시간 에피소드 하나로 한정했다."
        ),
        "not_exempted_after_seeing_the_result": True,
        "reported_as_major_limitation": bool(red_episodes),
    }
