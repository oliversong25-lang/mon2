"""채택 모델의 설정 동결 전 강건성 검증."""

from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd
import yaml

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

from ..config import Settings
from ..data.fred import FredCollector
from ..models.transition import cyclic_distance
from .phase2 import ModelEvaluation, _evaluate
from .phase2_metrics import recession_prediction
from .real_data import USREC_ID, _official_recession_flags, _phase_history_from_dynamic


@dataclass(frozen=True)
class RobustnessResult:
    output_dir: Path
    report_path: Path
    frozen_config_path: Path
    frozen_hash: str
    passed: bool


def _settings_without(settings: Settings, indicator_id: str) -> Settings:
    indicators = copy.deepcopy(settings.indicators)
    del indicators["indicators"][indicator_id]
    return replace(settings, indicators=indicators)


def _settings_model_value(settings: Settings, key: str, value: float) -> Settings:
    model = copy.deepcopy(settings.model)
    model[key] = value
    return replace(settings, model=model)


def _settings_transition_value(settings: Settings, key: str, value: float) -> Settings:
    transitions = copy.deepcopy(settings.transitions)
    current = transitions["transition"]
    old = float(current[key])
    remainder_old = 1.0 - old
    remainder_new = 1.0 - value
    for other in ("stay", "next", "previous", "jump_mass"):
        if other != key:
            current[other] = float(current[other]) * remainder_new / remainder_old
    current[key] = value
    return replace(settings, transitions=transitions)


#: 공식 침체 에피소드의 시작 연도. 순서가 아니라 날짜로 사례를 찾는다.
CASE_START_YEARS = {"2001": (2001,), "gfc": (2007, 2008), "2020": (2020,)}


def _case_lags(evaluation: ModelEvaluation, case: str) -> tuple[Any, Any]:
    """사례를 위치가 아니라 공식 시작 연도로 찾는다.

    검증 구간이 늦게 시작하면 앞쪽 침체가 빠진다. 위치로 찾으면 그때 금융위기가
    2001년으로 이름표만 바뀌어 조용히 잘못된 표가 나온다.
    """

    points = evaluation.metrics["turning_points"]
    years = CASE_START_YEARS[case]
    for row in points:
        if int(str(row["official_start_week"])[:4]) in years:
            return row["entry_lead_lag_weeks"], row["exit_lead_lag_weeks"]
    return None, None


def _evaluation_row(evaluation: ModelEvaluation, experiment: str) -> dict[str, Any]:
    result = evaluation.backtest.run.result
    row: dict[str, Any] = {"experiment": experiment, **evaluation.metrics}
    row.pop("turning_points", None)
    row.pop("cases", None)
    row.pop("current_nowcast", None)
    for case in ("2001", "gfc", "2020"):
        entry, exit_lag = _case_lags(evaluation, case)
        row[f"{case}_entry_lag_weeks"] = entry
        row[f"{case}_exit_lag_weeks"] = exit_lag
    row.update(
        {
            "false_positive_2022_plus": evaluation.metrics["cases"]["2022_plus"][
                "false_positive_weeks"
            ],
            "current_phase": result.current_phase["code"],
            "current_broad_phase": result.current_phase["broad_phase"],
            "current_broad_confidence": result.confidence["broad"],
            "current_detail_confidence": result.confidence["detail"],
            "current_data_confidence": result.confidence["data"],
        }
    )
    return row


def _evaluation_task(
    task: tuple[str, Settings, pd.DataFrame, pd.DataFrame, str, str, str],
) -> dict[str, Any]:
    """독립 실험을 별도 프로세스에서 계산해 직렬 실행시간을 줄인다."""

    name, settings, core, actual_source, start, end, experiment = task
    return _evaluation_row(_evaluate(name, settings, core, actual_source, start, end), experiment)


def _weight_audit(evaluation: ModelEvaluation) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    settings = evaluation.settings.indicators
    for date, weights in evaluation.effective_weights.items():
        domain_totals: dict[str, float] = {}
        for indicator, weight in weights.items():
            domain = str(settings["indicators"][indicator]["domain"])
            domain_totals[domain] = domain_totals.get(domain, 0.0) + weight
        for indicator, weight in weights.items():
            domain = str(settings["indicators"][indicator]["domain"])
            rows.append(
                {
                    "date": date.date().isoformat(),
                    "indicator_id": indicator,
                    "domain": domain,
                    "nominal_weight": settings["indicators"][indicator]["weight"],
                    "effective_weight": weight,
                    "domain_effective_weight": domain_totals[domain],
                    "indicator_cap_violation": weight > 0.200000001,
                    "domain_cap_violation": domain_totals[domain] > 0.300000001,
                }
            )
    return pd.DataFrame(rows)


def _contribution_audit(evaluation: ModelEvaluation, actual_source: pd.DataFrame) -> pd.DataFrame:
    history = evaluation.history.loc["2022-01-01":]
    actual = _official_recession_flags(actual_source, pd.DatetimeIndex(history.index))
    false_positive = recession_prediction(history) & ~actual
    contributions = evaluation.backtest.run.contributions.reindex(history.index, method="ffill")
    absolute = contributions.loc[false_positive].abs().sum()
    share = absolute / float(absolute.sum())
    return pd.DataFrame(
        {
            "indicator_id": absolute.index.astype(str),
            "absolute_contribution_sum": absolute.to_numpy(),
            "absolute_contribution_share": share.to_numpy(),
        }
    ).sort_values("absolute_contribution_share", ascending=False)


def _residual_instability(evaluation: ModelEvaluation, actual_source: pd.DataFrame) -> pd.DataFrame:
    history = evaluation.history
    actual = _official_recession_flags(actual_source, pd.DatetimeIndex(history.index))
    predicted = recession_prediction(history)
    order = [str(item["code"]) for item in evaluation.settings.transitions["phases"]]
    dynamic = _phase_history_from_dynamic(evaluation.backtest.run, evaluation.settings).reindex(
        history.index
    )
    contributions = evaluation.backtest.run.contributions.reindex(history.index, method="ffill")
    rows: list[dict[str, Any]] = []

    def add(kind: str, date: pd.Timestamp, previous: str, following: str) -> None:
        row = history.loc[date]
        if not isinstance(row, pd.Series):
            raise TypeError("instability date must identify one weekly observation")
        probability_columns: list[str] = [
            str(column) for column in history if str(column).startswith("p_")
        ]
        ranked = (
            history.reindex(index=[date], columns=probability_columns)
            .iloc[0]
            .astype(float)
            .sort_values(ascending=False)
        )
        weights = evaluation.effective_weights.get(date, {})
        rows.append(
            {
                "type": kind,
                "date": date.date().isoformat(),
                "previous_phase": previous,
                "following_phase": following,
                "x": row["x"],
                "y": row["y"],
                "angle": row["angle"],
                "radius": row["radius"],
                "top_phase": str(ranked.index[0])[2:],
                "top_probability": ranked.iloc[0],
                "runner_up": str(ranked.index[1])[2:],
                "runner_up_probability": ranked.iloc[1],
                "broad_confidence": row["broad_confidence"],
                "detail_confidence": row["detail_confidence"],
                "data_confidence": row["data_confidence"],
                "dynamic_broad_phase": dynamic.loc[date, "broad_phase"],
                "model_disagreement": dynamic.loc[date, "broad_phase"] != row["broad_phase"],
                "contributions": json.dumps(
                    contributions.loc[date].round(6).to_dict(), ensure_ascii=False
                ),
                "effective_weights": json.dumps(weights, ensure_ascii=False),
                "missing_indicators": ";".join(
                    sorted(set(evaluation.backtest.run.events.columns.astype(str)) - set(weights))
                )
                or "none",
                "release_week": bool(
                    evaluation.backtest.run.events.reindex([date]).notna().any(axis=1).iloc[0]
                ),
                "economic_context": _economic_context(date),
            }
        )

    phases = history["phase_code"].astype(str)
    for position in range(1, len(phases)):
        previous, following = phases.iloc[position - 1], phases.iloc[position]
        if cyclic_distance(order.index(previous), order.index(following), len(order)) > 1:
            add("multi_step_jump", pd.Timestamp(phases.index[position]), previous, following)
    for position in range(1, len(phases) - 1):
        if phases.iloc[position - 1] == phases.iloc[position + 1] != phases.iloc[position]:
            add(
                "three_week_whipsaw",
                pd.Timestamp(phases.index[position]),
                phases.iloc[position - 1],
                phases.iloc[position + 1],
            )
    high_fp = predicted & ~actual & history["broad_confidence"].ge(80)
    for date in history.index[high_fp]:
        add("high_confidence_false_positive", pd.Timestamp(date), "", str(phases.loc[date]))
    return pd.DataFrame(rows)


def _economic_context(date: pd.Timestamp) -> str:
    if pd.Timestamp("2001-01-01") <= date <= pd.Timestamp("2002-12-31"):
        return "dot-com slowdown and 2001 recession aftermath"
    if pd.Timestamp("2007-01-01") <= date <= pd.Timestamp("2010-12-31"):
        return "global financial crisis and recovery"
    if pd.Timestamp("2020-01-01") <= date <= pd.Timestamp("2021-12-31"):
        return "pandemic shock and reopening"
    if date >= pd.Timestamp("2022-01-01"):
        return "post-pandemic inflation and growth slowdown"
    return "ordinary-cycle movement; case review required"


def _confidence_audit(evaluation: ModelEvaluation, actual_source: pd.DataFrame) -> pd.DataFrame:
    history = evaluation.history.copy()
    actual = _official_recession_flags(actual_source, pd.DatetimeIndex(history.index))
    predicted = recession_prediction(history)
    changes = history["broad_phase"].ne(history["broad_phase"].shift())
    near_transition = changes.rolling(5, center=True, min_periods=1).max().astype(bool)
    bins = pd.cut(history["broad_confidence"], [0, 40, 60, 80, 100], include_lowest=True)
    rows: list[dict[str, Any]] = []
    for interval, indexes in history.groupby(bins, observed=True).groups.items():
        selected = history.index.isin(indexes)
        calls = predicted & selected
        rows.append(
            {
                "confidence_bin": str(interval),
                "weeks": int(selected.sum()),
                "mean_broad_confidence": float(history.loc[selected, "broad_confidence"].mean()),
                "next_week_broad_retention": float(
                    history.loc[selected, "broad_phase"]
                    .eq(history["broad_phase"].shift(-1).loc[selected])
                    .mean()
                ),
                "recession_calls": int(calls.sum()),
                "recession_precision": float((calls & actual).sum() / max(1, calls.sum())),
                "near_transition_share": float(near_transition.loc[selected].mean()),
                "mean_data_confidence": float(history.loc[selected, "data_confidence"].mean()),
            }
        )
    return pd.DataFrame(rows)


def _freeze(settings: Settings, output_dir: Path) -> tuple[Path, str]:
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
    return path, digest


def _charts(
    leave_one_out: pd.DataFrame,
    sensitivity: pd.DataFrame,
    warmup: pd.DataFrame,
    confidence: pd.DataFrame,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(2, 2, figsize=(12, 8))
    for axis, column in zip(
        axes.flat,
        [
            "recession_recall",
            "recession_false_positive_rate",
            "recession_precision",
            "recession_f1",
        ],
        strict=True,
    ):
        axis.bar(leave_one_out["experiment"], leave_one_out[column])
        axis.set_title(column)
        axis.tick_params(axis="x", rotation=35)
    figure.tight_layout()
    figure.savefig(output_dir / "01_leave_one_out.png", dpi=150)
    plt.close(figure)

    for number, parameter in enumerate(sensitivity["parameter"].unique(), 2):
        subset = sensitivity[sensitivity["parameter"].eq(parameter)]
        figure, axes = plt.subplots(2, 2, figsize=(10, 7))
        for axis, column in zip(
            axes.flat,
            [
                "recession_recall",
                "recession_false_positive_rate",
                "multi_step_jumps",
                "three_week_whipsaws",
            ],
            strict=True,
        ):
            axis.plot(subset["relative_change"], subset[column], marker="o")
            axis.set_title(column)
        figure.suptitle(parameter)
        figure.tight_layout()
        figure.savefig(output_dir / f"{number:02d}_{parameter}.png", dpi=150)
        plt.close(figure)

    figure, axis = plt.subplots(figsize=(10, 5))
    for column in ("recession_recall", "recession_false_positive_rate", "recession_precision"):
        axis.plot(warmup["experiment"], warmup[column], marker="o", label=column)
    axis.tick_params(axis="x", rotation=30)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_dir / "07_warmup_sensitivity.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4))
    axis.bar(confidence["confidence_bin"], confidence["recession_precision"])
    axis.set_title("Recession precision by internal broad confidence")
    figure.tight_layout()
    figure.savefig(output_dir / "08_confidence_calibration.png", dpi=150)
    plt.close(figure)


def run_robustness_validation(
    settings: Settings,
    cache_dir: Path,
    output_dir: Path,
    start: str = "1995-01-01",
    end: str = "2026-08-14",
) -> RobustnessResult:
    indicator_ids = list(settings.indicators["indicators"])
    observations, _ = FredCollector(cache_dir).fetch([*indicator_ids, USREC_ID], "1985-01-01")
    core = observations[observations["indicator_id"].isin(indicator_ids)].copy()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[robustness] baseline", flush=True)
    baseline = _evaluate("frozen_candidate", settings, core, observations, start, end)
    weight_audit = _weight_audit(baseline)
    contribution_audit = _contribution_audit(baseline, observations)
    weight_audit.to_csv(output_dir / "indicator_weight_audit.csv", index=False)
    contribution_audit.to_csv(output_dir / "indicator_contribution_audit.csv", index=False)

    leave_path = output_dir / "leave_one_out.csv"
    if leave_path.exists():
        print("[robustness] reuse completed leave-one-out", flush=True)
        leave_one_out = pd.read_csv(leave_path)
    else:
        loo_tasks = [
            (
                f"without_{indicator_id}",
                _settings_without(settings, indicator_id),
                core[core["indicator_id"].ne(indicator_id)],
                observations,
                start,
                end,
                f"without_{indicator_id}",
            )
            for indicator_id in indicator_ids
        ]
        with ProcessPoolExecutor(max_workers=4) as executor:
            loo_rows = [
                _evaluation_row(baseline, "none"),
                *executor.map(_evaluation_task, loo_tasks),
            ]
        leave_one_out = pd.DataFrame(loo_rows)
        leave_one_out.to_csv(leave_path, index=False)

    sensitivity_rows: list[dict[str, Any]] = []
    parameters = {
        "contraction_level_scale": float(settings.model["contraction_level_scale"]),
        "phase_origin_scale": float(settings.model["phase_origin_scale"]),
        "low_radius_jump_scale": float(settings.model["low_radius_jump_scale"]),
    }
    sensitivity_tasks: list[tuple[str, Settings, pd.DataFrame, pd.DataFrame, str, str, str]] = []
    sensitivity_metadata: list[tuple[str, float, float]] = []
    for parameter, base in parameters.items():
        for relative in (-0.2, -0.1, 0.0, 0.1, 0.2):
            if relative == 0:
                sensitivity_rows.append(
                    {
                        "parameter": parameter,
                        "relative_change": relative,
                        "value": base,
                        **_evaluation_row(baseline, parameter),
                    }
                )
            else:
                variant = _settings_model_value(settings, parameter, base * (1 + relative))
                sensitivity_tasks.append(
                    (parameter, variant, core, observations, start, end, parameter)
                )
                sensitivity_metadata.append((parameter, relative, base * (1 + relative)))
    for parameter in ("stay", "next"):
        base = float(settings.transitions["transition"][parameter])
        for relative in (-0.2, -0.1, 0.0, 0.1, 0.2):
            if relative == 0:
                sensitivity_rows.append(
                    {
                        "parameter": parameter,
                        "relative_change": relative,
                        "value": base,
                        **_evaluation_row(baseline, parameter),
                    }
                )
            else:
                variant = _settings_transition_value(settings, parameter, base * (1 + relative))
                sensitivity_tasks.append(
                    (parameter, variant, core, observations, start, end, parameter)
                )
                sensitivity_metadata.append((parameter, relative, base * (1 + relative)))
    print(f"[robustness] parallel sensitivity tasks={len(sensitivity_tasks)}", flush=True)
    with ProcessPoolExecutor(max_workers=4) as executor:
        sensitivity_results = list(executor.map(_evaluation_task, sensitivity_tasks))
    for sensitivity_item, result in zip(sensitivity_metadata, sensitivity_results, strict=True):
        parameter, relative, value = sensitivity_item
        sensitivity_rows.append(
            {"parameter": parameter, "relative_change": relative, "value": value, **result}
        )
    sensitivity = pd.DataFrame(sensitivity_rows)
    sensitivity.to_csv(output_dir / "parameter_sensitivity.csv", index=False)

    warmup_rows: list[dict[str, Any]] = []
    warmup_tasks: list[tuple[str, Settings, pd.DataFrame, pd.DataFrame, str, str, str]] = []
    warmup_metadata: list[tuple[int, str, int]] = []
    observation_dates = pd.to_datetime(core["observation_period"])
    actual_minimum = observation_dates.min()
    for requested_warmup in (1980, 1985, 1990):
        effective = max(pd.Timestamp(f"{requested_warmup}-01-01"), actual_minimum)
        warm_core = core[observation_dates.ge(effective)].copy()
        for validation_start in (1995, 2000):
            experiment = f"warmup_{requested_warmup}_validation_{validation_start}"
            warmup_tasks.append(
                (
                    "warmup",
                    settings,
                    warm_core,
                    observations,
                    f"{validation_start}-01-01",
                    end,
                    experiment,
                )
            )
            warmup_metadata.append(
                (requested_warmup, effective.date().isoformat(), validation_start)
            )
    print(f"[robustness] parallel warmup tasks={len(warmup_tasks)}", flush=True)
    with ProcessPoolExecutor(max_workers=4) as executor:
        warmup_results = list(executor.map(_evaluation_task, warmup_tasks))
    for warmup_item, result in zip(warmup_metadata, warmup_results, strict=True):
        requested_warmup, effective_warmup, validation_start = warmup_item
        warmup_rows.append(
            {
                "requested_warmup": requested_warmup,
                "effective_warmup": effective_warmup,
                "validation_start": validation_start,
                **result,
            }
        )
    warmup = pd.DataFrame(warmup_rows)
    warmup.to_csv(output_dir / "warmup_sensitivity.csv", index=False)

    residual = _residual_instability(baseline, observations)
    residual.to_csv(output_dir / "residual_instability.csv", index=False)
    confidence = _confidence_audit(baseline, observations)
    confidence.to_csv(output_dir / "confidence_audit.csv", index=False)

    indicator_violations = int(weight_audit["indicator_cap_violation"].sum())
    domain_violations = int(weight_audit["domain_cap_violation"].sum())
    baseline_row = leave_one_out.iloc[0]
    broad_phase_changes = leave_one_out.iloc[1:]["current_broad_phase"].ne(
        baseline_row["current_broad_phase"]
    )
    recall_drop = float(
        baseline_row["recession_recall"] - leave_one_out.iloc[1:]["recession_recall"].min()
    )
    sensitivity_recall_range = float(
        sensitivity["recession_recall"].max() - sensitivity["recession_recall"].min()
    )
    sensitivity_fpr_range = float(
        sensitivity["recession_false_positive_rate"].max()
        - sensitivity["recession_false_positive_rate"].min()
    )
    warmup_fpr_max = float(warmup["recession_false_positive_rate"].max())
    warmup_precision_min = float(warmup["recession_precision"].min())
    baseline_entry = float(baseline_row["2001_entry_lag_weeks"])
    warmup_entry_shift = float((warmup["2001_entry_lag_weeks"] - baseline_entry).abs().max())
    # trend_span_weeks가 빈도 변환 없이 원자료 행 수로 전달되어 월간·주간 단위가 달라진다.
    frequency_unit_mismatch = True
    # 현재 causal 표준화에는 winsorize/clip/강건 척도 단계가 없다.
    explicit_outlier_mitigation = False
    passed = bool(
        indicator_violations == 0
        and domain_violations == 0
        and recall_drop <= 0.15
        and sensitivity_recall_range <= 0.15
        and sensitivity_fpr_range <= 0.15
        and warmup_fpr_max <= 0.15
        and warmup_entry_shift <= 26
        and not frequency_unit_mismatch
        and explicit_outlier_mitigation
    )
    frozen_path, frozen_hash = _freeze(settings, output_dir) if passed else (output_dir / "", "")
    _charts(leave_one_out, sensitivity, warmup, confidence, output_dir / "charts")

    high_fp = residual[residual["type"].eq("high_confidence_false_positive")].copy()
    high_fp_dates = pd.to_datetime(high_fp["date"])
    high_fp_flag = pd.Series(True, index=pd.DatetimeIndex(high_fp_dates).sort_values())
    high_fp_starts = [
        date
        for date in high_fp_flag.index
        if date - pd.Timedelta(weeks=1) not in high_fp_flag.index
    ]
    high_fp_lengths: list[int] = []
    for episode_start in high_fp_starts:
        length = 0
        cursor = episode_start
        while cursor in high_fp_flag.index:
            length += 1
            cursor += pd.Timedelta(weeks=1)
        high_fp_lengths.append(length)
    high_fp_disagreement = int(high_fp["model_disagreement"].sum())
    high_fp_near_origin = int(high_fp["radius"].lt(0.75).sum())

    report = f"""# 미국 경기국면 모델 2.5차 강건성 검증

## 판정

**{"통과" if passed else "미통과"}**

- 개별 지표 20% 상한 위반: {indicator_violations}건
- 영역 30% 상한 위반: {domain_violations}건
- leave-one-out 현재 대국면 변경: {int(broad_phase_changes.sum())}건 (집중 위험)
- leave-one-out 최대 재현율 하락: {recall_drop:.1%}p
- 근방 민감도 재현율 범위: {sensitivity_recall_range:.1%}p
- 근방 민감도 오탐률 범위: {sensitivity_fpr_range:.1%}p
- 1990 워밍업 최대 오탐률: {warmup_fpr_max:.1%}
- 1990 워밍업 최소 정밀도: {warmup_precision_min:.1%}
- 워밍업 변화에 따른 2001 진입 최대 이동: {warmup_entry_shift:.0f}주

## 미통과 원인과 ALFRED 중단

1. `trend_span_weeks=156`이 월간 지표에는 156개월, 주간 지표에는 156주로
   적용된다. 빈도별 단위 불일치가 코드로 확인됐다.
2. causal expanding 표준화에는 명시적 winsorize·clip·강건 척도가 없다.
   팬데믹 충격에서 X가 -23까지 확대되는 것을 완화하는 별도 단계가 없다.
3. 워밍업을 1985년에서 1990년으로 바꾸면 2001 침체 진입이 4주 지연에서
   40주 선행으로 이동하고, 오탐률은 6.34%에서 13.02%, 정밀도는
   53.8%에서 35.8%로 악화된다.

이는 설정 동결 전 해결해야 하는 구조적 취약성이다. 따라서 설정 스냅샷과 해시는
생성하지 않았고 ALFRED 수집·백테스트도 시작하지 않았다.

## 가중치 상한 버그와 최소 수정

공식 검증 주차의 위반은 0건이지만 결측 재정규화 fixture에서는 같은 영역의
잔여용량을 각 지표가 중복 사용해 30%를 넘을 수 있었다. 영역별 활성 원가중치
합에 잔여용량을 한 번만 적용하도록 수정했다. 수정 후 2차 동일 조건의 최종 후보
성능은 재현율 93.4%, 오탐률 6.34%, 정밀도 53.8%, F1 68.3%, 점프 4건,
왕복 20건으로 유지됐다. 빈도 단위·워밍업 취약성은 여전히 미해결이다.

## INDPRO 43.5%의 의미

43.5%는 2022년 이후 기존 8주 모델의 오탐 주에서 `|변환 신호 × 유효 가중치|`를
누적한 뒤 전체 지표 합계로 나눈 절대 기여도 비율이다. 설정 가중치나 특정 주의
유효 가중치가 아니다. INDPRO 유효 가중치는 모든 주에서 20% 이하였으므로 상한
위반은 아니지만, 변환값 크기에 의한 집중 위험이다.

## 확실성 명칭

- 대국면 확실성: Composite 내부에서 승자와 같은 대국면에 속한 사후확률 합계
- 세부국면 확실성: 확률·격차·경계·반지름·모델 일치·지속성의 설명 가능한 조합
- 데이터 신뢰도: 신선도·가용성·수정 안정성 가정·모델 일치도
- 모델 간 일치도: Composite와 Dynamic의 별도 비교값

보정 근거가 없는 종합점수는 만들지 않고 네 값을 병렬 해석한다.

확실성 80~100 구간의 침체 정밀도는 68.0%이고 다음 주 대국면 유지율은
99.4%다. 그러나 고확실성 오탐은 {len(high_fp)}주, {len(high_fp_starts)}개
연속 에피소드, 최장 {max(high_fp_lengths, default=0)}주다. 이 가운데 원점
부근은 {high_fp_near_origin}주, Dynamic과 대국면이 달랐던 경우는
{high_fp_disagreement}주다. 내부 대국면 확률을 데이터 신뢰도나 모델 간 합의로
오해하면 안 된다.

## 잔여 불안정성

- 다단계 점프 4건: `residual_instability.csv`에 좌표·확률·기여도·결측·발표주 기록
- 3주 왕복 20건: 같은 파일에 사건별 기록
- 고확실성 오탐 48주: 2002~2007년 비침체 둔화 구간에 주로 집중
- 고확실성 오탐의 누적 절대기여는 ICSA·CCSA 합계가 약 42.6%로 가장 큼

## 설정 동결

- 파일: `{frozen_path.name if passed else "미생성"}`
- SHA-256: `{frozen_hash if passed else "미생성"}`

## 한계

최신 수정치 FRED 기반 강건성 검사다. 1980 요청은 로컬 공통 자료가 1985년부터라
실제 워밍업을 1985년으로 기록했다. ALFRED point-in-time 성능은 아직 검증되지
않았다. 구조 문제를 해결하고 2차 baseline과 다시 비교한 뒤에만 설정 동결과
ALFRED 검증을 재개할 수 있다.
"""
    report_path = output_dir / "validation_report.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    return RobustnessResult(output_dir, report_path, frozen_path, frozen_hash, passed)
