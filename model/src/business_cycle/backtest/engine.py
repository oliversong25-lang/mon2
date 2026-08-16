"""인과적 one-pass walk-forward 백테스트 엔진."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..config import Settings
from ..pipeline import PipelineRun, run_pipeline
from .metrics import backtest_metrics


@dataclass
class BacktestResult:
    run: PipelineRun
    history: pd.DataFrame
    metrics: dict[str, Any]
    metadata: dict[str, Any]


def run_backtest(
    observations: pd.DataFrame,
    settings: Settings,
    start: str,
    end: str,
    walk_forward: bool = True,
) -> BacktestResult:
    """expanding 표준화와 forward filter로 시간순 결과를 만든다.

    매 주 파이프라인을 처음부터 재호출하지 않고 한 번의 순방향 재귀로 같은 결과를
    계산한다. 각 행은 그 행까지의 통계량과 관측만 사용하므로 수학적으로 walk-forward와
    동일하며 backward smoothing이나 전체기간 파라미터 적합은 수행하지 않는다.
    """

    if not walk_forward:
        raise ValueError("v0.1 백테스트는 look-ahead 방지를 위해 walk_forward만 허용합니다")
    run = run_pipeline(observations, settings, end)
    history = run.history.loc[pd.Timestamp(start) : pd.Timestamp(end)].copy()
    minimum = int(settings.model["minimum_training_weeks"])
    if len(history) < minimum:
        raise ValueError(f"백테스트 구간이 최소 {minimum}주보다 짧습니다")
    order = [str(phase["code"]) for phase in settings.transitions["phases"]]
    metrics = backtest_metrics(history, order)
    return BacktestResult(
        run=run,
        history=history,
        metrics=metrics,
        metadata={
            "walk_forward": True,
            "random_seed": int(settings.model["random_seed"]),
            "revision_basis": "latest_revision_preliminary",
            "common_start": history.index.min().date().isoformat(),
            "common_end": history.index.max().date().isoformat(),
            "model_comparison": {
                "composite_available_weeks": int(run.composite.loc[history.index].notna().sum()),
                "dynamic_available_weeks": int(run.dynamic.loc[history.index].notna().sum()),
                "correlation": float(
                    run.composite.loc[history.index].corr(run.dynamic.loc[history.index])
                ),
            },
            "settings": {
                "indicators": settings.indicators,
                "model": settings.model,
                "transitions": settings.transitions,
            },
        },
    )
