"""2차 보정 감사용 차트 12종."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402


def _save(figure: Any, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.tight_layout()
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return path


def _shade(axis: Any, actual: pd.Series) -> None:
    active: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    for timestamp, value in actual.items():
        current = pd.Timestamp(str(timestamp))
        if bool(value) and active is None:
            active = current
        if not bool(value) and active is not None:
            axis.axvspan(active, previous or current, color="#ccd2dc", alpha=0.6)
            active = None
        previous = current
    if active is not None and previous is not None:
        axis.axvspan(active, previous, color="#ccd2dc", alpha=0.6)


def _case_chart(
    history: pd.DataFrame,
    actual: pd.Series,
    start: str,
    end: str,
    title: str,
    path: Path,
) -> Path:
    subset = history.loc[start:end]
    flags = actual.reindex(subset.index, fill_value=False)
    figure, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    axes[0].plot(subset.index, subset["x"], label="X momentum")
    axes[0].plot(subset.index, subset["y"], label="Y level")
    axes[0].axhline(0, color="#777", linewidth=0.8)
    axes[0].legend()
    axes[1].step(
        subset.index,
        subset["broad_phase"].map({"recovery": 0, "expansion": 1, "slowdown": 2, "contraction": 3}),
        where="post",
    )
    axes[1].set_yticks(range(4), ["recovery", "expansion", "slowdown", "contraction"])
    for axis in axes:
        _shade(axis, flags)
        axis.grid(alpha=0.2)
    axes[0].set_title(title)
    return _save(figure, path)


def create_phase2_charts(
    histories: dict[str, pd.DataFrame],
    metrics: pd.DataFrame,
    actual: pd.Series,
    final_name: str,
    contributions: pd.DataFrame,
    composite: pd.Series,
    dynamic: pd.Series,
    output_dir: Path,
) -> list[Path]:
    final = histories[final_name]
    paths: list[Path] = []

    figure, axis = plt.subplots(figsize=(13, 4))
    axis.fill_between(actual.index, 0, actual.astype(int), step="post", alpha=0.35, label="USREC")
    for name, history in histories.items():
        axis.step(
            history.index,
            history["broad_phase"].eq("contraction").astype(int),
            where="post",
            label=name,
            linewidth=0.8,
        )
    axis.set_ylim(-0.05, 1.1)
    axis.legend(ncol=3)
    axis.set_title("Official USREC and model recession calls")
    paths.append(_save(figure, output_dir / "01_usrec_vs_models.png"))

    false_positive = final["broad_phase"].eq("contraction") & ~actual
    figure, axis = plt.subplots(figsize=(13, 4))
    axis.fill_between(final.index, 0, false_positive.astype(int), step="post", color="#d9534f")
    axis.set_title("Final model false-positive recession episodes")
    axis.set_ylim(-0.05, 1.1)
    paths.append(_save(figure, output_dir / "02_false_positive_episodes.png"))

    for start, end, title, filename in (
        ("2000-01-01", "2002-12-31", "2001 recession", "03_case_2001.png"),
        ("2006-01-01", "2010-12-31", "Global financial crisis", "04_case_gfc.png"),
        ("2019-01-01", "2021-06-30", "2020 pandemic", "05_case_2020.png"),
        ("2021-01-01", str(final.index.max().date()), "2021 onward", "06_2021_onward.png"),
    ):
        paths.append(_case_chart(final, actual, start, end, title, output_dir / filename))

    recent = final.loc["2022-01-01":]
    figure, axis = plt.subplots(figsize=(12, 4))
    axis.plot(recent.index, recent["x"], label="X momentum")
    axis.plot(recent.index, recent["y"], label="Y level")
    axis.axhline(0, color="#777", linewidth=0.8)
    axis.legend()
    axis.grid(alpha=0.2)
    axis.set_title("2022 onward X/Y coordinates")
    paths.append(_save(figure, output_dir / "07_2022_xy.png"))

    recent_contributions = contributions.reindex(recent.index, method="ffill")
    figure, axis = plt.subplots(figsize=(12, 5))
    for column in recent_contributions:
        axis.plot(
            recent_contributions.index,
            recent_contributions[column],
            label=str(column),
            linewidth=0.8,
        )
    axis.axhline(0, color="#777", linewidth=0.8)
    axis.legend(ncol=4, fontsize=8)
    axis.grid(alpha=0.2)
    axis.set_title("2022 onward indicator contributions")
    paths.append(_save(figure, output_dir / "08_2022_contributions.png"))

    probability_columns = [column for column in recent if str(column).startswith("p_")]
    contraction_probability = recent[
        [column for column in probability_columns if "contraction_" in str(column)]
    ].sum(axis=1)
    figure, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(recent.index, recent["angle"])
    axes[0].set_ylabel("angle")
    axes[1].plot(recent.index, recent["radius"])
    axes[1].set_ylabel("radius")
    axes[2].plot(recent.index, contraction_probability, label="contraction probability")
    axes[2].legend()
    for axis in axes:
        axis.grid(alpha=0.2)
    axes[0].set_title("2022 angle, radius and contraction probability")
    paths.append(_save(figure, output_dir / "09_angle_radius_probability.png"))

    figure, axes = plt.subplots(2, 2, figsize=(11, 7))
    for axis, column, title in (
        (axes[0, 0], "recession_false_positive_rate", "False positive rate"),
        (axes[0, 1], "recession_precision", "Precision"),
        (axes[1, 0], "multi_step_jumps", "Multi-step jumps"),
        (axes[1, 1], "three_week_whipsaws", "Three-week whipsaws"),
    ):
        axis.bar(metrics["model"], metrics[column])
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=20)
    paths.append(_save(figure, output_dir / "10_candidate_comparison.png"))

    figure, axis = plt.subplots(figsize=(13, 4))
    axis.plot(final.index, final["broad_confidence"], label="broad")
    axis.plot(final.index, final["detail_confidence"], label="detail")
    axis.plot(final.index, final["data_confidence"], label="data")
    axis.fill_between(final.index, 0, false_positive.astype(int) * 100, color="#d9534f", alpha=0.15)
    axis.set_ylim(0, 100)
    axis.legend()
    axis.set_title("Confidence scores and false positives")
    paths.append(_save(figure, output_dir / "11_confidence_false_positive.png"))

    common = pd.concat([composite.rename("Composite"), dynamic.rename("Dynamic")], axis=1).reindex(
        final.index
    )
    common = (common - common.expanding(min_periods=26).mean()) / common.expanding(
        min_periods=26
    ).std(ddof=0)
    figure, axis = plt.subplots(figsize=(13, 4))
    axis.plot(common.index, common["Composite"], label="Composite")
    axis.plot(common.index, common["Dynamic"], label="Dynamic", alpha=0.8)
    _shade(axis, actual)
    axis.legend()
    axis.grid(alpha=0.2)
    axis.set_title("Composite representative vs Dynamic comparison")
    paths.append(_save(figure, output_dir / "12_composite_dynamic.png"))
    return paths
