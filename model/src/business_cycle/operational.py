"""동결된 미국 4국면 모델을 매주 한 명령으로 실행하는 작은 운영 어댑터.

모델 계산은 기존 ``four_phase`` 코드만 호출한다. 이 모듈은 빈티지 선택, 보호 지문
검사, 현재 결과 설명, append-only 이력만 담당한다. 모델 점수나 전이 규칙을 복제하지
않는 이유는 운영 편의를 추가하다 동결 모델을 몰래 바꾸지 않기 위해서다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

import pandas as pd

from .config import Settings, load_baseline, load_settings
from .current_state.domains import COINCIDENT_DOMAINS, DOMAINS
from .data.alfred import AlfredCollector, MissingCredential, observations_as_of
from .four_phase.alfred import cached_frames
from .four_phase.contract import PHASES
from .four_phase.engine import FourPhaseConfig, FourPhaseRun, load_config, prepare, score
from .four_phase.freshness import evaluate
from .operational_review.preserve import PROTECTED, measure
from .operational_review.review import transition_watch

MODEL_VERSION: Final[str] = "us_four_phase_v1.1"
MODEL_STATUS: Final[str] = "operational_v1"
VALIDATION_STATUS: Final[str] = "provisional"
BASELINE_DATE: Final[str] = "2026-08-14"
AUDITED_PATH: Final[str] = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"

# Git은 이 CSV를 LF로 저장하지만 최초 실행은 Windows CRLF 바이트를 해시했다. 두 값을
# 함께 고정해야 플랫폼 정규화를 모델 변조로 오인하지 않으면서 내용 변경은 막을 수 있다.
FRONTIER_LF_SHA256: Final[str] = "8e8043cddbd43dbedc6b38c39b41af07af1b77969166c46a062a535595f16f94"
FRONTIER_LEGACY_CRLF_SHA256: Final[str] = PROTECTED["frontier_csv"]
RECOVERY_DECISION_SHA256: Final[str] = (
    "ec6fdb414718905bd6cccc359c24e5c6cb8aeb50a050e6472b9fc2dbe45e0f3d"
)
STATE_DECISION_SHA256: Final[str] = (
    "0ea6db2eaabd9adf0c20d9bbf31e633fd4c82737a36dd561550f9f693bd75c1d"
)

HISTORY_FIELDS: Final[tuple[str, ...]] = (
    "as_of_date",
    "official_phase",
    "raw_phase",
    "evidence_quality",
    "raw_recovery",
    "raw_expansion",
    "raw_slowdown",
    "raw_contraction",
    "filtered_recovery",
    "filtered_expansion",
    "filtered_slowdown",
    "filtered_contraction",
    "activity_level",
    "activity_momentum",
    "breadth",
    "concentration",
    "freshness_summary",
    "confirmation_state",
    "transition_watch",
    "phase_status",
    "change_from_prior_week",
    "configuration_hash",
    "result_digest",
)


class OperationalError(RuntimeError):
    """사용자에게 한 줄로 설명할 수 있는 운영 실패."""


class ReproducibilityError(OperationalError):
    """이미 기록한 주를 다른 결과로 덮으려 한 경우."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OperationalError(f"객체여야 하는 산출물입니다: {path}")
    return cast(dict[str, Any], value)


def verify_protected_state(settings: Settings | None = None) -> dict[str, Any]:
    """모델·후보·두 의미 결정의 보호 상태를 실행 전에 확인한다."""

    base = settings or load_settings()
    measured = measure(base)
    mismatched: dict[str, Any] = {
        name: {"expected": expected, "measured": measured[name]}
        for name, expected in PROTECTED.items()
        if name != "frontier_csv" and measured[name] != expected
    }
    frontier_path = base.root / "outputs/four_phase_v1_1/development_frontier.csv"
    frontier = frontier_path.read_bytes()
    # 체크아웃 플랫폼의 줄바꿈만 다를 수 있으므로 먼저 LF로 정규화한다.
    normalized = frontier.replace(b"\r\n", b"\n")
    lf_hash = _sha256(normalized)
    crlf_hash = _sha256(normalized.replace(b"\n", b"\r\n"))
    if lf_hash != FRONTIER_LF_SHA256 or crlf_hash != FRONTIER_LEGACY_CRLF_SHA256:
        mismatched["frontier_csv"] = {
            "expected_lf": FRONTIER_LF_SHA256,
            "measured_lf": lf_hash,
            "expected_legacy_crlf": FRONTIER_LEGACY_CRLF_SHA256,
            "measured_legacy_crlf": crlf_hash,
        }

    recovery = _json(base.root / "outputs/recovery_semantics/recovery_semantics_decision.json")
    state = _json(base.root / "outputs/state_semantics/state_semantics_decision.json")
    if recovery.get("semantic_digest") != RECOVERY_DECISION_SHA256:
        mismatched["recovery_semantics_decision"] = recovery.get("semantic_digest")
    if state.get("semantic_digest") != STATE_DECISION_SHA256:
        mismatched["state_semantics_decision"] = state.get("semantic_digest")
    if recovery.get("classification") != "provisional_operational_adoption":
        mismatched["recovery_semantics_classification"] = recovery.get("classification")
    if state.get("classification") != "provisional_model_locked":
        mismatched["state_semantics_classification"] = state.get("classification")
    if mismatched:
        raise OperationalError(f"동결 모델 보호 상태가 어긋났습니다: {mismatched}")

    protected = [
        "model/configs/four_phase.yaml",
        "model/configs/four_phase_v1_1.yaml",
        "model/src/business_cycle/four_phase",
        "model/outputs/four_phase",
        "model/outputs/four_phase_v1_1",
        "model/outputs/recovery_semantics",
        "model/outputs/state_semantics",
    ]
    repository = base.root.parent
    process = subprocess.run(
        ["git", "status", "--porcelain", "--", *protected],
        cwd=repository,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    dirty = [line for line in process.stdout.splitlines() if line.strip()]
    if dirty:
        raise OperationalError(f"보호 경로에 미커밋 변경이 있습니다: {dirty}")
    return {
        "verified": True,
        "hashes": {**measured, "frontier_csv": FRONTIER_LEGACY_CRLF_SHA256},
        "frontier_git_lf_sha256": lf_hash,
        "frontier_legacy_crlf_sha256": crlf_hash,
        "recovery_semantics_decision": RECOVERY_DECISION_SHA256,
        "state_semantics_decision": STATE_DECISION_SHA256,
        "protected_paths_clean": True,
    }


def _audited_path(settings: Settings) -> pd.DataFrame:
    path = pd.read_csv(settings.root / AUDITED_PATH, dtype=str).fillna("")
    if path.empty or "as_of" not in path:
        raise OperationalError("검증된 ALFRED 주간 경로가 비어 있습니다")
    path["as_of"] = pd.to_datetime(path["as_of"])
    return path.set_index("as_of").sort_index()


def _latest_friday(day: date) -> date:
    return day - timedelta(days=(day.weekday() - 4) % 7)


def _friday_of_week(moment: pd.Timestamp) -> pd.Timestamp:
    return moment.normalize() + pd.Timedelta(days=(4 - moment.weekday()) % 7)


def _raw_cache_exists(settings: Settings) -> bool:
    directory = settings.root / "data/cache/alfred"
    return all(
        (directory / f"{series}.csv").exists() for series in settings.indicators["indicators"]
    )


def _raw_cache_last_week(settings: Settings) -> pd.Timestamp:
    frames = cached_frames(settings)
    latest = max(pd.Timestamp(frame["realtime_start"].max()) for frame in frames.values())
    return _friday_of_week(latest)


def refresh_alfred_cache(settings: Settings) -> dict[str, str]:
    """일곱 계열이 모두 성공한 뒤에만 ALFRED 캐시를 원자적으로 교체한다."""

    target = settings.root / "data/cache/alfred"
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="alfred-refresh-", dir=target.parent) as temporary:
        stage = Path(temporary)
        collector = AlfredCollector(stage)
        latest: dict[str, str] = {}
        for series_id in settings.indicators["indicators"]:
            frame = collector.realtime_observations(str(series_id))
            latest[str(series_id)] = str(pd.Timestamp(frame["realtime_start"].max()).date())
        target.mkdir(parents=True, exist_ok=True)
        for series_id in settings.indicators["indicators"]:
            source = stage / f"{series_id}.csv"
            destination = target / source.name
            shutil.copyfile(source, destination.with_suffix(".csv.tmp"))
        for series_id in settings.indicators["indicators"]:
            temporary_file = target / f"{series_id}.csv.tmp"
            os.replace(temporary_file, target / f"{series_id}.csv")
    return latest


def _number(row: Mapping[str, Any], name: str, default: float = 0.0) -> float:
    value = row.get(name, default)
    if value in (None, ""):
        return default
    return float(str(value))


def _integer(row: Mapping[str, Any], name: str, default: int = 0) -> int:
    return int(round(_number(row, name, float(default))))


def assert_causal(observations: pd.DataFrame, as_of: pd.Timestamp) -> int:
    """명시한 as-of 뒤에 공개된 관측은 한 건도 모델로 넘기지 않는다."""

    release = pd.to_datetime(observations["release_date"])
    count = int(release.gt(as_of).sum())
    if count:
        raise OperationalError(f"미래 관측 {count}건이 감지되어 실행을 중단했습니다")
    return count


def _compute_from_frames(
    settings: Settings, frames: dict[str, pd.DataFrame], as_of: pd.Timestamp
) -> tuple[dict[str, Any], dict[str, dict[str, float]]]:
    observations = observations_as_of(frames, as_of, settings.indicators["indicators"])
    assert_causal(observations, as_of)
    config = load_config(settings)
    baseline = load_baseline("candidate_h_breadth_gate", settings)
    prepared = prepare(observations, baseline, as_of, config)
    if prepared.index.empty:
        return {"as_of": str(as_of.date()), "phase_status": "withheld"}, {}
    eligibility = evaluate(
        as_of, prepared.index, prepared.weeks_since_release, prepared.arrived, config.freshness
    )
    run = score(prepared, config)
    week = pd.Timestamp(run.official_phase.index[-1])
    row = _row_from_run(run, frames, as_of, week, eligibility.status, eligibility.withheld)
    domains = {
        domain: {
            "level": float(str(run.level_scaled.at[week, domain])),
            "momentum": float(str(run.momentum_scaled.at[week, domain])),
        }
        for domain in DOMAINS
    }
    return row, domains


def _row_from_run(
    run: FourPhaseRun,
    frames: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    week: pd.Timestamp,
    status: str,
    withheld: bool,
) -> dict[str, Any]:
    filtered = {phase: float(str(run.filtered_scores.at[week, phase])) for phase in PHASES}
    ordered = sorted(filtered.values(), reverse=True)
    row: dict[str, Any] = {
        "as_of": str(as_of.date()),
        "last_modelled_week": str(week.date()),
        "raw_phase": str(run.raw_phase.loc[week]),
        "official_phase": "" if withheld else str(run.official_phase.loc[week]),
        "phase_status": status,
        "activity_level": float(run.activity_level.loc[week]),
        "activity_momentum": float(run.activity_momentum.loc[week]),
        "negative_level_domains": int(run.negative_level_domains.loc[week]),
        "negative_momentum_domains": int(run.negative_momentum_domains.loc[week]),
        "positive_momentum_domains": int(run.positive_momentum_domains.loc[week]),
        "confirming_domains": int(run.confirming_domains.loc[week]),
        "concentration": float(run.concentration.loc[week]),
        "recession_alert": str(run.alert_level.loc[week]),
        "recession_alert_character": str(run.alert_character.loc[week]),
        "evidence_quality_high": bool(run.evidence_quality_high.loc[week]),
        "phase_separation": ordered[0] - ordered[1],
        "confirmation_pending": int(run.confirmation_pending.loc[week]),
        "filtered_winner": str(run.filtered_winner.loc[week]),
        "future_observations": 0,
        "withheld": int(withheld),
    }
    for phase in PHASES:
        row[f"raw_{phase}"] = float(str(run.raw_scores.at[week, phase]))
        row[f"filtered_{phase}"] = filtered[phase]
    for domain in DOMAINS:
        row[f"age_{domain}"] = float(str(run.weeks_since_release.at[week, domain]))
        row[f"arrived_{domain}"] = int(bool(run.arrived.at[week, domain]))
    for series_id, frame in frames.items():
        visible = frame[(frame["realtime_start"] <= as_of) & (frame["realtime_end"] >= as_of)]
        if not visible.empty:
            row[f"latest_observation_{series_id}"] = str(pd.Timestamp(visible["date"].max()).date())
            row[f"source_vintage_{series_id}"] = str(
                pd.Timestamp(visible["realtime_start"].max()).date()
            )
    return row


def _directional_domains(
    domains: Mapping[str, Mapping[str, float]], phase: str
) -> tuple[list[str], list[str], list[str]]:
    expected = {
        "recovery": (-1, 1),
        "expansion": (1, 1),
        "slowdown": (1, -1),
        "contraction": (-1, -1),
    }.get(phase)
    if expected is None:
        return [], [], sorted(domains)
    opposite = (-expected[0], -expected[1])
    support: list[str] = []
    opposition: list[str] = []
    mixed: list[str] = []
    for name, values in domains.items():
        signs = (
            1 if values["level"] > 0 else -1 if values["level"] < 0 else 0,
            1 if values["momentum"] > 0 else -1 if values["momentum"] < 0 else 0,
        )
        if signs == expected:
            support.append(name)
        elif signs == opposite:
            opposition.append(name)
        else:
            mixed.append(name)
    return sorted(support), sorted(opposition), sorted(mixed)


def _semantic_digest(payload: Mapping[str, Any]) -> str:
    # 결과 지문 자체를 다시 입력으로 삼으면 첫 계산과 이력 계산이 달라진다.
    # 실행 시각과 자기 지문만 제외해 같은 경제 상태는 언제 실행해도 같은 값을 갖는다.
    stable = {
        key: value for key, value in payload.items() if key not in {"generated_at", "result_digest"}
    }
    data = json.dumps(stable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return _sha256(data.encode("utf-8"))


def _previous_row(path: pd.DataFrame, as_of: pd.Timestamp) -> Mapping[str, Any] | None:
    earlier = path.loc[path.index < as_of]
    if earlier.empty:
        return None
    return cast(Mapping[str, Any], earlier.iloc[-1].to_dict())


def _transition_detail(
    path: pd.DataFrame, as_of: pd.Timestamp, current: Mapping[str, Any]
) -> dict[str, Any]:
    previous = _previous_row(path, as_of)
    old = str(previous.get("official_phase", "")) if previous else ""
    new = str(current.get("official_phase", ""))
    changed = bool(old and new and old != new)
    detail: dict[str, Any] = {
        "changed": changed,
        "previous_official_phase": old or None,
        "new_official_phase": new or None,
    }
    if not changed:
        return detail
    official_transition = as_of
    history = path.loc[path.index <= as_of]
    raw_matches = history.index[history["raw_phase"].astype(str).eq(new)]
    first_raw = raw_matches[-1] if len(raw_matches) else official_transition
    for candidate in reversed(list(raw_matches)):
        if candidate > official_transition:
            continue
        previous_week = candidate - pd.Timedelta(days=7)
        if previous_week not in history.index or str(history.at[previous_week, "raw_phase"]) != new:
            first_raw = candidate
            break
    detail.update(
        {
            "first_raw_week_supporting_change": str(pd.Timestamp(first_raw).date()),
            "official_transition_week": str(official_transition.date()),
            "confirmation_delay_weeks": int((official_transition - first_raw).days // 7),
            "persisted_through_as_of": True,
        }
    )
    return detail


def build_payload(
    row: Mapping[str, Any],
    path: pd.DataFrame,
    config: FourPhaseConfig,
    domains: Mapping[str, Mapping[str, float]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    as_of = pd.Timestamp(str(row["as_of"]))
    status = str(row.get("phase_status", "withheld"))
    official = str(row.get("official_phase", "")) or None
    raw = str(row.get("raw_phase", "")) or None
    raw_scores = {phase: round(_number(row, f"raw_{phase}"), 9) for phase in PHASES}
    filtered = {phase: round(_number(row, f"filtered_{phase}"), 9) for phase in PHASES}
    detail = domains or {}
    evidence_quality = (
        "high" if str(row.get("evidence_quality_high", "False")).lower() == "true" else "low"
    )
    ranking: list[dict[str, Any]] = []
    ordered_phases = sorted(PHASES, key=lambda name: filtered[name], reverse=True)
    for rank, phase in enumerate(ordered_phases, start=1):
        phase_support, phase_opposition, phase_mixed = _directional_domains(detail, phase)
        ranking.append(
            {
                "phase": phase,
                "raw_score": raw_scores[phase],
                "filtered_score": filtered[phase],
                "rank": rank,
                "supporting_domains": phase_support,
                "opposing_domains": phase_opposition,
                "mixed_domains": phase_mixed,
                "breadth": {
                    "supporting_domains": len(phase_support),
                    "available_domains": len(detail),
                },
                "evidence_quality": evidence_quality,
            }
        )
    support, opposition, mixed = _directional_domains(detail, official or "")
    freshness = {domain: _number(row, f"age_{domain}", -1.0) for domain in DOMAINS}
    stale = sorted(
        domain for domain, weeks in freshness.items() if weeks >= config.stale_weeks and weeks >= 0
    )
    observation_dates = {
        key.removeprefix("latest_observation_"): str(value)
        for key, value in row.items()
        if str(key).startswith("latest_observation_") and value not in (None, "")
    }
    source_dates = {
        key.removeprefix("source_vintage_"): str(value)
        for key, value in row.items()
        if str(key).startswith("source_vintage_") and value not in (None, "")
    }
    transition = _transition_detail(path, as_of, row)
    watch = transition_watch(filtered, official or "")
    previous = transition.get("previous_official_phase")
    change = (
        "unchanged" if previous == official else f"{previous or 'none'}_to_{official or 'withheld'}"
    )
    limitations = [
        "Strict real-time validation contains only one recession episode, "
        "so generalisation is limited.",
        "The 2020 episode is not an untouched holdout.",
        "Genuine common ALFRED vintage coverage starts on 2013-06-14.",
        "The model remains provisional rather than finally validated.",
        "Recovery recognition has an amber latency warning: 11 calendar weeks and "
        "4 evidence-adjusted weeks.",
    ]
    payload: dict[str, Any] = {
        "as_of_date": str(as_of.date()),
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "model_version": MODEL_VERSION,
        "model_status": MODEL_STATUS,
        "validation_status": VALIDATION_STATUS,
        "configuration_hash": config.sha256,
        "official_current_phase": official,
        "raw_current_phase": raw,
        "phase_status": status,
        "evidence_quality": evidence_quality,
        "raw_phase_scores": raw_scores,
        "filtered_phase_scores": filtered,
        "phase_ranking": ranking,
        "phase_separation": round(_number(row, "phase_separation"), 9),
        "activity_level": round(_number(row, "activity_level"), 9),
        "activity_momentum": round(_number(row, "activity_momentum"), 9),
        "domain_support": {
            "domains": support,
            "directional_rule": "level_and_momentum_signs_match_the_official_phase",
            "details": detail,
        },
        "domain_opposition": {"domains": opposition},
        "mixed_domains": mixed,
        "domain_freshness": freshness,
        "stale_domains": stale,
        "breadth": {
            "confirming_coincident_domains": _integer(row, "confirming_domains"),
            "negative_level_domains": _integer(row, "negative_level_domains"),
            "negative_momentum_domains": _integer(row, "negative_momentum_domains"),
            "positive_momentum_domains": _integer(row, "positive_momentum_domains"),
            "coincident_domains": list(COINCIDENT_DOMAINS),
        },
        "concentration": round(_number(row, "concentration"), 9),
        "confirmation_state": {
            "filtered_winner": str(row.get("filtered_winner", "")) or None,
            "pending_weeks": _integer(row, "confirmation_pending"),
            "required_weeks": config.confirmation_weeks,
            "transition": transition,
        },
        "transition_watch": watch,
        "recession_alert": {
            "level": str(row.get("recession_alert", "none")),
            "character": str(row.get("recession_alert_character", "absent")),
            "role": "secondary_validation_signal",
        },
        "change_from_previous_week": change,
        "source_vintage_dates": source_dates,
        "latest_observation_dates": observation_dates,
        "source_vintage_status": "strict_alfred_point_in_time",
        "future_information_violation_count": _integer(row, "future_observations"),
        "known_limitations": limitations,
    }
    if status == "withheld":
        payload["official_current_phase"] = None
    return payload


def _human_report(payload: Mapping[str, Any]) -> str:
    phase = payload["official_current_phase"] or "withheld"
    ranking = cast(list[Mapping[str, Any]], payload["phase_ranking"])
    runner_up = ranking[1]
    support = cast(Mapping[str, Any], payload["domain_support"])["domains"]
    opposition = cast(Mapping[str, Any], payload["domain_opposition"])["domains"]
    mixed = payload["mixed_domains"]
    stale = cast(Iterable[str], payload["stale_domains"])
    stale_text = ", ".join(stale) if payload["stale_domains"] else "없음"
    freshness = cast(Mapping[str, float], payload["domain_freshness"])
    freshness_text = ", ".join(f"{name} {weeks:g}주" for name, weeks in freshness.items())
    raw_scores = cast(Mapping[str, float], payload["raw_phase_scores"])
    filtered_scores = cast(Mapping[str, float], payload["filtered_phase_scores"])
    confirmation = cast(Mapping[str, Any], payload["confirmation_state"])
    confirmation_text = (
        f"필터 승자 {confirmation['filtered_winner']}, 대기 "
        f"{confirmation['pending_weeks']}/{confirmation['required_weeks']}주"
    )
    score_rows = [
        f"| {name} | {raw_scores[name]:.4f} | {filtered_scores[name]:.4f} |" for name in PHASES
    ]
    lines = [
        f"Official U.S. economic phase: {phase}",
        f"Evidence quality: {payload['evidence_quality']}",
        f"As of: {payload['as_of_date']}",
        f"Data status: {payload['phase_status']}",
        "",
        "## 이번 주 판정",
        "",
        f"동결 모델의 필터 점수에서 `{phase}`이 1위입니다. 2위는 "
        f"`{runner_up['phase']}`이며 점수 차이는 {payload['phase_separation']:.4f}입니다.",
        f"활동 수준은 {payload['activity_level']:.4f}, 모멘텀은 "
        f"{payload['activity_momentum']:.4f}입니다.",
        "",
        "| phase | raw score | filtered score |",
        "|---|---:|---:|",
        *score_rows,
        "",
        "## 근거",
        "",
        "- 같은 방향을 보인 도메인: "
        f"{', '.join(support) if support else '없음 또는 원자료 캐시에 미기록'}",
        f"- 반대 방향 도메인: {', '.join(opposition) if opposition else '없음'}",
        f"- 혼재 도메인: {', '.join(cast(Iterable[str], mixed)) if mixed else '없음'}",
        f"- 오래된 도메인: {stale_text}",
        f"- 도메인별 마지막 발표 경과: {freshness_text}",
        f"- 확인 상태: {confirmation_text}",
        f"- 전주 대비: {payload['change_from_previous_week']}",
        f"- 전환 감시: {payload['transition_watch']}",
        "",
        "## 알려진 한계",
        "",
        *[f"- {item}" for item in cast(Iterable[str], payload["known_limitations"])],
        "",
    ]
    return "\n".join(lines)


def _history_row(payload: Mapping[str, Any]) -> dict[str, str]:
    raw = cast(Mapping[str, float], payload["raw_phase_scores"])
    filtered = cast(Mapping[str, float], payload["filtered_phase_scores"])
    freshness = cast(Mapping[str, float], payload["domain_freshness"])
    confirmation = cast(Mapping[str, Any], payload["confirmation_state"])
    row = {
        "as_of_date": str(payload["as_of_date"]),
        "official_phase": str(payload["official_current_phase"] or ""),
        "raw_phase": str(payload["raw_current_phase"] or ""),
        "evidence_quality": str(payload["evidence_quality"]),
        **{f"raw_{phase}": str(raw[phase]) for phase in PHASES},
        **{f"filtered_{phase}": str(filtered[phase]) for phase in PHASES},
        "activity_level": str(payload["activity_level"]),
        "activity_momentum": str(payload["activity_momentum"]),
        "breadth": json.dumps(payload["breadth"], ensure_ascii=False, sort_keys=True),
        "concentration": str(payload["concentration"]),
        "freshness_summary": json.dumps(freshness, ensure_ascii=False, sort_keys=True),
        "confirmation_state": json.dumps(confirmation, ensure_ascii=False, sort_keys=True),
        "transition_watch": str(payload["transition_watch"]),
        "phase_status": str(payload["phase_status"]),
        "change_from_prior_week": str(payload["change_from_previous_week"]),
        "configuration_hash": str(payload["configuration_hash"]),
        "result_digest": _semantic_digest(payload),
    }
    return row


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    """최신 두 파일은 원자적으로 교체하고 이력은 같은 날짜를 절대 덮지 않는다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    history = output_dir / "history.csv"
    new_row = _history_row(payload)
    rows: list[dict[str, str]] = []
    history_changed = False
    if history.exists():
        with history.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        same = [row for row in rows if row.get("as_of_date") == new_row["as_of_date"]]
        if same:
            if same[0].get("result_digest") != new_row["result_digest"]:
                raise ReproducibilityError(
                    f"{new_row['as_of_date']}의 기존 결과와 재실행 결과가 다릅니다. "
                    "원래 행을 보존합니다."
                )
        else:
            rows.append(new_row)
            history_changed = True
    else:
        rows.append(new_row)
        history_changed = True

    existing = output_dir / "latest.json"
    if existing.exists():
        prior = _json(existing)
        if prior.get("as_of_date") == payload.get("as_of_date") and _semantic_digest(
            prior
        ) == _semantic_digest(payload):
            payload["generated_at"] = prior.get("generated_at", payload["generated_at"])
    json_temp = output_dir / "latest.json.tmp"
    md_temp = output_dir / "latest.md.tmp"
    json_temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    md_temp.write_text(_human_report(payload), encoding="utf-8", newline="\n")
    os.replace(json_temp, output_dir / "latest.json")
    os.replace(md_temp, output_dir / "latest.md")

    if history_changed:
        history_temp = output_dir / "history.csv.tmp"
        with history_temp.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(HISTORY_FIELDS), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(history_temp, history)


def _select_row(
    settings: Settings, requested: pd.Timestamp | None, cache_only: bool
) -> tuple[dict[str, Any], dict[str, dict[str, float]], pd.DataFrame, str]:
    path = _audited_path(settings)
    if requested is not None and requested.weekday() != 4:
        raise OperationalError("--as-of는 주간 판정일인 금요일(YYYY-MM-DD)이어야 합니다")

    refreshed = False
    if not cache_only and os.environ.get("FRED_API_KEY"):
        refresh_alfred_cache(settings)
        refreshed = True
    frames: dict[str, pd.DataFrame] | None = None
    if _raw_cache_exists(settings):
        frames = cached_frames(settings)

    if requested is None:
        desired = pd.Timestamp(_latest_friday(datetime.now().date()))
        if frames is not None and refreshed:
            target = desired
        elif frames is not None:
            target = min(desired, _raw_cache_last_week(settings))
        else:
            target = pd.Timestamp(path.index.max())
    else:
        target = requested

    if frames is not None and (refreshed or target <= _raw_cache_last_week(settings)):
        row, domains = _compute_from_frames(settings, frames, target)
        source = "alfred_raw_cache"
    elif target in path.index:
        row = cast(dict[str, Any], path.loc[target].to_dict())
        row["as_of"] = str(target.date())
        domains = {}
        source = "verified_alfred_weekly_cache"
    else:
        raise MissingCredential(
            f"{target.date()} 판정을 만들 빈티지 캐시가 없습니다. "
            "FRED_API_KEY를 환경변수로 제공하거나 --cache-only로 검증된 마지막 주를 실행하세요."
        )
    return row, domains, path, source


def run(
    as_of: str | None = None,
    cache_only: bool = False,
    output_dir: Path | None = None,
    write: bool = True,
) -> dict[str, Any]:
    settings = load_settings()
    protected = verify_protected_state(settings)
    requested = pd.Timestamp(as_of) if as_of else None
    row, domains, path, source = _select_row(settings, requested, cache_only)
    config = load_config(settings)
    payload = build_payload(row, path, config, domains)
    payload["operational_source"] = source
    payload["protected_hashes"] = protected["hashes"]
    payload["result_digest"] = _semantic_digest(payload)
    if payload["future_information_violation_count"] != 0:
        raise OperationalError("미래정보 위반이 0이 아니므로 결과를 저장하지 않습니다")
    if write:
        destination = output_dir or settings.root.parent / "outputs/us_cycle"
        write_outputs(payload, destination)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="동결된 미국 4국면 모델의 주간 운영 판정")
    parser.add_argument("--as-of", help="금요일 기준일(YYYY-MM-DD)")
    parser.add_argument("--cache-only", action="store_true", help="네트워크를 사용하지 않음")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        payload = run(arguments.as_of, arguments.cache_only)
    except (OperationalError, MissingCredential, FileNotFoundError, ValueError) as error:
        print(f"미국 경기국면 실행 실패: {error}")
        return 1
    phase = payload["official_current_phase"] or "withheld"
    print(f"Official U.S. economic phase: {phase}")
    print(f"Evidence quality: {payload['evidence_quality']}")
    print(f"As of: {payload['as_of_date']} · Data status: {payload['phase_status']}")
    print(f"Transition watch: {payload['transition_watch']}")
    print("Saved: outputs/us_cycle/latest.json, latest.md, history.csv")
    return 0
