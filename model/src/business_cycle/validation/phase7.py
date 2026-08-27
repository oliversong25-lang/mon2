"""단계 B: ALFRED point-in-time 검증.

증거 층을 섞지 않는다.

- **A층** 최신 수정치 개발 결과(1995~2026). 이미 있는 결과이며 여기서 다시 만들지 않는다.
- **B층** 7개 지표가 모두 진짜 빈티지를 갖는 구간의 엄격한 실시간 검증(2013-06-14~).
- **C층** 그 이전 구간의 부분 지표 분석. 보조 자료일 뿐이며 동결 모델의 성능이 아니다.

B층에서는 매 주 그 시점에 실제로 공개돼 있던 판본만 쓴다. 나중 빈티지, 최신값 대체,
후방 채움, 그 시점에 없던 수정본은 쓰지 않는다. 동결한 설정과 임계값은 그대로 두며
ALFRED 결과로 무엇도 조정하지 않는다.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, load_baseline
from ..data.alfred import (
    AlfredCollector,
    common_vintage_start,
    observations_as_of,
    provenance_table,
    slice_vintage,
)
from ..pipeline import run_pipeline

#: 동결한 운영 후보. ALFRED 결과로 바꾸지 않는다.
FROZEN_CANDIDATE = "candidate_h_breadth_gate"

#: 7개 지표가 모두 진짜 빈티지를 갖는 첫날. 실측으로 정해지며 손으로 적지 않는다.
STRICT_WINDOW_NOTE = "모든 핵심지표의 fred/series/vintagedates 첫 값 중 가장 늦은 날"

#: 이 창에 들어 있는 NBER 침체. 하나뿐이라는 사실이 결론의 한계를 정한다.
NBER_2020 = (pd.Timestamp("2020-03-06"), pd.Timestamp("2020-04-24"))


@dataclass(frozen=True)
class Phase7Result:
    output_dir: Path
    strict_start: pd.Timestamp
    strict_end: pd.Timestamp
    weeks: int
    frozen_hash_matches: bool


def verify_frozen_configuration(settings: Settings, frozen_dir: Path) -> tuple[bool, str, str]:
    """동결 스냅샷이 지금 설정과 같은지 확인한다.

    ALFRED를 돌리기 전에 확인해야 한다. 동결한 것과 다른 설정으로 실시간 검증을 하면
    검증 대상이 무엇인지 알 수 없게 된다.
    """

    import yaml

    path = frozen_dir / "frozen_model_config.yaml"
    recorded = (frozen_dir / "frozen_model_config.sha256").read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    current = load_baseline(FROZEN_CANDIDATE, settings)
    same = bool(stored["model"] == current.model and stored["indicators"] == current.indicators)
    file_hash = hashlib.sha256(
        yaml.safe_dump(
            {
                "baseline_name": stored["baseline_name"],
                "source_commit": stored["source_commit"],
                "model_version": stored["model_version"],
                "indicators": stored["indicators"],
                "model": stored["model"],
                "transitions": stored["transitions"],
            },
            allow_unicode=True,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return bool(same and file_hash == recorded), recorded, actual


def strict_window(
    collector: AlfredCollector, indicator_ids: list[str]
) -> tuple[pd.Timestamp, list[Any]]:
    """모든 지표가 진짜 빈티지를 갖는 시작일을 vintagedates 응답에서 정한다."""

    coverages = [collector.coverage(series_id) for series_id in indicator_ids]
    return common_vintage_start(coverages), coverages


def _newest_observation(frames: dict[str, pd.DataFrame], vintage: pd.Timestamp) -> dict[str, Any]:
    newest: dict[str, Any] = {}
    for series_id, frame in frames.items():
        visible = slice_vintage(frame, vintage)
        newest[f"newest_{series_id}"] = (
            str(visible["date"].max().date()) if not visible.empty else ""
        )
    return newest


def realtime_path(
    settings: Settings,
    frames: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """주간 as-of 날짜마다 그 시점 판본만으로 판정을 다시 만든다."""

    variant = load_baseline(FROZEN_CANDIDATE, settings)
    indicator_settings = settings.indicators["indicators"]
    domain_of = {str(key): str(value["domain"]) for key, value in indicator_settings.items()}
    rows: list[dict[str, Any]] = []
    for vintage in pd.date_range(start, end, freq="W-FRI"):
        observations = observations_as_of(frames, vintage, indicator_settings)
        # 추정 발표일이 as-of를 넘는 관측은 파이프라인이 거른다. 몇 건이 걸리는지 남긴다.
        withheld_rows = int((observations["release_date"] > vintage).sum())
        try:
            run = run_pipeline(observations, variant, vintage)
        except ValueError as error:
            rows.append(
                {
                    "as_of": str(vintage.date()),
                    "status": "unavailable",
                    "status_reason": str(error),
                    "observations_after_as_of": withheld_rows,
                }
            )
            continue
        result = run.result
        history = run.history
        latest = history.index[-1]
        contributions = run.contributions.reindex([latest], method="ffill").iloc[0]
        domain_totals: dict[str, float] = {}
        for indicator, value in contributions.items():
            domain = domain_of.get(str(indicator))
            if domain is not None and pd.notna(value):
                domain_totals[domain] = domain_totals.get(domain, 0.0) + float(value)
        probabilities = {
            row["code"]: float(row["probability"]) for row in result.phase_probabilities
        }
        contraction = sum(
            value for code, value in probabilities.items() if code.startswith("contraction_")
        )
        slowdown = sum(
            value for code, value in probabilities.items() if code.startswith("slowdown_")
        )
        rows.append(
            {
                "as_of": str(vintage.date()),
                "status": result.status,
                "status_reason": str(result.metadata.get("status_reason", "")),
                "broad_phase": result.current_phase["broad_phase"],
                "detail_phase": result.current_phase["code"],
                "contraction_probability": contraction,
                "slowdown_probability": slowdown,
                "top_probability": probabilities[result.current_phase["code"]],
                "runner_up": result.runner_up["code"],
                "runner_up_probability": float(result.runner_up["probability"]),
                "gap_percentage_points": float(result.runner_up["gap_percentage_points"]),
                "x": float(result.coordinates["x_momentum"]),
                "y": float(result.coordinates["y_level"]),
                "radius": float(result.coordinates["radius"]),
                "angle": float(result.coordinates["angle_degrees"]),
                "negative_domains": int(sum(1 for value in domain_totals.values() if value < 0)),
                "breadth_minimum": result.metadata.get("contraction_breadth_minimum"),
                "indicators_present": int(observations["indicator_id"].nunique()),
                "observation_rows": int(len(observations)),
                "observations_after_as_of": withheld_rows,
                "warmup_years": float(result.metadata["warmup_years"]),
                "coordinate_history_years": float(result.metadata["coordinate_history_years"]),
                "broad_confidence": float(result.confidence["broad"]),
                "data_confidence": float(result.confidence["data"]),
                **_newest_observation(frames, vintage),
            }
        )
    return pd.DataFrame(rows)


def compare_with_latest_vintage(
    realtime: pd.DataFrame, latest_history: pd.DataFrame
) -> pd.DataFrame:
    """같은 주에 대해 그때의 판정과 지금 자료의 판정을 나란히 둔다.

    최신 수정치 이력은 A층 결과다. 여기서 다시 만들지 않고 그대로 가져와 비교만 한다.
    """

    weeks = pd.DatetimeIndex(pd.to_datetime(realtime["as_of"]))
    aligned = latest_history.reindex(weeks)
    frame = pd.DataFrame(
        {
            "as_of": realtime["as_of"].to_numpy(),
            "realtime_broad": realtime["broad_phase"].to_numpy(),
            "realtime_detail": realtime["detail_phase"].to_numpy(),
            "realtime_y": realtime["y"].to_numpy(),
            "latest_broad": aligned["broad_phase"].to_numpy(),
            "latest_detail": aligned["phase_code"].to_numpy(),
            "latest_y": aligned["y"].to_numpy(),
        }
    )
    frame["broad_agrees"] = frame["realtime_broad"] == frame["latest_broad"]
    frame["detail_agrees"] = frame["realtime_detail"] == frame["latest_detail"]
    frame["y_difference"] = frame["realtime_y"] - frame["latest_y"]
    return frame


def detection_summary(realtime: pd.DataFrame) -> dict[str, Any]:
    """2020년 탐지 시점과 침체 전후 오탐을 실시간 경로에서 직접 센다."""

    weeks = pd.DatetimeIndex(pd.to_datetime(realtime["as_of"]))
    contraction = pd.Series(realtime["broad_phase"].to_numpy() == "contraction", index=weeks)
    confirmed = contraction.rolling(4).sum().eq(4)
    start, end = NBER_2020
    before = contraction & (weeks < start)
    after = contraction & (weeks > end)
    during = contraction & (weeks >= start) & (weeks <= end)
    first = contraction[contraction].index.min() if contraction.any() else pd.NaT
    decision = confirmed[confirmed].index.min() if confirmed.any() else pd.NaT
    return {
        "first_contraction_signal_date": str(first.date()) if pd.notna(first) else "",
        "confirmation_decision_date": str(decision.date()) if pd.notna(decision) else "",
        "nber_reference_week": str(start.date()),
        "entry_lead_lag_from_first_signal": (
            round((first - start).days / 7.0, 1) if pd.notna(first) else float("nan")
        ),
        "entry_lead_lag_from_confirmation_decision": (
            round((decision - start).days / 7.0, 1) if pd.notna(decision) else float("nan")
        ),
        "pre_recession_false_positive_weeks": int(before.sum()),
        "post_recession_false_positive_weeks": int(after.sum()),
        "within_recession_contraction_weeks": int(during.sum()),
        "nber_weeks_in_window": int(((weeks >= start) & (weeks <= end)).sum()),
        "recession_episodes_in_window": 1,
    }


def run_phase7(settings: Settings, output_dir: Path, frozen_dir: Path) -> Phase7Result:
    output_dir.mkdir(parents=True, exist_ok=True)
    indicator_ids = list(settings.indicators["indicators"])
    collector = AlfredCollector(settings.root / "data" / "cache" / "alfred")
    retrieved_at = datetime.now(UTC).isoformat(timespec="seconds")

    matches, recorded, _ = verify_frozen_configuration(settings, frozen_dir)
    start, coverages = strict_window(collector, indicator_ids)
    frames = {series_id: collector.realtime_observations(series_id) for series_id in indicator_ids}
    provenance = provenance_table(coverages, frames, start, retrieved_at)
    provenance.to_csv(output_dir / "vintage_provenance.csv", index=False)

    end = min(pd.Timestamp(coverage.last_vintage) for coverage in coverages)
    realtime = realtime_path(settings, frames, start, end)
    realtime.to_csv(output_dir / "realtime_path.csv", index=False)

    summary = {
        "evidence_layers": {
            "A_latest_vintage_development": "1995-01-01..2026-08-14 (기존 결과, 재생성하지 않음)",
            "B_strict_alfred_validation": f"{start.date()}..{end.date()}",
            "C_earlier_partial_indicators": "보조 자료. 동결 모델의 성능이 아니다.",
        },
        "strict_window_start": str(start.date()),
        "strict_window_start_rule": STRICT_WINDOW_NOTE,
        "strict_window_end": str(end.date()),
        "strict_weeks": int(len(realtime)),
        "frozen_candidate": FROZEN_CANDIDATE,
        "frozen_hash": recorded,
        "frozen_hash_matches_current_config": matches,
        "thresholds_changed_for_alfred": False,
        "detection": detection_summary(realtime),
        "statistical_limitation": (
            "엄격 창에는 NBER 침체가 2020년 하나뿐이다. 침체 한 건으로는 실시간 침체 "
            "탐지 성능을 일반화할 수 없다. 재현율·오탐률을 여러 순환에 걸쳐 검증했다고 "
            "말할 수 없으며, 2001년과 금융위기는 이 자료로 실시간 검증이 불가능하다."
        ),
        "retrieved_at": retrieved_at,
    }
    (output_dir / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return Phase7Result(output_dir, start, end, len(realtime), matches)
