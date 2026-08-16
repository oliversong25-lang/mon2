"""실자료 검증용 재현 가능한 PNG 차트."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

BROAD_ORDER = ["recovery", "expansion", "slowdown", "contraction"]


def _shade_recessions(axis: Any, flags: pd.Series) -> None:
    active_start: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    for timestamp, active in flags.items():
        current = pd.Timestamp(str(timestamp))
        if bool(active) and active_start is None:
            active_start = current
        if not bool(active) and active_start is not None:
            axis.axvspan(active_start, previous or current, color="#d9dde5", alpha=0.65)
            active_start = None
        previous = current
    if active_start is not None and previous is not None:
        axis.axvspan(active_start, previous, color="#d9dde5", alpha=0.65)


def _save(figure: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return path


def _case_chart(
    history: pd.DataFrame,
    recession: pd.Series,
    start: str,
    end: str,
    title: str,
    path: Path,
) -> Path:
    subset = history.loc[pd.Timestamp(start) : pd.Timestamp(end)]
    flags = recession.reindex(subset.index, fill_value=False)
    figure, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    axes[0].plot(subset.index, subset["x"], label="X momentum", color="#2463eb")
    axes[0].plot(subset.index, subset["y"], label="Y level", color="#e45c33")
    axes[0].axhline(0, color="#777", linewidth=0.8)
    axes[0].legend(loc="upper left")
    broad = subset["broad_phase"].map({key: i for i, key in enumerate(BROAD_ORDER)})
    axes[1].step(subset.index, broad, where="post", color="#2a7f62")
    axes[1].set_yticks(range(4), BROAD_ORDER)
    for axis in axes:
        _shade_recessions(axis, flags)
        axis.grid(alpha=0.2)
    axes[0].set_title(title)
    return _save(figure, path)


def create_validation_charts(
    history: pd.DataFrame,
    dynamic_history: pd.DataFrame,
    recession: pd.Series,
    composite: pd.Series,
    dynamic: pd.Series,
    output_dir: Path,
) -> list[Path]:
    """명세의 전체기간 6개와 역사 사례 4개 차트를 생성한다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    figure, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    axes[0].plot(history.index, history["x"], color="#2463eb", linewidth=0.9)
    axes[1].plot(history.index, history["y"], color="#e45c33", linewidth=0.9)
    axes[0].set_ylabel("X momentum")
    axes[1].set_ylabel("Y level")
    for axis in axes:
        axis.axhline(0, color="#777", linewidth=0.8)
        _shade_recessions(axis, recession)
        axis.grid(alpha=0.2)
    axes[0].set_title("US business-cycle coordinates since 1995")
    paths.append(_save(figure, output_dir / "01_coordinates.png"))

    figure, axis = plt.subplots(figsize=(13, 4))
    broad = history["broad_phase"].map({key: i for i, key in enumerate(BROAD_ORDER)})
    axis.step(history.index, broad, where="post", color="#2a7f62", linewidth=1.0)
    axis.set_yticks(range(4), BROAD_ORDER)
    _shade_recessions(axis, recession)
    axis.grid(alpha=0.2)
    axis.set_title("Representative broad phase and official NBER recession shading")
    paths.append(_save(figure, output_dir / "02_broad_phase.png"))

    figure, axis = plt.subplots(figsize=(13, 4))
    phase_codes = list(dict.fromkeys(history["phase_code"].astype(str)))
    configured = [column[2:] for column in history.columns if str(column).startswith("p_")]
    order = configured or phase_codes
    detail = history["phase_code"].map({key: i for i, key in enumerate(order)})
    axis.step(history.index, detail, where="post", color="#6c4ccf", linewidth=0.8)
    axis.set_yticks(range(len(order)), order, fontsize=7)
    _shade_recessions(axis, recession)
    axis.grid(alpha=0.2)
    axis.set_title("Twelve detailed phases")
    paths.append(_save(figure, output_dir / "03_detail_phase.png"))

    probability_columns = [column for column in history if str(column).startswith("p_")]
    ordered = np.sort(history[probability_columns].to_numpy(dtype=float), axis=1)
    figure, axis = plt.subplots(figsize=(13, 4))
    axis.plot(history.index, ordered[:, -1], label="top 1", linewidth=0.9)
    axis.plot(history.index, ordered[:, -2], label="top 2", linewidth=0.8)
    axis.plot(history.index, ordered[:, -3], label="top 3", linewidth=0.7)
    _shade_recessions(axis, recession)
    axis.legend()
    axis.grid(alpha=0.2)
    axis.set_title("Top detailed-phase probabilities")
    paths.append(_save(figure, output_dir / "04_top_probabilities.png"))

    figure, axis = plt.subplots(figsize=(13, 4))
    for column, label in (
        ("broad_confidence", "broad"),
        ("detail_confidence", "detail"),
        ("data_confidence", "data"),
    ):
        axis.plot(history.index, history[column], label=label, linewidth=0.8)
    _shade_recessions(axis, recession)
    axis.set_ylim(0, 100)
    axis.legend()
    axis.grid(alpha=0.2)
    axis.set_title("Broad, detail and data confidence")
    paths.append(_save(figure, output_dir / "05_confidence.png"))

    cases = [
        ("2000-01-01", "2002-12-31", "2001 recession", "06_case_2001.png"),
        ("2006-01-01", "2010-12-31", "Global financial crisis", "07_case_gfc.png"),
        ("2019-01-01", "2021-06-30", "2020 pandemic shock", "08_case_2020.png"),
        ("2022-01-01", str(history.index.max().date()), "2022 onward", "09_case_2022.png"),
    ]
    for start, end, title, name in cases:
        paths.append(_case_chart(history, recession, start, end, title, output_dir / name))

    common = pd.concat([composite.rename("composite"), dynamic.rename("dynamic")], axis=1).reindex(
        history.index
    )
    common = (common - common.expanding(min_periods=26).mean()) / common.expanding(
        min_periods=26
    ).std(ddof=0)
    figure, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True)
    axes[0].plot(common.index, common["composite"], label="Composite", linewidth=0.9)
    axes[0].plot(common.index, common["dynamic"], label="Dynamic", linewidth=0.8, alpha=0.8)
    axes[0].legend()
    representative = history["broad_phase"].map({key: i for i, key in enumerate(BROAD_ORDER)})
    comparison = (
        dynamic_history["broad_phase"]
        .map({key: i for i, key in enumerate(BROAD_ORDER)})
        .reindex(history.index)
    )
    axes[1].step(history.index, representative, where="post", label="Composite phase")
    axes[1].step(history.index, comparison, where="post", label="Dynamic phase", alpha=0.75)
    axes[1].set_yticks(range(4), BROAD_ORDER)
    axes[1].legend()
    for axis in axes:
        _shade_recessions(axis, recession)
        axis.grid(alpha=0.2)
    axes[0].set_title("Composite representative vs Dynamic comparison")
    paths.append(_save(figure, output_dir / "10_model_comparison.png"))
    return paths
