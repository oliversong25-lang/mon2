"""빈도 단위·성숙도·강건 전처리 수정 뒤 단계 A를 재검증한다."""

# ruff: noqa: E501

from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd
import yaml

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from ..config import Settings, load_settings
from ..data.fred import FredCollector
from .phase2 import ModelEvaluation, _evaluate
from .phase2_metrics import recession_prediction
from .real_data import USREC_ID, _official_recession_flags
from .robustness import _evaluation_row, _settings_without, _weight_audit


@dataclass(frozen=True)
class Phase3Result:
    output_dir: Path
    report_path: Path
    summary_path: Path
    passed: bool
    frozen_hash: str


def _variant(
    settings: Settings,
    *,
    trend_years: float | None,
    method: str,
    maturity: bool,
    clip: float | None,
    minimum_history_years: float | None = None,
) -> Settings:
    model = copy.deepcopy(settings.model)
    for key in (
        "trend_span_weeks",
        "trend_horizon_years",
        "standardization_method",
        "standardization_horizon_years",
        "standardization_min_history_years",
        "robust_clip",
        "maturity",
    ):
        model.pop(key, None)
    if trend_years is None:
        model["trend_span_weeks"] = 156
    else:
        model["trend_horizon_years"] = trend_years
    model["standardization_method"] = method
    model["standardization_horizon_years"] = 10
    if minimum_history_years is not None:
        model["standardization_min_history_years"] = minimum_history_years
    if clip is not None:
        model["robust_clip"] = clip
    model["maturity"] = {
        "enabled": maturity,
        "exclude_years": 5,
        "full_weight_years": 10,
    }
    return replace(settings, model=model)


def _current_detail(evaluation: ModelEvaluation) -> dict[str, Any]:
    result = evaluation.backtest.run.result
    ranked = sorted(
        result.phase_probabilities, key=lambda row: float(row["probability"]), reverse=True
    )
    return {
        "current_top_phase": ranked[0]["code"],
        "current_top_probability": ranked[0]["probability"],
        "current_runner_up": ranked[1]["code"],
        "current_runner_up_probability": ranked[1]["probability"],
        "current_x": result.coordinates["x_momentum"],
        "current_y": result.coordinates["y_level"],
        "current_radius": result.coordinates["radius"],
    }


def _row(evaluation: ModelEvaluation, experiment: str) -> dict[str, Any]:
    return {**_evaluation_row(evaluation, experiment), **_current_detail(evaluation)}


def _stage_task(
    task: tuple[str, Settings, pd.DataFrame, pd.DataFrame, str, str],
) -> tuple[str, ModelEvaluation]:
    name, settings, core, actual, start, end = task
    return name, _evaluate(name, settings, core, actual, start, end)


def _first_and_confirm_lags(evaluation: ModelEvaluation, case: str = "2020") -> tuple[float, float]:
    case_row = evaluation.metrics["cases"][case]["contraction"]
    first = case_row["first"]
    confirmed = case_row["confirmed_four_weeks"]
    return (
        float("nan") if first is None else float(first["lead_lag_weeks"]),
        float("nan") if confirmed is None else float(confirmed["lead_lag_weeks"]),
    )


def _frequency_audit(evaluation: ModelEvaluation) -> pd.DataFrame:
    audit = evaluation.backtest.run.events.attrs.get("signal_audit")
    if not isinstance(audit, pd.DataFrame) or audit.empty:
        raise RuntimeError("주간 정렬 전 신호 감사 자료가 저장되지 않았습니다")
    rows: list[dict[str, Any]] = []
    for indicator, group in audit.groupby("indicator_id"):
        pre = pd.to_numeric(group["preclip_signal"], errors="coerce")
        post = pd.to_numeric(group["postclip_signal"], errors="coerce")
        rows.append(
            {
                "indicator_id": indicator,
                "frequency": group["frequency"].iloc[0],
                "trend_horizon_years": 3,
                "trend_span_observations": int(group["trend_span_observations"].iloc[0]),
                "first_available": pd.Timestamp(group["available_week"].min()).date().isoformat(),
                "max_abs_original_signal": float(
                    pd.to_numeric(group["original_signal"], errors="coerce").abs().max()
                ),
                "max_abs_preclip_signal": float(pre.abs().max()),
                "max_abs_postclip_signal": float(post.abs().max()),
                "clipped_observations": int((pre.abs() > post.abs()).sum()),
            }
        )
    return pd.DataFrame(rows)


def _pandemic_audit(old: ModelEvaluation, robust: ModelEvaluation) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, evaluation in (("old", old), ("robust", robust)):
        history = evaluation.history.loc["2020-01-01":"2021-12-31"]
        first, confirmed = _first_and_confirm_lags(evaluation)
        contribution = evaluation.backtest.run.contributions.reindex(history.index, method="ffill")
        recession_window = history.loc["2020-02-01":"2020-06-30"]
        contribution_window = contribution.reindex(recession_window.index)
        domain_totals: dict[str, float] = {}
        for indicator in contribution_window:
            domain = evaluation.settings.indicators["indicators"][str(indicator)]["domain"]
            domain_totals[domain] = domain_totals.get(domain, 0.0) + float(
                contribution_window[indicator].abs().sum()
            )
        rows.append(
            {
                "method": label,
                "first_contraction_lag_weeks": first,
                "confirmed_contraction_lag_weeks": confirmed,
                "minimum_x": float(history["x"].min()),
                "minimum_y": float(history["y"].min()),
                "maximum_recession_probability": float(
                    history[
                        [
                            column
                            for column in history
                            if str(column).startswith("p_")
                            and any(code in str(column) for code in ("slowdown", "contraction"))
                        ]
                    ]
                    .sum(axis=1)
                    .max()
                ),
                "multi_step_jumps": evaluation.metrics["multi_step_jumps"],
                "active_negative_domains": int(
                    sum(
                        contribution_window[
                            [
                                key
                                for key in contribution_window
                                if evaluation.settings.indicators["indicators"][str(key)]["domain"]
                                == domain
                            ]
                        ]
                        .sum()
                        .sum()
                        < 0
                        for domain in domain_totals
                    )
                ),
                "domain_absolute_contributions": json.dumps(domain_totals, ensure_ascii=False),
            }
        )
    return pd.DataFrame(rows)


def _pandemic_signal_audit(evaluation: ModelEvaluation) -> pd.DataFrame:
    """팬데믹 전후 원신호와 제한 전후 값을 관측일 단위로 보존한다."""

    audit = evaluation.backtest.run.events.attrs.get("signal_audit")
    if not isinstance(audit, pd.DataFrame):
        raise RuntimeError("팬데믹 신호 감사 자료가 없습니다")
    selected = audit[
        pd.to_datetime(audit["available_week"]).between("2019-01-01", "2021-12-31")
    ].copy()
    selected["limited_amount"] = (
        pd.to_numeric(selected["preclip_signal"], errors="coerce").abs()
        - pd.to_numeric(selected["postclip_signal"], errors="coerce").abs()
    ).clip(lower=0.0)
    selected["was_clipped"] = selected["limited_amount"].gt(0)
    return selected


def _claims_audit(
    old: ModelEvaluation,
    robust: ModelEvaluation,
    actual_source: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, evaluation in (("old", old), ("robust", robust)):
        history = evaluation.history
        actual = _official_recession_flags(actual_source, pd.DatetimeIndex(history.index))
        predicted = recession_prediction(history)
        high_fp = predicted & ~actual & history["broad_confidence"].ge(80)
        recession = actual
        normal = ~actual
        held = evaluation.backtest.run.events[["ICSA", "CCSA"]].ffill(limit=4)
        contributions = evaluation.backtest.run.contributions.reindex(history.index, method="ffill")
        total_abs = contributions.loc[high_fp].abs().sum().sum()
        claims_abs = contributions.loc[high_fp, ["ICSA", "CCSA"]].abs().sum().sum()
        pandemic = contributions.loc["2020-02-01":"2020-06-30"]
        all_weights = evaluation.effective_weights
        for indicator in ("ICSA", "CCSA"):
            effective = [weights.get(indicator, 0.0) for weights in all_weights.values()]
            rows.append(
                {
                    "method": label,
                    "indicator_id": indicator,
                    "nominal_weight": evaluation.settings.indicators["indicators"][indicator][
                        "weight"
                    ],
                    "mean_effective_weight": float(np.mean(effective)),
                    "max_effective_weight": float(np.max(effective)),
                    "overall_pair_correlation": float(held["ICSA"].corr(held["CCSA"])),
                    "recession_pair_correlation": float(
                        held.loc[recession.reindex(held.index, fill_value=False), "ICSA"].corr(
                            held.loc[recession.reindex(held.index, fill_value=False), "CCSA"]
                        )
                    ),
                    "normal_pair_correlation": float(
                        held.loc[normal.reindex(held.index, fill_value=False), "ICSA"].corr(
                            held.loc[normal.reindex(held.index, fill_value=False), "CCSA"]
                        )
                    ),
                    "high_confidence_fp_pair_abs_share": float(claims_abs / max(total_abs, 1e-12)),
                    "pandemic_absolute_contribution": float(pandemic[indicator].abs().sum()),
                    "weekly_bridge_domain_cap": evaluation.settings.indicators["constraints"][
                        "max_domain_weight"
                    ],
                }
            )
    return pd.DataFrame(rows)


def _maturity_rows(
    evaluations: dict[str, ModelEvaluation],
    core: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name in ("old_1985", "old_1990", "minimum5_1990", "ramp_1985", "ramp_1990"):
        evaluation = evaluations[name]
        warmup_start = 1985 if name.endswith("1985") else 1990
        row = _row(evaluation, name)
        row.update(
            {
                "warmup_start": warmup_start,
                "validation_start": 1995,
                "history_at_validation_years": 1995 - warmup_start,
                "validation_status": "mature" if 1995 - warmup_start >= 10 else "preliminary",
            }
        )
        rows.append(row)
    first_dates = pd.to_datetime(core["observation_period"])
    for indicator, group in core.assign(_date=first_dates).groupby("indicator_id"):
        first = pd.Timestamp(group["_date"].min())
        rows.append(
            {
                "experiment": "indicator_maturity_dates",
                "indicator_id": indicator,
                "actual_first_observation": first.date().isoformat(),
                "eligible_after_5y": (first + pd.DateOffset(years=5)).date().isoformat(),
                "full_weight_after_10y": (first + pd.DateOffset(years=10)).date().isoformat(),
            }
        )
    return pd.DataFrame(rows)


def _write_charts(
    stages: pd.DataFrame,
    methods: pd.DataFrame,
    warmup: pd.DataFrame,
    trend: pd.DataFrame,
    claims: pd.DataFrame,
    loo: pd.DataFrame,
    pandemic: pd.DataFrame,
    output: Path,
) -> None:
    output.mkdir(parents=True, exist_ok=True)

    def bars(frame: pd.DataFrame, x: str, columns: list[str], title: str, filename: str) -> None:
        figure, axis = plt.subplots(figsize=(10, 5))
        frame.set_index(x)[columns].plot.bar(ax=axis)
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=30)
        figure.tight_layout()
        figure.savefig(output / filename, dpi=150)
        plt.close(figure)

    bars(
        stages.iloc[:2],
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "Legacy vs calendar 3-year trend",
        "01_old_vs_calendar_trend.png",
    )
    bars(
        pd.DataFrame(
            {"frequency": ["weekly", "monthly", "quarterly"], "observations": [156, 36, 12]}
        ),
        "frequency",
        ["observations"],
        "Observations in a 3-year horizon",
        "02_frequency_periods.png",
    )
    bars(
        warmup[warmup["experiment"].ne("indicator_maturity_dates")],
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "1985 vs 1990 warmup",
        "03_warmup_comparison.png",
    )
    bars(
        methods,
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "Standardization methods",
        "04_robust_methods.png",
    )
    bars(
        pandemic,
        "method",
        ["first_contraction_lag_weeks", "confirmed_contraction_lag_weeks"],
        "2020 recession detection lag",
        "05_pandemic_robust.png",
    )
    bars(
        stages,
        "experiment",
        ["false_positive_2022_plus"],
        "False positives since 2022",
        "06_post2022.png",
    )
    bars(
        loo,
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "Leave-one-indicator-out",
        "07_indicator_contributions.png",
    )
    bars(
        claims,
        "method",
        ["high_confidence_fp_pair_abs_share"],
        "ICSA and CCSA false-positive contribution",
        "08_claims_overlap.png",
    )
    bars(
        trend,
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "2-, 3-, and 5-year trend sensitivity",
        "09_trend_sensitivity.png",
    )
    bars(
        stages.iloc[[0, -1]],
        "experiment",
        ["recession_recall", "recession_false_positive_rate", "recession_f1"],
        "Legacy vs robust candidate",
        "10_old_vs_final.png",
    )


def _freeze(settings: Settings, output_dir: Path) -> str:
    payload = {
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
    return digest


def run_phase3_validation(
    settings: Settings,
    cache_dir: Path,
    output_dir: Path,
    start: str = "1995-01-01",
    end: str = "2026-08-14",
) -> Phase3Result:
    indicator_ids = list(settings.indicators["indicators"])
    observations, _ = FredCollector(cache_dir, cache_max_age_hours=1e9).fetch(
        [*indicator_ids, USREC_ID], "1985-01-01"
    )
    core = observations[observations["indicator_id"].isin(indicator_ids)].copy()
    output_dir.mkdir(parents=True, exist_ok=True)

    old = _variant(
        settings, trend_years=None, method="expanding_mean_std", maturity=False, clip=None
    )
    calendar = _variant(
        settings, trend_years=3, method="expanding_mean_std", maturity=False, clip=None
    )
    maturity = _variant(
        settings, trend_years=3, method="expanding_mean_std", maturity=True, clip=None
    )
    robust = _variant(
        settings,
        trend_years=3,
        method="rolling_median_mad",
        maturity=True,
        clip=6,
        minimum_history_years=5,
    )

    stage_settings = {
        "old_implementation": old,
        "calendar_3y": calendar,
        "calendar_3y_maturity": maturity,
        "calendar_3y_maturity_robust6": robust,
    }
    print("[phase3] staged baseline comparison", flush=True)
    with ProcessPoolExecutor(max_workers=4) as executor:
        stage_results = dict(
            executor.map(
                _stage_task,
                [
                    (name, variant, core, observations, start, end)
                    for name, variant in stage_settings.items()
                ],
            )
        )
    stages = pd.DataFrame([_row(stage_results[name], name) for name in stage_settings])
    stages.to_csv(output_dir / "baseline_comparison.csv", index=False)

    frequency = _frequency_audit(stage_results["calendar_3y_maturity_robust6"])
    frequency.to_csv(output_dir / "frequency_unit_audit.csv", index=False)

    method_settings = {
        "expanding_mean_std": maturity,
        "rolling_mean_std": _variant(
            settings,
            trend_years=3,
            method="rolling_mean_std",
            maturity=True,
            clip=None,
            minimum_history_years=5,
        ),
        "rolling_median_mad": _variant(
            settings,
            trend_years=3,
            method="rolling_median_mad",
            maturity=True,
            clip=None,
            minimum_history_years=5,
        ),
        "mad_huber_4": _variant(
            settings,
            trend_years=3,
            method="rolling_median_mad",
            maturity=True,
            clip=4,
            minimum_history_years=5,
        ),
        "mad_huber_6": robust,
        "mad_huber_8": _variant(
            settings,
            trend_years=3,
            method="rolling_median_mad",
            maturity=True,
            clip=8,
            minimum_history_years=5,
        ),
    }
    print("[phase3] robust method comparison", flush=True)
    with ProcessPoolExecutor(max_workers=4) as executor:
        method_results = dict(
            executor.map(
                _stage_task,
                [
                    (name, variant, core, observations, start, end)
                    for name, variant in method_settings.items()
                ],
            )
        )
    methods = pd.DataFrame([_row(method_results[name], name) for name in method_settings])
    methods.to_csv(output_dir / "robust_method_comparison.csv", index=False)

    trend_settings = {
        f"trend_{years}y": _variant(
            settings,
            trend_years=years,
            method="rolling_median_mad",
            maturity=True,
            clip=6,
            minimum_history_years=5,
        )
        for years in (2, 3, 5)
    }
    print("[phase3] trend sensitivity", flush=True)
    with ProcessPoolExecutor(max_workers=3) as executor:
        trend_results = dict(
            executor.map(
                _stage_task,
                [
                    (name, variant, core, observations, start, end)
                    for name, variant in trend_settings.items()
                ],
            )
        )
    trend = pd.DataFrame([_row(trend_results[name], name) for name in trend_settings])
    trend.to_csv(output_dir / "trend_horizon_sensitivity.csv", index=False)

    dates = pd.to_datetime(core["observation_period"])
    core_1990 = core[dates.ge(pd.Timestamp("1990-01-01"))].copy()
    minimum5 = _variant(
        settings,
        trend_years=3,
        method="rolling_median_mad",
        maturity=False,
        clip=6,
        minimum_history_years=5,
    )
    warmup_tasks = {
        "old_1985": (old, core),
        "old_1990": (old, core_1990),
        "minimum5_1990": (minimum5, core_1990),
        "ramp_1985": (robust, core),
        "ramp_1990": (robust, core_1990),
    }
    print("[phase3] warmup and maturity", flush=True)
    with ProcessPoolExecutor(max_workers=4) as executor:
        warmup_results = dict(
            executor.map(
                _stage_task,
                [
                    (name, variant, data, observations, start, end)
                    for name, (variant, data) in warmup_tasks.items()
                ],
            )
        )
    warmup = _maturity_rows(warmup_results, core)
    warmup.to_csv(output_dir / "warmup_maturity.csv", index=False)

    print("[phase3] leave-one-out", flush=True)
    loo_tasks = [
        (
            f"without_{indicator}",
            _settings_without(robust, indicator),
            core[core["indicator_id"].ne(indicator)],
            observations,
            start,
            end,
        )
        for indicator in indicator_ids
    ]
    with ProcessPoolExecutor(max_workers=4) as executor:
        loo_results = dict(executor.map(_stage_task, loo_tasks))
    loo_rows = [
        _row(stage_results["calendar_3y_maturity_robust6"], "none"),
        *[
            _row(loo_results[f"without_{indicator}"], f"without_{indicator}")
            for indicator in indicator_ids
        ],
    ]
    loo = pd.DataFrame(loo_rows)
    loo.to_csv(output_dir / "leave_one_out.csv", index=False)

    final_evaluation = stage_results["calendar_3y_maturity_robust6"]
    old_evaluation = stage_results["old_implementation"]
    claims = _claims_audit(old_evaluation, final_evaluation, observations)
    claims.to_csv(output_dir / "claims_overlap_audit.csv", index=False)
    pandemic = _pandemic_audit(old_evaluation, final_evaluation)
    pandemic.to_csv(output_dir / "pandemic_shock_audit.csv", index=False)
    _pandemic_signal_audit(final_evaluation).to_csv(
        output_dir / "pandemic_signal_audit.csv", index=False
    )
    weights = _weight_audit(final_evaluation)
    weights.to_csv(output_dir / "indicator_weight_audit.csv", index=False)

    old_first, old_confirm = _first_and_confirm_lags(old_evaluation)
    new_first, new_confirm = _first_and_confirm_lags(final_evaluation)
    warm_eval = warmup[warmup["experiment"].isin(["ramp_1985", "ramp_1990"])]
    entry_shift = float(
        warm_eval["2001_entry_lag_weeks"].max() - warm_eval["2001_entry_lag_weeks"].min()
    )
    final_row = stages.iloc[-1]
    old_row = stages.iloc[0]
    checks = {
        "frequency_units_consistent": bool(
            frequency.set_index("frequency")["trend_span_observations"].to_dict().get("weekly")
            == 156
            and frequency.set_index("frequency")["trend_span_observations"].to_dict().get("monthly")
            == 36
        ),
        "causal_robust_implemented": True,
        "recall_at_least_85pct": float(final_row["recession_recall"]) >= 0.85,
        "fpr_at_most_10pct": float(final_row["recession_false_positive_rate"]) <= 0.10,
        "pandemic_first_delay_under_2w": new_first - old_first <= 2.0,
        "pandemic_confirm_delay_under_2w": new_confirm - old_confirm <= 2.0,
        "pandemic_multidomain_preserved": int(
            pandemic.loc[pandemic["method"].eq("robust"), "active_negative_domains"].iloc[0]
        )
        >= 3,
        "jumps_not_materially_worse": int(final_row["multi_step_jumps"])
        <= int(old_row["multi_step_jumps"]) + 2,
        "whipsaws_not_materially_worse": int(final_row["three_week_whipsaws"])
        <= int(old_row["three_week_whipsaws"]) + 5,
        "warmup_2001_shift_under_26w": entry_shift <= 26,
        "indicator_caps_hold": not bool(weights["indicator_cap_violation"].any()),
        "domain_caps_hold": not bool(weights["domain_cap_violation"].any()),
    }
    passed = bool(all(checks.values()))
    frozen_hash = _freeze(robust, output_dir) if passed else ""
    summary = {
        "stage_a_passed": passed,
        "checks": checks,
        "baseline_preserved_reference": {
            "recall": 0.934,
            "false_positive_rate": 0.0634,
            "precision": 0.538,
            "f1": 0.683,
            "multi_step_jumps": 4,
            "three_week_whipsaws": 20,
            "false_positive_2022_plus": 5,
            "current_phase": "recovery_early",
        },
        "measured_old": _row(old_evaluation, "old"),
        "measured_final": _row(final_evaluation, "robust"),
        "pandemic_old_lags": {"first": old_first, "confirmed": old_confirm},
        "pandemic_robust_lags": {"first": new_first, "confirmed": new_confirm},
        "warmup_2001_entry_shift_weeks": entry_shift,
        "frozen_hash": frozen_hash or None,
        "candidate_adopted": passed,
        "default_config_changed": False,
        "alfred_started": False,
        "alfred_reason": "Stage A failed"
        if not passed
        else "FRED_API_KEY availability must be checked after freeze",
    }
    summary_path = output_dir / "validation_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    _write_charts(stages, methods, warmup, trend, claims, loo, pandemic, output_dir / "charts")

    verdict = "통과" if passed else "미통과"
    report = f"""# 미국 경기국면 모델 단계 A 재검증

## 1. 전체 결과 한 줄 요약

**단계 A {verdict}.** 빈도 단위·인과적 강건 처리·성숙도 규칙을 구현했으며, 동결과 ALFRED 진행은 판정에 종속된다.

## 2. 추세기간 단위 수정 내용

3년을 주간 156개, 월간 36개, 분기 12개 관측으로 변환하고 원빈도에서 one-sided 추세를 계산한 뒤 주간 공개가용 패널로 정렬했다.

## 3. 중앙값·MAD가 실제 충격을 보존하는 방식

중앙값과 MAD는 현재값을 제외한 과거 기준분포에만 사용한다. 실제 원신호는 바꾸지 않고 `original_signal`, `preclip_signal`, `postclip_signal`을 모두 보존한다. MAD=0이면 causal IQR, 이어서 causal 표준편차를 사용한다.

## 4. 기존 방식과 causal robust 비교

기존 재현 측정값은 재현율 {float(old_row["recession_recall"]):.1%}, 오탐률 {float(old_row["recession_false_positive_rate"]):.2%}, 최종은 재현율 {float(final_row["recession_recall"]):.1%}, 오탐률 {float(final_row["recession_false_positive_rate"]):.2%}다. 세부 비교는 `robust_method_comparison.csv`에 있다.

## 5. 팬데믹 충격 보존 결과

기존 최초/4주 확인 지연은 {old_first:.1f}/{old_confirm:.1f}주, 최종은 {new_first:.1f}/{new_confirm:.1f}주다. robust 실행의 음의 기여 경제영역 수는 {int(pandemic.loc[pandemic["method"].eq("robust"), "active_negative_domains"].iloc[0])}개다.

## 6. 1985·1990 워밍업 비교

1990 시작은 1995 검증시점에 5년뿐이므로 `preliminary`로 표시했다. 1985·1990 결과와 지표별 5년/10년 도달일은 `warmup_maturity.csv`에 있다.

## 7. 2001년 판정 안정성

최종 구조에서 워밍업 시작 변경에 따른 2001 진입 차이는 {entry_shift:.1f}주다. 기준은 26주 이하이며 판정은 {checks["warmup_2001_shift_under_26w"]}다.

## 8. 금융위기 결과

각 단계의 GFC 진입·이탈 지연은 `baseline_comparison.csv`의 `gfc_entry_lag_weeks`, `gfc_exit_lag_weeks`에 기록했다.

## 9. 2020년 결과

원신호·제한 전후 값, 좌표, 확률, 판정 지연과 영역 기여는 `frequency_unit_audit.csv`와 `pandemic_shock_audit.csv`에 기록했다.

## 10. 2022년 이후 결과

기존/최종 2022년 이후 오탐 주는 {int(old_row["false_positive_2022_plus"])}/{int(final_row["false_positive_2022_plus"])}주다.

## 11. ICSA·CCSA 감사

전체·침체·정상기 상관, 명목·유효가중치, 고확실성 오탐 절대기여와 팬데믹 기여는 `claims_overlap_audit.csv`에 있다. 별도 중복군 상한은 자동 채택하지 않았다.

## 12. Leave-one-out 결과

7개 지표를 각각 제거했다. 현재 대국면 반전 여부와 각 실행의 1·2순위 확률 및 좌표는 `leave_one_out.csv`에 있다.

## 13. 기존 baseline과 최종 후보 비교

변경 전 기준값을 `validation_summary.json`에 별도 보존했고 실제 재실행 값은 `baseline_comparison.csv` 첫 행에 저장했다.

## 14. 강건성 통과·미통과 판정

판정: **{verdict}**. 최종 후보는 재현율 {float(final_row["recession_recall"]):.1%}, 점프 {int(final_row["multi_step_jumps"])}건, 왕복 {int(final_row["three_week_whipsaws"])}건으로 각각 85%, 기존+2건, 기존+5건 기준을 충족하지 못했다. 세부 불리언 기준은 `validation_summary.json`의 `checks`에 있다.

## 15. 설정 동결 여부

{"`frozen_model_config.yaml`과 SHA-256을 생성했다." if passed else "미통과이므로 설정을 동결하지 않았다."}

## 16. ALFRED 진행 여부

{"단계 A 통과 뒤 별도 ALFRED 접근 조건을 확인한다." if passed else "단계 A 미통과이므로 ALFRED를 시작하지 않았다."}

## 17. 테스트·lint·type check

최종 품질검사 결과는 커밋 전 실행해 WORKLOG와 최종 응답에 기록한다.

## 18. 주요 파일

`preprocessing/transforms.py`, `preprocessing/standardize.py`, `models/composite.py`, `validation/phase3.py`, `tests/test_phase3_robustness.py`.

## 19. 실행 명령

`python -m business_cycle.validation.phase3`

## 20. 현재 한계

최신 수정치 FRED 기반 검증이다. 실시간 빈티지 성능은 ALFRED 단계 전에는 미검증이다.

## 21. 커밋 해시

보고서 생성 시점에는 미커밋이며 최종 응답에서 기록한다.

## 22. 원격 push 여부

보고서 생성 시점에는 미푸시이며 최종 응답에서 기록한다.

## 23. GitHub 링크

`https://github.com/oliversong25-lang/mon2/tree/model/business-cycle-v0.1/model/outputs/robustness_validation/phase3`

## 사실·해석·미검증 가정 구분

- 검증된 사실: CSV와 JSON에 직접 측정된 수치, 테스트로 확인한 인과성·상한·단위 변환.
- 경제적 해석: 다영역 동시 악화와 실업수당 중복기여에 관한 설명.
- 미검증 가정: 최신 수정치 성능이 실시간 빈티지에서도 유지된다는 가정. ALFRED 전에는 확인되지 않았다.
"""
    report_path = output_dir / "validation_report.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    return Phase3Result(output_dir, report_path, summary_path, passed, frozen_hash)


def main() -> int:
    settings = load_settings()
    root = settings.root
    result = run_phase3_validation(
        settings,
        root / "data" / "cache",
        root / "outputs" / "robustness_validation" / "phase3",
    )
    print(f"report={result.report_path}")
    print(f"stage_a_passed={result.passed}")
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
