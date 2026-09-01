"""단계 A-3: 좌표 표준화 층을 바로잡고 시작시점 강건성을 다시 판정한다.

단계 A-2는 좌표 층에 10년 창을 얹었고, 그 결과 지표 성숙 5~10년 위에 좌표 성숙
10년이 겹쳐 전체 성숙 요구가 약 15년이 됐다. 1990 시작 실행은 2001년에 아직
미성숙이므로 1985 실행과의 2001년 비교 자체가 동등 조건이 아니었다.

여기서는 좌표 창 길이를 후보로 놓고 (A) 10년, (B) 5년, (C) 3년, (D) 7년,
(E) 재표준화 없음을 비교한 뒤, 두 실행이 모두 성숙한 조건에서만 2001년을 비교한다.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..config import Settings, load_baseline, load_settings
from ..models.phase import phase_definitions
from .phase2 import ModelEvaluation, _evaluate
from .phase2_metrics import recession_prediction
from .phase3 import _row
from .phase4 import (
    END,
    START,
    _emission_frame,
    claims_variants,
    leave_one_out,
    load_core_observations,
    restrict_start,
)
from .real_data import _official_recession_flags

#: 좌표 창 후보와 비교 기준. 이름 순서가 보고서 표의 순서다.
CANDIDATES = [
    "legacy_benchmark",
    "frequency_maturity_reference",
    "corrected_baseline",
    "coordinate_a_10y",
    "coordinate_b_5y",
    "coordinate_c_3y",
    "coordinate_d_7y",
    "coordinate_e_none",
]

#: 두 실행이 모두 성숙했을 때만 비교한다. 5년 좌표 창이면 1990 시작도 2000년에 성숙한다.
CONVERGENCE_START = "2000-01-01"
WARMUP_SHIFT_LIMIT_WEEKS = 8.0

#: 후보 선택 규칙. 전체 성숙 요구가 10년을 넘으면 후보 자격이 없다.
MAX_TOTAL_MATURITY_YEARS = 10.0


@dataclass(frozen=True)
class Phase5Result:
    output_dir: Path
    report_path: Path
    summary_path: Path
    passed: bool
    selected: str
    frozen_hash: str


def total_maturity_years(settings: Settings) -> float:
    """지표 성숙과 좌표 성숙을 이어 붙인 전체 성숙 요구."""

    maturity = settings.model.get("maturity") or {}
    indicator_full = float(maturity.get("full_weight_years", 10.0))
    if str(settings.model.get("coordinate_standardization_method")) == "none":
        return indicator_full
    minimum = float(maturity.get("exclude_years", 5.0)) if maturity.get("enabled") else 0.0
    coordinate_full = float(
        settings.model.get(
            "coordinate_full_history_years",
            settings.model.get("coordinate_standardization_horizon_years", 10.0),
        )
    )
    # 합성요인은 지표가 쓰이기 시작하는 시점(exclude_years)부터 존재한다.
    return max(indicator_full, minimum + coordinate_full)


def coordinate_window_audit(evaluation: ModelEvaluation, name: str) -> pd.DataFrame:
    """좌표 창이 의도한 달력 길이였는지, 척도가 붕괴한 적이 있는지 기록한다."""

    audit = evaluation.backtest.run.coordinate_audit
    method = str(evaluation.settings.model.get("coordinate_standardization_method"))
    mature = audit.dropna(subset=["window_duration_years"])
    intended = float(
        evaluation.settings.model.get("coordinate_standardization_horizon_years", 10.0)
    )
    scale = pd.to_numeric(audit["coordinate_scale"], errors="coerce").dropna()
    reference = float(scale.median()) if len(scale) else float("nan")
    coordinates = audit["y"].dropna()
    return pd.DataFrame(
        [
            {
                "candidate": name,
                "coordinate_method": method,
                "intended_window_years": intended if method != "none" else np.nan,
                "median_window_duration_years": (
                    float(mature["window_duration_years"].median()) if len(mature) else np.nan
                ),
                "max_window_duration_years": (
                    float(mature["window_duration_years"].max()) if len(mature) else np.nan
                ),
                "median_window_observations": float(
                    pd.to_numeric(audit["window_observation_count"], errors="coerce").median()
                ),
                "first_coordinate_week": (
                    str(coordinates.index.min().date()) if len(coordinates) else ""
                ),
                "coordinate_weeks": int(len(coordinates)),
                "median_scale": reference,
                "minimum_scale": float(scale.min()) if len(scale) else np.nan,
                # 척도 붕괴 진단. 임의의 하한을 두지 않고 얼마나 작아졌는지만 기록한다.
                "min_scale_ratio_to_median": (
                    float(scale.min() / reference) if len(scale) and reference > 0 else np.nan
                ),
                "weeks_scale_below_half_median": (
                    int((scale < 0.5 * reference).sum()) if reference > 0 else 0
                ),
                "weeks_scale_below_quarter_median": (
                    int((scale < 0.25 * reference).sum()) if reference > 0 else 0
                ),
                "max_abs_scaled_y": float(pd.to_numeric(audit["y"], errors="coerce").abs().max()),
                "fallback_weeks": int(
                    (
                        ~audit["scale_fallback_used"].isin(
                            ["std", "insufficient_history", "not_standardized"]
                        )
                    ).sum()
                ),
            }
        ]
    )


def maturity_timeline(evaluation: ModelEvaluation, name: str, warmup_start: int) -> pd.DataFrame:
    """각 성숙 시계가 언제 끝나는지 날짜로 남긴다."""

    run = evaluation.backtest.run
    first_available = min(
        pd.Timestamp(value) for value in run.events.attrs["first_available"].values()
    )
    factor = run.composite.dropna()
    coordinate = run.coordinate_audit["y"].dropna()
    metadata = run.result.metadata
    coordinate_full = float(metadata.get("coordinate_full_history_years", 10.0))
    indicator_date = first_available + pd.DateOffset(years=10)
    coordinate_date = (
        factor.index.min() + pd.DateOffset(years=int(coordinate_full))
        if len(factor)
        else first_available
    )
    return pd.DataFrame(
        [
            {
                "candidate": name,
                "warmup_start": warmup_start,
                "first_raw_observation": str(first_available.date()),
                "first_composite_factor_week": (
                    str(factor.index.min().date()) if len(factor) else ""
                ),
                "first_coordinate_week": (
                    str(coordinate.index.min().date()) if len(coordinate) else ""
                ),
                "indicator_full_weight_date": str(indicator_date.date()),
                "coordinate_full_date": str(coordinate_date.date()),
                "total_required_maturity_years": total_maturity_years(evaluation.settings),
                "official_from": str(max(indicator_date, coordinate_date).date()),
                "status_at_end": run.result.status,
                "status_reason": str(metadata.get("status_reason", "")),
                "coordinate_history_years": float(metadata.get("coordinate_history_years", 0.0)),
            }
        ]
    )


def exact_recall_counts(evaluation: ModelEvaluation, name: str, actual: pd.Series) -> pd.DataFrame:
    """재현율을 반올림된 백분율이 아니라 주 수로 본다."""

    history = evaluation.history
    predicted = recession_prediction(history)
    flags = actual.reindex(history.index).astype(bool)
    missed = flags & ~predicted
    total = int(flags.sum())
    hit = int((flags & predicted).sum())
    # 이항 비율의 Wilson 95% 구간. 주 단위 표본은 서로 독립이 아니므로 참고값으로만 읽는다.
    z = 1.959964
    proportion = hit / total if total else float("nan")
    low = high = float("nan")
    if total:
        denominator = 1.0 + z**2 / total
        centre = (proportion + z**2 / (2 * total)) / denominator
        spread = (
            z
            * float(np.sqrt(proportion * (1 - proportion) / total + z**2 / (4 * total**2)))
            / denominator
        )
        low, high = centre - spread, centre + spread
    episodes: list[dict[str, Any]] = []
    if missed.any():
        block = (missed != missed.shift()).cumsum()
        for _, group in missed[missed].groupby(block[missed]):
            dates = pd.DatetimeIndex(group.index)
            episodes.append(
                {
                    "start": str(dates.min().date()),
                    "end": str(dates.max().date()),
                    "weeks": int(len(dates)),
                    "phase_at_start": str(history.loc[dates.min(), "phase_code"]),
                }
            )
    return pd.DataFrame(
        [
            {
                "candidate": name,
                "recession_weeks_total": total,
                "true_positive_weeks": hit,
                "false_negative_weeks": total - hit,
                "recall": proportion,
                "recall_wilson_low": low,
                "recall_wilson_high": high,
                "weeks_needed_for_85pct": max(0, int(np.ceil(0.85 * total)) - hit),
                "missed_episode_count": len(episodes),
                "missed_episodes": json.dumps(episodes, ensure_ascii=False),
            }
        ]
    )


def current_phase_detail(evaluation: ModelEvaluation, name: str) -> pd.DataFrame:
    """현재 판정이 어디에 서 있는지, 경계에서 얼마나 떨어져 있는지 남긴다."""

    run = evaluation.backtest.run
    result = run.result
    phases = phase_definitions(evaluation.settings.transitions["phases"])
    probabilities = {row["code"]: float(row["probability"]) for row in result.phase_probabilities}
    broad: dict[str, float] = {}
    for phase in phases:
        broad[phase.broad] = broad.get(phase.broad, 0.0) + probabilities[phase.code]
    angle = float(result.coordinates["angle_degrees"])
    current = next(phase for phase in phases if phase.code == result.current_phase["code"])
    lower = float(current.start)
    upper = float(current.end) if float(current.end) > lower else float(current.end) + 360.0
    reference = angle if angle >= lower else angle + 360.0
    latest = run.history.index[-1]
    audit = run.coordinate_audit
    contributions = run.contributions.reindex([latest], method="ffill").iloc[0].dropna()
    return pd.DataFrame(
        [
            {
                "candidate": name,
                "status": result.status,
                "status_reason": str(result.metadata.get("status_reason", "")),
                "current_phase": result.current_phase["code"],
                "broad_phase": result.current_phase["broad_phase"],
                "x": float(result.coordinates["x_momentum"]),
                "y": float(result.coordinates["y_level"]),
                "angle": angle,
                "radius": float(result.coordinates["radius"]),
                "unscaled_x": float(audit.loc[latest, "unscaled_x"]),
                "unscaled_y": float(audit.loc[latest, "unscaled_y"]),
                "coordinate_center": float(audit.loc[latest, "coordinate_center"]),
                "coordinate_scale": float(audit.loc[latest, "coordinate_scale"]),
                "runner_up": result.runner_up["code"],
                "top_probability": probabilities[result.current_phase["code"]],
                "runner_up_probability": float(result.runner_up["probability"]),
                "probability_gap_pp": float(result.runner_up["gap_percentage_points"]),
                "degrees_to_nearest_boundary": float(min(reference - lower, upper - reference)),
                "broad_probabilities": json.dumps(
                    {key: round(value, 4) for key, value in broad.items()}, ensure_ascii=False
                ),
                "detail_probabilities": json.dumps(
                    {key: round(value, 4) for key, value in probabilities.items()},
                    ensure_ascii=False,
                ),
                "indicator_contributions": json.dumps(
                    {str(key): round(float(value), 4) for key, value in contributions.items()},
                    ensure_ascii=False,
                ),
                "composite_dynamic_agreement": float(
                    run.composite.corr(run.dynamic.reindex(run.composite.index))
                ),
                "broad_confidence": float(result.confidence["broad"]),
                "detail_confidence": float(result.confidence["detail"]),
                "data_confidence": float(result.confidence["data"]),
            }
        ]
    )


def historical_cases(evaluation: ModelEvaluation, name: str, actual: pd.Series) -> pd.DataFrame:
    """사례별 첫 신호와 4주 확인 신호를 함께 남긴다."""

    history = evaluation.history
    windows = {
        "2001": ("2000-01-01", "2002-06-30"),
        "gfc": ("2007-01-01", "2010-06-30"),
        "2020": ("2020-01-01", "2021-06-30"),
        "2022_plus": ("2022-01-01", END),
    }
    rows: list[dict[str, Any]] = []
    for case, (start, end) in windows.items():
        window = history.loc[start:end]
        if window.empty:
            rows.append({"candidate": name, "case": case, "available": False})
            continue
        flags = actual.reindex(window.index).astype(bool)
        predicted = recession_prediction(window)
        slowdown = window["broad_phase"].eq("slowdown")
        confirmed = predicted.rolling(4).sum().eq(4)
        official = flags[flags]
        contributions = evaluation.backtest.run.contributions.reindex(window.index)
        rows.append(
            {
                "candidate": name,
                "case": case,
                "available": True,
                "official_start": str(official.index.min().date()) if len(official) else "",
                "official_end": str(official.index.max().date()) if len(official) else "",
                "first_slowdown_week": (
                    str(slowdown[slowdown].index.min().date()) if slowdown.any() else ""
                ),
                "first_contraction_week": (
                    str(predicted[predicted].index.min().date()) if predicted.any() else ""
                ),
                "confirmed_contraction_week": (
                    str(confirmed[confirmed].index.min().date()) if confirmed.any() else ""
                ),
                "contraction_weeks": int(predicted.sum()),
                "slowdown_weeks": int(slowdown.sum()),
                "false_positive_weeks": int((predicted & ~flags).sum()),
                "phase_changes_in_window": int(
                    (window["phase_code"] != window["phase_code"].shift()).sum()
                ),
                "negative_contribution_indicators": int((contributions.mean() < 0).sum()),
                "top_contributors": json.dumps(
                    {
                        str(key): round(float(value), 4)
                        for key, value in contributions.mean().abs().nlargest(3).items()
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return pd.DataFrame(rows)


def convergence_rows(evaluations: dict[int, ModelEvaluation], name: str) -> pd.DataFrame:
    """1985·1990 실행이 성숙 이후 실제로 같아지는지 층별로 잰다."""

    left, right = evaluations[1985], evaluations[1990]
    rows: list[dict[str, Any]] = []

    def compare(scope: str, key: str, a: pd.Series, b: pd.Series) -> None:
        joined = pd.concat([a.rename("left"), b.rename("right")], axis=1).dropna()
        joined = joined.loc[CONVERGENCE_START:]
        if joined.empty:
            return
        raw = joined["left"] - joined["right"]
        difference = ((raw + 180.0) % 360.0 - 180.0).abs() if key == "angle" else raw.abs()
        reference = float(joined["left"].abs().mean())
        rows.append(
            {
                "candidate": name,
                "scope": scope,
                "key": key,
                "weeks_compared": int(len(joined)),
                "mean_absolute_difference": float(difference.mean()),
                "max_absolute_difference": float(difference.max()),
                "relative_mean_difference": (
                    float(difference.mean() / reference) if reference > 0 else np.nan
                ),
                "correlation": float(joined["left"].corr(joined["right"])),
            }
        )

    compare("factor", "composite_factor", left.backtest.run.composite, right.backtest.run.composite)
    for column in (
        "coordinate_center",
        "coordinate_scale",
        "unscaled_y",
        "x",
        "y",
        "angle",
        "radius",
    ):
        compare(
            "coordinate",
            column,
            left.backtest.run.coordinate_audit[column],
            right.backtest.run.coordinate_audit[column],
        )
    for indicator in left.backtest.run.contributions.columns:
        if indicator in right.backtest.run.contributions:
            compare(
                "contribution",
                str(indicator),
                left.backtest.run.contributions[indicator],
                right.backtest.run.contributions[indicator],
            )
    left_emissions, right_emissions = _emission_frame(left), _emission_frame(right)
    for phase in left_emissions.columns:
        compare("emission", str(phase), left_emissions[phase], right_emissions[phase])
        compare(
            "filtered",
            str(phase),
            left.backtest.run.history[f"p_{phase}"],
            right.backtest.run.history[f"p_{phase}"],
        )
    for scope, column in (("selected_detail", "phase_code"), ("selected_broad", "broad_phase")):
        a = left.backtest.run.history[column].loc[CONVERGENCE_START:]
        b = right.backtest.run.history[column].loc[CONVERGENCE_START:]
        common = a.index.intersection(b.index)
        if not len(common):
            continue
        rows.append(
            {
                "candidate": name,
                "scope": scope,
                "key": "disagreement_share",
                "weeks_compared": int(len(common)),
                "mean_absolute_difference": float((a.loc[common] != b.loc[common]).mean()),
                "max_absolute_difference": 1.0,
                "relative_mean_difference": np.nan,
                "correlation": np.nan,
            }
        )
    return pd.DataFrame(rows)


def _candidate_task(
    payload: tuple[str, Settings, pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> tuple[str, dict[str, Any]]:
    """후보 하나를 1985·1990 두 시작점으로 돌리고 모든 측정을 끝낸다."""

    name, settings, core_1985, core_1990, actual_source = payload
    variant = load_baseline(name, settings)
    evaluations = {
        1985: _evaluate(f"{name}_1985", variant, core_1985, actual_source, START, END),
        1990: _evaluate(f"{name}_1990", variant, core_1990, actual_source, START, END),
    }
    actual = _official_recession_flags(
        actual_source, pd.DatetimeIndex(evaluations[1985].history.index)
    )
    rows = []
    for year, evaluation in evaluations.items():
        row = _row(evaluation, f"{name}_{year}")
        row.update(
            {
                "candidate": name,
                "warmup_start": year,
                "total_required_maturity_years": total_maturity_years(variant),
                "status": evaluation.backtest.run.result.status,
            }
        )
        rows.append(row)
    return name, {
        "rows": rows,
        "window_audit": coordinate_window_audit(evaluations[1985], name),
        "maturity": pd.concat(
            [maturity_timeline(evaluations[year], name, year) for year in (1985, 1990)]
        ),
        "convergence": convergence_rows(evaluations, name),
        "recall": exact_recall_counts(evaluations[1985], name, actual),
        "cases": historical_cases(evaluations[1985], name, actual),
        "current": current_phase_detail(evaluations[1985], name),
    }


def candidate_stage(
    settings: Settings,
    core: pd.DataFrame,
    actual: pd.DataFrame,
    names: list[str],
    workers: int = 4,
) -> dict[str, dict[str, Any]]:
    core_1985 = restrict_start(core, 1985)
    core_1990 = restrict_start(core, 1990)
    payloads = [(name, settings, core_1985, core_1990, actual) for name in names]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(_candidate_task, payloads))


def select_candidate(comparison: pd.DataFrame) -> tuple[str, str]:
    """후보 선택 규칙을 코드로 고정한다. 사후에 고르지 않는다.

    1. 전체 성숙 요구가 10년을 넘는 설정은 제외한다. 단계 A-3의 목적이 그것이다.
    2. 좌표를 재표준화하지 않는 설정(E)은 임계값 의미가 깨지므로 제외한다.
    3. 남은 후보 중 1985·1990의 2001 진입 차이가 8주 이하인 것만 남긴다.
    4. 그중 F1이 가장 높은 것을 고른다.
    """

    entries = comparison[comparison["candidate"].str.startswith("coordinate_")]
    entries = entries[entries["warmup_start"].eq(1985)]
    shift = _warmup_shift(comparison)
    eligible = []
    for _, row in entries.iterrows():
        name = str(row["candidate"])
        if name == "coordinate_e_none":
            continue
        if float(row["total_required_maturity_years"]) > MAX_TOTAL_MATURITY_YEARS:
            continue
        value = shift.get(name, float("nan"))
        if not np.isfinite(value) or value > WARMUP_SHIFT_LIMIT_WEEKS:
            continue
        eligible.append((float(row["recession_f1"]), name))
    if eligible:
        eligible.sort(reverse=True)
        return eligible[0][1], "성숙 10년 이내 + 2001 차이 8주 이내 중 F1 최고"
    fallback = entries[entries["total_required_maturity_years"].le(MAX_TOTAL_MATURITY_YEARS)]
    fallback = fallback[fallback["candidate"].ne("coordinate_e_none")]
    if fallback.empty:
        return "coordinate_b_5y", "자격 후보 없음 — 권고 후보를 그대로 둔다"
    best = fallback.sort_values("recession_f1", ascending=False).iloc[0]
    return str(best["candidate"]), "8주 기준을 만족한 후보 없음 — 성숙 조건만 만족한 F1 최고"


def _warmup_shift(comparison: pd.DataFrame) -> dict[str, float]:
    entry = comparison.pivot_table(
        index="candidate", columns="warmup_start", values="2001_entry_lag_weeks"
    )
    result: dict[str, float] = {}
    for candidate, row in entry.iterrows():
        left, right = row.get(1985, np.nan), row.get(1990, np.nan)
        result[str(candidate)] = (
            abs(float(left) - float(right)) if pd.notna(left) and pd.notna(right) else float("nan")
        )
    return result


def freeze_configuration(
    settings: Settings, name: str, commit: str, output_dir: Path
) -> tuple[str, Path]:
    """선택한 설정을 스냅샷으로 굳히고 해시를 남긴다."""

    payload = {
        "baseline_name": name,
        "source_commit": commit,
        "model_version": str(settings.model["version"]),
        "indicators": settings.indicators,
        "model": settings.model,
        "transitions": settings.transitions,
    }
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=True)
    path = output_dir / "frozen_model_config.yaml"
    path.write_text(serialized, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    (output_dir / "frozen_model_config.sha256").write_text(
        f"{digest}  frozen_model_config.yaml\n", encoding="utf-8", newline="\n"
    )
    return digest, path


__all__ = [
    "CANDIDATES",
    "Phase5Result",
    "candidate_stage",
    "claims_variants",
    "coordinate_window_audit",
    "convergence_rows",
    "current_phase_detail",
    "exact_recall_counts",
    "freeze_configuration",
    "historical_cases",
    "leave_one_out",
    "load_core_observations",
    "load_settings",
    "maturity_timeline",
    "select_candidate",
    "total_maturity_years",
]
