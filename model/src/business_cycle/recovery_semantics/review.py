"""회복 의미론 심사의 조립. 동결 v1.1을 읽기만 한다.

증거 위계는 앞 단계와 같다. 바뀐 것은 **월간 전환점을 주간 출력과 맞대는 규약** 하나뿐이며,
그 규약은 결과를 보기 전에 §3에 못박혔다.

2013-06-14 이전 에피소드(2001, 금융위기)에는 진짜 빈티지가 없다. 그래서 그 둘은 최신
수정치 인과 경로에서만 볼 수 있고, 표본 역할을 그렇게 적는다. 실시간 결과로 위장하지
않는다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ..config import Settings, load_settings
from ..four_phase import alfred as AL
from ..four_phase.engine import load_config
from ..operational_review.review import load_alfred_path
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import canonical, consistency, gates, latency, preserve, timeline, turning
from . import manifest as MF
from .decide import classify

OUTPUT_NAME = "recovery_semantics"
AS_OF = pd.Timestamp(END)

#: 2020년 실시간 창. 저점 월 직전부터 13주 왕복 검사 뒤까지 덮는다.
REAL_TIME_WINDOW: Final[tuple[str, str]] = ("2020-04-03", "2020-11-27")

#: 표본 역할. 어느 것도 손대지 않은 홀드아웃이 아니다.
SAMPLE_ROLES: Final[dict[str, str]] = {
    "recession_2001": "development_latest_vintage",
    "gfc_2009": "development_latest_vintage",
    "recession_2020": "strict_alfred_real_time",
}


def merged_real_time_frame(settings: Settings, config: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
    """도메인 기록과 앞 단계의 실시간 경로를 맞댄다. 어긋나면 그 사실을 적는다.

    도메인 기록은 이 단계가 다시 계산하고, 국면·상태는 앞 단계 산출물에서 가져온다.
    둘이 어긋나면 재현이 깨진 것이므로 조용히 덮지 않고 불일치 수를 남긴다.
    """

    start, end = (pd.Timestamp(value) for value in REAL_TIME_WINDOW)
    detail = timeline.build(settings, config, start, end)
    path = load_alfred_path(settings)
    path.index = pd.Index([str(pd.Timestamp(value).date()) for value in path.index], name="as_of")
    shared = detail.index.intersection(path.index)
    authoritative = path.loc[
        shared, ["raw_phase", "official_phase", "phase_status", "filtered_winner"]
    ]
    disagreement = {
        column: int(
            (detail.loc[shared, column].astype(str) != authoritative[column].astype(str)).sum()
        )
        for column in ("raw_phase", "official_phase", "filtered_winner")
    }
    frame = detail.loc[shared].copy()
    for column in ("raw_phase", "official_phase", "phase_status", "filtered_winner"):
        frame[column] = authoritative[column].astype(str)
    return frame, {
        "window": list(REAL_TIME_WINDOW),
        "as_of_weeks": int(len(frame)),
        "recomputed_versus_recorded_disagreements": disagreement,
        "reproduces_the_recorded_real_time_path": not any(disagreement.values()),
        "source": "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv",
    }


def run(settings: Settings | None = None) -> dict[str, Any]:
    base = settings or load_settings()
    provenance = preserve.verify(base)
    provenance["executed_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    config = load_config(base)

    real_time, reproduction = merged_real_time_frame(base, config)
    latest = timeline.latest_vintage_detail(base, config, AS_OF)

    trough_2020 = turning.turning_month("recession_2020")
    dates = timeline.availability_dates(
        real_time, trough_2020.end, config.thresholds.minimum_coincident_domains
    )
    decomposition = latency.decompose(
        real_time,
        dates,
        trough_2020,
        config.confirmation_weeks,
        config.cap_momentum,
        config.momentum_weeks,
    )
    domain_rows = timeline.domain_recovery_timeline(
        real_time, trough_2020.start, trough_2020.end, config.thresholds
    )

    gfc_window = latest.loc[(latest.index >= "2007-12-01") & (latest.index <= "2010-12-31")]
    detail_2009 = gates.episode_2009_detail(gfc_window, turning.turning_month("gfc_2009"))
    layer_timelines = consistency.layer_recovery_timeline(
        gfc_window, "gfc_2009", turning.turning_month("gfc_2009")
    )
    detail_2009["layer_timelines"] = layer_timelines
    detail_2009["reclassification"] = next(
        row["sequence_position"] for row in layer_timelines if row["layer"] == "official_phase"
    )
    detail_2009["definitively_premature"] = (
        detail_2009["reclassification"] == consistency.GENUINE_PRE_TROUGH_POSITION
    )
    gap_path, gap_summary = consistency.post_trough_phase_path(
        latest.loc[(latest.index >= "2001-11-01") & (latest.index <= "2003-06-30")],
        "recession_2001",
        turning.turning_month("recession_2001"),
    )

    scans = [
        gates.pre_trough_recovery_scan(
            latest, "recession_2001", turning.turning_month("recession_2001")
        ),
        gates.pre_trough_recovery_scan(latest, "gfc_2009", turning.turning_month("gfc_2009")),
        gates.pre_trough_recovery_scan(real_time, "recession_2020", trough_2020),
    ]
    for scan in scans:
        scan["sample_role"] = SAMPLE_ROLES[scan["episode"]]

    # 세 에피소드의 달력 지연을 같은 구간대로 잰다. 게이트는 2020년에만 걸지만,
    # 2001년이 어디에 떨어지는지 숨기지 않는다.
    turning_audit: list[dict[str, Any]] = []
    for episode in ("recession_2001", "gfc_2009", "recession_2020"):
        month = turning.turning_month(episode)
        frame = real_time if episode == "recession_2020" else latest
        window = frame.loc[frame.index >= str(turning.peak_month_start(episode).date())]
        official = window.index[window["official_phase"].astype(str).eq("recovery").to_numpy(bool)]
        exit_weeks = window.index[
            window["official_phase"].astype(str).ne("contraction").to_numpy(bool)
        ]
        exit_after = [w for w in exit_weeks if pd.Timestamp(w) > month.start]
        first_official = str(official[0]) if len(official) else None
        first_exit = str(exit_after[0]) if exit_after else None
        turning_audit.append(
            {
                "episode": episode,
                "sample_role": SAMPLE_ROLES[episode],
                **month.as_dict(),
                "first_official_recovery": first_official,
                "position": month.position(first_official),
                "calendar_recovery_latency_weeks": month.calendar_latency_weeks(first_official),
                "calendar_band": turning.band(month.calendar_latency_weeks(first_official)),
                "first_week_out_of_official_contraction": first_exit,
                "contraction_exit_latency_weeks": month.calendar_latency_weeks(first_exit),
                "contraction_exit_band": turning.band(month.calendar_latency_weeks(first_exit)),
                "gated": episode == "recession_2020",
            }
        )

    core, source = load_core_observations(base)
    path = load_alfred_path(base)
    recession = _official_recession_flags(source, pd.DatetimeIndex(path.index))
    audit = json.loads(
        (base.root / "outputs/four_phase_v1_1/alfred_audit/audit_summary.json").read_text(
            encoding="utf-8"
        )
    )
    cache = AL.cache_audit(base, AS_OF)
    recheck = gates.recheck(path, recession, audit, cache)

    round_trips = detail_round_trips(real_time, dates["first_official_recovery"])
    amber = gates.amber_conditions(decomposition, scans, recheck, path, round_trips)
    decision = classify(
        reclassification_2009=detail_2009["reclassification"],
        genuine_pre_trough_episodes=[
            scan["episode"]
            for scan in scans
            if scan.get("official_phase__genuine_pre_trough_four_week_recovery") is not None
        ],
        calendar_band=decomposition["calendar_band"],
        amber=amber,
        all_previous_gates_pass=recheck["all_previously_passed_gates_still_pass"],
        model_or_parameter_changed=False,
        measurable=dates["first_official_recovery"] is not None,
    )

    usrec = _official_recession_flags(source, pd.DatetimeIndex(pd.to_datetime(latest.index)))
    usrec.index = pd.Index(latest.index)
    secondary = {
        episode: turning.usrec_secondary_comparison(usrec, turning.turning_month(episode))
        for episode in ("recession_2001", "gfc_2009", "recession_2020")
    }

    red_scope = consistency.red_scope_audit(
        turning_audit, all(entry["passes"] for entry in amber.values())
    )

    payload: dict[str, Any] = {
        "provenance": provenance,
        "convention": {
            "primary": "interval_censored_monthly_turning_point",
            "positions": list(turning.POSITIONS),
            "bands": {
                "green": f"<= {turning.GREEN_MAXIMUM_WEEKS}주",
                "amber": f"{turning.GREEN_MAXIMUM_WEEKS + 1}~{turning.AMBER_MAXIMUM_WEEKS}주",
                "red": f"> {turning.AMBER_MAXIMUM_WEEKS}주",
            },
            "band_meaning": dict(turning.BAND_MEANING),
            "bands_declared_before_the_audit": True,
            "no_day_inside_the_trough_month_was_selected": True,
            "usrec_secondary_comparison": secondary,
        },
        "reproduction": reproduction,
        "sample_roles": dict(SAMPLE_ROLES),
        "turning_month_audit": turning_audit,
        "episode_2009": detail_2009,
        "layer_recovery_timelines": layer_timelines,
        "red_scope_audit": red_scope,
        "post_trough_gap": gap_summary,
        "pre_trough_scans": scans,
        "recovery_availability_dates": dates,
        "delay_decomposition": decomposition,
        "domain_recovery_timeline": domain_rows,
        "rechecked_gates": recheck,
        "amber_conditions": amber,
        "round_trips_within_13_weeks": round_trips,
        "decision": decision,
        "cache": cache,
        "evidence_hierarchy": {
            "operational_behaviour": "strict_alfred_real_time",
            "revision_sensitivity": "latest_vintage_causal",
            "episode_labels": "nber_retrospective_monthly_only",
            "2020_is_untouched_holdout": False,
            "strict_real_time_recession_episodes": 1,
            "pre_2013_episodes_have_no_real_time_path": True,
        },
    }
    payload["post_trough_phase_path"] = gap_path.reset_index().to_dict(orient="records")
    if decision["classification"] == "provisional_operational_adoption":
        state = MF.current_state(path, decomposition, provenance)
        MF.validate_contract(state)
        payload["current_state"] = state
        payload["operational_manifest"] = MF.operational_manifest(
            provenance, decision, decomposition
        )
    payload["model_status"] = (
        "provisional"
        if decision["classification"] == "provisional_operational_adoption"
        else "rejected"
    )
    payload["semantic_digest"] = canonical.semantic_digest(payload)
    payload["semantic_digest_covers"] = list(canonical.COVERED)
    payload["semantic_digest_excludes"] = list(canonical.VOLATILE_FIELDS)
    # 전체 지문은 **맨 마지막에** 만든다. 앞에서 만들면 그 뒤에 붙는 키가 지문 밖에 남고,
    # 저장된 산출물로 다시 계산했을 때 값이 달라진다.
    payload["run_digest"] = digest(payload)
    return payload


#: 전체 지문에서 빼는 키. 지문 자신뿐이다.
DIGEST_EXCLUDED_KEYS: Final[tuple[str, ...]] = (
    "run_digest",
    "semantic_digest",
    "semantic_digest_covers",
    "semantic_digest_excludes",
)


def digest(payload: dict[str, Any]) -> str:
    """산출물 **전체**의 지문. 의미 지문이 덮지 않는 표까지 포함해 비결정성을 잡는다.

    `canonical.semantic_digest`가 결정을 지킨다면 이쪽은 재현을 지킨다. 목적이 달라
    둘을 함께 둔다. 빼는 것은 지문 자신과 `canonical.VOLATILE_FIELDS`뿐이다.
    """

    body = {key: value for key, value in payload.items() if key not in DIGEST_EXCLUDED_KEYS}
    body["provenance"] = {
        key: value
        for key, value in dict(body["provenance"]).items()
        if key not in canonical.VOLATILE_FIELDS
    }
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def detail_round_trips(frame: pd.DataFrame, first_official_recovery: str | None) -> int | None:
    """공식 회복 뒤 13주 안에 침체로 돌아간 주 수."""

    if first_official_recovery is None:
        return None
    begin = pd.Timestamp(first_official_recovery)
    moments = pd.to_datetime(pd.Index([str(value) for value in frame.index]))
    ahead = (moments > begin) & (moments <= begin + pd.Timedelta(weeks=13))
    contraction = frame["official_phase"].astype(str).eq("contraction").to_numpy(bool)
    return int((contraction & ahead).sum())


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
