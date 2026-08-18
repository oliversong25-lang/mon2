"""ALFRED 주간 백테스트 러너: 중단돼도 이어서 돌릴 수 있게 만든다.

688주를 한 번에 돌리고 마지막에야 파일을 쓰면, 컴퓨터가 꺼지는 순간 몇 시간이 통째로
사라진다. 실제로 그렇게 잃었다. 그래서 이 러너는 세 가지를 지킨다.

1. 한 주를 끝낼 때마다 체크포인트에 **덧붙여** 쓰고 즉시 flush한다.
2. 다시 시작하면 체크포인트에 있는 주는 건너뛰고 그 다음부터 이어간다.
3. 최종 파일은 임시 파일에 다 쓴 뒤 rename으로 원자적으로 만든다. 반쯤 쓰인 결과가
   완성본처럼 보이는 일이 없다.

모델과 동결 설정은 건드리지 않는다. 여기 있는 것은 관측·재개 기능뿐이다.
"""

# ruff: noqa: E501

from __future__ import annotations

import csv
import os
import time
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, load_baseline
from ..data.alfred import AlfredCollector, observations_as_of
from ..pipeline import run_pipeline
from .phase7 import FROZEN_CANDIDATE

#: 진행률을 몇 주마다 찍을지.
PROGRESS_EVERY = 10

CHECKPOINT_NAME = "realtime_path.checkpoint.csv"
FINAL_NAME = "realtime_path.csv"
ERROR_LOG_NAME = "runner_errors.log"


@dataclass(frozen=True)
class RunnerState:
    completed: int
    remaining: int
    checkpoint: Path


def _completed_weeks(checkpoint: Path) -> set[str]:
    """이미 끝난 주를 읽는다. 파일이 깨져 있어도 읽을 수 있는 데까지 살린다."""

    if not checkpoint.exists():
        return set()
    done: set[str] = set()
    with checkpoint.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            value = row.get("as_of")
            if value:
                done.add(str(value))
    return done


def _week_row(
    settings: Settings,
    variant: Settings,
    frames: dict[str, pd.DataFrame],
    vintage: pd.Timestamp,
) -> dict[str, Any]:
    """한 주의 결과. 빈티지 슬라이스를 한 번만 만들어 재사용한다."""

    indicator_settings = settings.indicators["indicators"]
    domain_of = {str(key): str(value["domain"]) for key, value in indicator_settings.items()}
    observations = observations_as_of(frames, vintage, indicator_settings)
    withheld_rows = int((observations["release_date"] > vintage).sum())
    # 계열별 최신 관측일은 방금 만든 관측 표에서 바로 얻는다. 슬라이스를 다시 하지 않는다.
    newest = {
        f"newest_{series_id}": str(group["observation_period"].max().date())
        for series_id, group in observations.groupby("indicator_id")
    }

    run = run_pipeline(observations, variant, vintage)
    result = run.result
    latest = run.history.index[-1]
    contributions = run.contributions.reindex([latest], method="ffill").iloc[0]
    domain_totals: dict[str, float] = {}
    for indicator, value in contributions.items():
        domain = domain_of.get(str(indicator))
        if domain is not None and pd.notna(value):
            domain_totals[domain] = domain_totals.get(domain, 0.0) + float(value)
    probabilities = {row["code"]: float(row["probability"]) for row in result.phase_probabilities}
    return {
        "as_of": str(vintage.date()),
        "status": result.status,
        "status_reason": str(result.metadata.get("status_reason", "")),
        "broad_phase": result.current_phase["broad_phase"],
        "detail_phase": result.current_phase["code"],
        "contraction_probability": sum(
            value for code, value in probabilities.items() if code.startswith("contraction_")
        ),
        "slowdown_probability": sum(
            value for code, value in probabilities.items() if code.startswith("slowdown_")
        ),
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
        **newest,
    }


def run_weekly_backtest(
    settings: Settings,
    output_dir: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    progress_every: int = PROGRESS_EVERY,
) -> RunnerState:
    """주간 as-of 경로를 만든다. 이미 끝난 주는 건너뛴다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / CHECKPOINT_NAME
    error_log = output_dir / ERROR_LOG_NAME
    weeks = pd.date_range(start, end, freq="W-FRI")
    done = _completed_weeks(checkpoint)
    pending = [week for week in weeks if str(week.date()) not in done]

    print(
        f"[phase7] 전체 {len(weeks)}주 · 완료 {len(done)}주 · 남은 {len(pending)}주",
        flush=True,
    )
    if not pending:
        _finalise(checkpoint, output_dir / FINAL_NAME, weeks)
        return RunnerState(len(weeks), 0, checkpoint)

    collector = AlfredCollector(settings.root / "data" / "cache" / "alfred")
    indicator_ids = list(settings.indicators["indicators"])
    frames = {series_id: collector.realtime_observations(series_id) for series_id in indicator_ids}
    variant = load_baseline(FROZEN_CANDIDATE, settings)

    started = time.monotonic()
    handle = checkpoint.open("a", encoding="utf-8", newline="")
    writer: csv.DictWriter[str] | None = None
    try:
        for index, vintage in enumerate(pending, start=1):
            try:
                row = _week_row(settings, variant, frames, vintage)
            except Exception:
                with error_log.open("a", encoding="utf-8") as log:
                    log.write(
                        f"--- {datetime.now(UTC).isoformat(timespec='seconds')} "
                        f"as_of={vintage.date()}\n{traceback.format_exc()}\n"
                    )
                row = {"as_of": str(vintage.date()), "status": "error"}
            if writer is None:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                if not done and checkpoint.stat().st_size == 0:
                    writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in writer.fieldnames})
            handle.flush()
            os.fsync(handle.fileno())
            if index % progress_every == 0 or index == len(pending):
                elapsed = time.monotonic() - started
                rate = elapsed / index
                remaining = rate * (len(pending) - index)
                print(
                    f"[phase7] {index}/{len(pending)}주 · {vintage.date()} · "
                    f"경과 {elapsed / 60:.1f}분 · 주당 {rate:.1f}초 · "
                    f"남은 예상 {remaining / 60:.1f}분",
                    flush=True,
                )
    finally:
        handle.close()

    _finalise(checkpoint, output_dir / FINAL_NAME, weeks)
    return RunnerState(len(weeks), 0, checkpoint)


def _finalise(checkpoint: Path, final: Path, weeks: pd.DatetimeIndex) -> None:
    """체크포인트를 정렬·중복 제거해 원자적으로 최종 파일로 만든다."""

    frame = pd.read_csv(checkpoint)
    frame = frame.drop_duplicates(subset=["as_of"], keep="last")
    order = {str(week.date()): index for index, week in enumerate(weeks)}
    frame["_order"] = frame["as_of"].map(order)
    frame = frame.dropna(subset=["_order"]).sort_values("_order").drop(columns=["_order"])
    temporary = final.with_suffix(".tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(final)
    print(f"[phase7] 최종 파일 {final.name} · {len(frame)}행", flush=True)
