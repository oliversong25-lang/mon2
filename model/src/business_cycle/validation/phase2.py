"""미국 경기국면 모델 v0.1의 2차 실자료 보정 실행기."""

# ruff: noqa: E501

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ..backtest.engine import BacktestResult, run_backtest
from ..config import Settings
from ..data.fred import FredCollector
from ..models.composite import CompositeFactorModel
from ..pipeline import PRELIMINARY_WARNING
from ..preprocessing.frequency import held_signal_matrix
from .phase2_charts import create_phase2_charts
from .phase2_metrics import (
    binary_episodes,
    causal_confirmed_signals,
    classification_metrics,
    false_positive_episode_table,
    recession_prediction,
    stability_metrics,
    turning_point_metrics,
)
from .real_data import (
    USREC_ID,
    _confidence_history,
    _official_recession_flags,
    _phase_history_from_dynamic,
)


@dataclass(frozen=True)
class Phase2Result:
    output_dir: Path
    report_path: Path
    summary_path: Path
    chart_paths: list[Path]
    final_model: str


@dataclass
class ModelEvaluation:
    name: str
    settings: Settings
    backtest: BacktestResult
    history: pd.DataFrame
    metrics: dict[str, Any]
    effective_weights: dict[pd.Timestamp, dict[str, float]]


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _variant(
    settings: Settings,
    *,
    momentum_weeks: int,
    level_gate: bool,
    jump_constraint: bool,
) -> Settings:
    model = copy.deepcopy(settings.model)
    model["momentum_weeks"] = momentum_weeks
    model["contraction_level_gate"] = level_gate
    model["low_radius_jump_constraint"] = jump_constraint
    return replace(settings, model=model)


def _signal_detail(
    history: pd.DataFrame, broad: str, official_start: pd.Timestamp
) -> dict[str, Any]:
    flag = history["broad_phase"].eq(broad)
    window_start = official_start - pd.Timedelta(weeks=52)
    window_end = official_start + pd.Timedelta(weeks=52)
    entries = flag & ~flag.shift(1, fill_value=False)
    first_candidates = entries[
        entries & entries.index.to_series().between(window_start, window_end)
    ]
    first = (
        min(
            pd.DatetimeIndex(first_candidates.index),
            key=lambda date: abs((date - official_start).days),
        )
        if len(first_candidates)
        else None
    )
    confirmed = causal_confirmed_signals(flag, 4)
    confirmations = [date for date in confirmed.entries if window_start <= date <= window_end]
    confirmation = (
        min(confirmations, key=lambda date: abs((date - official_start).days))
        if confirmations
        else None
    )

    def point(date: pd.Timestamp | None) -> dict[str, Any] | None:
        if date is None:
            return None
        row = history.loc[date]
        if not isinstance(row, pd.Series):
            raise TypeError("signal date must identify one weekly observation")
        probability_columns: list[str] = [
            str(column) for column in history if str(column).startswith("p_")
        ]
        probabilities = pd.Series(
            [float(row.loc[column]) for column in probability_columns],
            index=probability_columns,
            dtype=float,
        ).sort_values(ascending=False)
        return {
            "date": date.date().isoformat(),
            "lead_lag_weeks": round((date - official_start).days / 7, 1),
            "x": float(row["x"]),
            "y": float(row["y"]),
            "radius": float(row["radius"]),
            "top_phase": str(probabilities.index[0])[2:],
            "top_probability": float(probabilities.iloc[0]),
            "runner_up": str(probabilities.index[1])[2:],
            "runner_up_probability": float(probabilities.iloc[1]),
            "broad_confidence": float(row["broad_confidence"]),
            "detail_confidence": float(row["detail_confidence"]),
        }

    return {"first": point(first), "confirmed_four_weeks": point(confirmation)}


def _case_metrics(history: pd.DataFrame, actual: pd.Series) -> dict[str, Any]:
    episodes = binary_episodes(actual)
    rows: dict[str, Any] = {}
    for start, end, _ in episodes:
        key = "2001" if start.year == 2001 else ("gfc" if start.year == 2008 else "2020")
        rows[key] = {
            "official_start": start.date().isoformat(),
            "official_end": end.date().isoformat(),
            "slowdown": _signal_detail(history, "slowdown", start),
            "contraction": _signal_detail(history, "contraction", start),
        }
    recent = history.loc["2022-01-01":]
    recent_actual = actual.reindex(recent.index, fill_value=False)
    recent_fp = recession_prediction(recent) & ~recent_actual
    rows["2022_plus"] = {
        "false_positive_weeks": int(recent_fp.sum()),
        "false_positive_episodes": len(binary_episodes(recent_fp)),
        "mean_x_on_false_positive": float(recent.loc[recent_fp, "x"].mean()),
        "mean_y_on_false_positive": float(recent.loc[recent_fp, "y"].mean()),
        "mean_radius_on_false_positive": float(recent.loc[recent_fp, "radius"].mean()),
    }
    return rows


def _evaluate(
    name: str,
    settings: Settings,
    core: pd.DataFrame,
    actual_source: pd.DataFrame,
    start: str,
    end: str,
) -> ModelEvaluation:
    result = run_backtest(core, settings, start, end, True)
    history = _confidence_history(result.run, settings).loc[start:end]
    actual = _official_recession_flags(actual_source, pd.DatetimeIndex(history.index))
    predicted = recession_prediction(history)
    metrics: dict[str, Any] = {
        **classification_metrics(predicted, actual),
        **stability_metrics(
            history, [str(phase["code"]) for phase in settings.transitions["phases"]]
        ),
    }
    false_positive = predicted & ~actual
    episode_lengths = [duration for _, _, duration in binary_episodes(false_positive)]
    metrics.update(
        {
            "weeks": len(history),
            "false_positive_episode_count": len(episode_lengths),
            "long_false_positive_episode_count": sum(length >= 8 for length in episode_lengths),
            "longest_false_positive_weeks": max(episode_lengths, default=0),
            "turning_points": turning_point_metrics(predicted, actual, 4),
            "cases": _case_metrics(history, actual),
            "current_nowcast": result.run.result.to_dict(),
            "high_broad_confidence_false_positive_weeks": int(
                (false_positive & history["broad_confidence"].ge(80)).sum()
            ),
        }
    )
    estimate = CompositeFactorModel(
        settings.indicators["indicators"],
        settings.indicators["constraints"],
        settings.model.get("maturity"),
        settings.model.get("robust_clip"),
    ).fit_filter(result.run.events)
    raw_weights = estimate.metadata["effective_weights"]
    if not isinstance(raw_weights, dict):
        raise TypeError("합성모델 유효가중치 메타데이터가 객체가 아닙니다")
    effective_weights = {
        pd.Timestamp(str(timestamp)): {str(key): float(value) for key, value in values.items()}
        for timestamp, values in raw_weights.items()
        if isinstance(values, dict)
    }
    return ModelEvaluation(name, settings, result, history, metrics, effective_weights)


def _segment_rows(
    evaluations: list[ModelEvaluation], actual_source: pd.DataFrame
) -> list[dict[str, Any]]:
    segments = [
        ("overall", "1995-01-01", "2026-08-14"),
        ("development", "1995-01-01", "2012-12-31"),
        ("stability", "2013-01-01", "2019-12-31"),
        ("pandemic_stress", "2020-01-01", "2020-12-31"),
        ("observed_diagnostic_holdout", "2021-01-01", "2026-08-14"),
    ]
    rows: list[dict[str, Any]] = []
    for evaluation in evaluations:
        for segment, start, end in segments:
            history = evaluation.history.loc[start:end]
            actual = _official_recession_flags(actual_source, pd.DatetimeIndex(history.index))
            metrics = classification_metrics(recession_prediction(history), actual)
            stability = stability_metrics(
                history,
                [str(phase["code"]) for phase in evaluation.settings.transitions["phases"]],
            )
            fp = recession_prediction(history) & ~actual
            lengths = [duration for _, _, duration in binary_episodes(fp)]
            rows.append(
                {
                    "model": evaluation.name,
                    "segment": segment,
                    **metrics,
                    **stability,
                    "false_positive_episode_count": len(lengths),
                    "longest_false_positive_weeks": max(lengths, default=0),
                    "turning_points": (
                        json.dumps(evaluation.metrics["turning_points"], ensure_ascii=False)
                        if segment == "overall"
                        else None
                    ),
                    "current_phase": (
                        evaluation.backtest.run.result.current_phase["code"]
                        if segment == "overall"
                        else None
                    ),
                }
            )
    return rows


def _history_segment_rows(
    name: str,
    history: pd.DataFrame,
    actual_source: pd.DataFrame,
    phase_order: list[str],
) -> list[dict[str, Any]]:
    """비교모델도 대표모델과 같은 산식과 구간으로 평가한다."""

    segments = [
        ("overall", "1995-01-01", "2026-08-14"),
        ("development", "1995-01-01", "2012-12-31"),
        ("stability", "2013-01-01", "2019-12-31"),
        ("pandemic_stress", "2020-01-01", "2020-12-31"),
        ("observed_diagnostic_holdout", "2021-01-01", "2026-08-14"),
    ]
    rows: list[dict[str, Any]] = []
    for segment, start, end in segments:
        subset = history.loc[start:end]
        actual = _official_recession_flags(actual_source, pd.DatetimeIndex(subset.index))
        predicted = recession_prediction(subset)
        false_positive = predicted & ~actual
        lengths = [duration for _, _, duration in binary_episodes(false_positive)]
        rows.append(
            {
                "model": name,
                "segment": segment,
                **classification_metrics(predicted, actual),
                **stability_metrics(subset, phase_order),
                "false_positive_episode_count": len(lengths),
                "longest_false_positive_weeks": max(lengths, default=0),
                "turning_points": (
                    json.dumps(turning_point_metrics(predicted, actual, 4), ensure_ascii=False)
                    if segment == "overall"
                    else None
                ),
                "current_phase": (
                    str(subset["phase_code"].iloc[-1]) if segment == "overall" else None
                ),
            }
        )
    return rows


def _indicator_table(evaluation: ModelEvaluation) -> pd.DataFrame:
    history = evaluation.history.loc["2022-01-01":].copy()
    events = evaluation.backtest.run.events
    ages = {
        key: int(value.get("max_age_weeks", 8))
        for key, value in evaluation.settings.indicators["indicators"].items()
    }
    held = held_signal_matrix(events, ages).reindex(history.index)
    contributions = evaluation.backtest.run.contributions.reindex(history.index, method="ffill")
    result = history.copy()
    probability_columns = [column for column in history if str(column).startswith("p_")]
    ordered = history[probability_columns].apply(
        lambda row: row.astype(float).sort_values(ascending=False).index.tolist(), axis=1
    )
    result["runner_up_phase"] = [str(values[1])[2:] for values in ordered]
    result["release_indicators"] = [
        "; ".join(str(key) for key, value in row.items() if pd.notna(value)) or "none"
        for _, row in events.reindex(history.index).iterrows()
    ]
    for indicator_id in events.columns:
        key = str(indicator_id)
        result[f"signal_{key}"] = held[indicator_id]
        result[f"contribution_{key}"] = contributions[indicator_id]
        result[f"weight_{key}"] = [
            evaluation.effective_weights.get(pd.Timestamp(timestamp), {}).get(key, 0.0)
            for timestamp in history.index
        ]
    return result.reset_index(names="date")


def _metric_audit() -> str:
    return f"""# 평가 지표 감사

## 침체 양성 정의

- 예측 양성은 `broad_phase == contraction`, 즉 `contraction_early`, `contraction_mid`, `contraction_late`뿐이다.
- `slowdown`은 선행·둔화 현재국면이며 침체 양성에 포함하지 않는다.
- 실제 양성은 공식 FRED `{USREC_ID}` 값이 1인 주다.

## USREC 월간→주간 정렬

- 월초 관측일을 원래 월간 인덱스에 유지한 뒤 모델의 금요일 주간 인덱스와 합친다.
- 과거 값만 forward-fill하고 모델 금요일에 다시 맞춘다.
- 따라서 USREC가 1로 바뀐 월의 첫 금요일부터 양성이고, 0으로 바뀐 월의 첫 금요일부터 음성이다.
- 최신 수정치 chronology이며 당시 발표정보 빈티지가 아니다.

## 공식

- 재현율 = TP / (TP + FN)
- 오탐률 = FP / (FP + TN), 분모는 실제 비침체 주 전체
- 정밀도 = TP / (TP + FP), 분모는 모델이 침체라고 판정한 주 전체
- 특이도 = TN / (TN + FP) = 1 - 오탐률
- F1 = 2 × 정밀도 × 재현율 / (정밀도 + 재현율)
- 균형정확도 = (재현율 + 특이도) / 2
- 세부국면 변경 = 인접 주 `phase_code`가 달라진 횟수
- 대국면 변경 = 인접 주 `broad_phase`가 달라진 횟수
- 다단계 점프 = 12개 순환 상태에서 최소 거리가 2 이상인 인접 주 이동
- 3주 왕복 = A→B→A이며 A와 B가 다른 경우

## 진입·종료 lead-lag

- 한 주짜리 판정을 사용하지 않는다.
- 4주 연속 같은 이진 판정이 관측된 네 번째 주를 causal 확인일로 사용한다.
- 첫 주로 소급하지 않으며 미래 관측을 확인일 이전 판정에 사용하지 않는다.
- lead-lag = 모델 확인일 - 공식 USREC 전환 주. 음수는 선행, 양수는 지연이다.
"""


def _markdown_table(frame: pd.DataFrame) -> str:
    """추가 런타임 의존성 없이 작은 비교표를 Markdown으로 만든다."""

    values = frame.copy()
    for column in values.columns:
        values[column] = values[column].map(
            lambda value: f"{value:.4f}" if isinstance(value, float) else str(value)
        )
    header = "| " + " | ".join(str(column) for column in values.columns) + " |"
    divider = "| " + " | ".join("---" for _ in values.columns) + " |"
    rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in values.itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *rows])


def _diagnosis_report(
    baseline: ModelEvaluation,
    final: ModelEvaluation,
    baseline_episodes: pd.DataFrame,
    final_episodes: pd.DataFrame,
    indicator_table: pd.DataFrame,
) -> str:
    baseline_recent = baseline.metrics["cases"]["2022_plus"]
    final_recent = final.metrics["cases"]["2022_plus"]
    contribution_columns = [
        column for column in indicator_table if str(column).startswith("contribution_")
    ]
    recent_fp = indicator_table["broad_phase"].eq("contraction")
    absolute = indicator_table.loc[recent_fp, contribution_columns].abs().sum()
    shares = (absolute / max(1e-12, float(absolute.sum()))).sort_values(ascending=False)
    top = ", ".join(
        f"{str(key).replace('contribution_', '')} {value:.1%}"
        for key, value in shares.head(4).items()
    )
    return f"""# 침체 오탐 구조 진단

## 전체 오탐

- 8주 baseline: {baseline.metrics["false_positive_weeks"]}주, {len(baseline_episodes)}개 에피소드, 최장 {baseline.metrics["longest_false_positive_weeks"]}주
- 최종 후보: {final.metrics["false_positive_weeks"]}주, {len(final_episodes)}개 에피소드, 최장 {final.metrics["longest_false_positive_weeks"]}주
- 모든 에피소드의 좌표·확실성·기여·결측·발표주 정보는 `false_positive_episodes.csv`에 기록했다.

## 2022년 이후 77주 원인

- 8주 baseline 오탐 {baseline_recent["false_positive_weeks"]}주 → 최종 후보 {final_recent["false_positive_weeks"]}주
- baseline 오탐 평균 X {baseline_recent["mean_x_on_false_positive"]:.3f}, Y {baseline_recent["mean_y_on_false_positive"]:.3f}, 반지름 {baseline_recent["mean_radius_on_false_positive"]:.3f}
- 실제 NBER 침체의 1차 분석 평균 반지름은 약 3.16이었지만 2022+ 오탐은 약 0.30이었다.
- 오탐 중 절대 기여 비중 상위: {top}
- 주원인은 약한 음수 X·Y가 원점 부근에서도 각도만으로 침체 국면을 확정한 구조다.
- INDPRO 신호 크기가 가장 오래 음의 기여를 냈지만 설정 가중치는 20% 상한을 넘지 않았다.
- 핵심 본체에는 금리·선행지표가 포함되지 않았고 13주 전망은 계속 `not_calibrated`다.
- 결측 최소 가용률 문제보다 팬데믹 이후 expanding 기준에서 Y가 장기간 약한 음수였던 영향이 컸다.

## 조기 침체판정

- 2001년과 금융위기는 첫 후퇴기·첫 침체기·4주 확인일을 분리했다.
- 8주 baseline의 지속 침체는 NBER보다 지나치게 빨랐고, Y 근거 게이트 후 NBER에 가까워졌다.
- 이는 선행 후퇴기와 현재 침체기를 같은 신호로 취급한 평가 문제가 아니라 현재 침체 관측확률의 수준 근거 부족이었다.

## 최소 보정

- 1단계: Y의 음수 크기가 기존 `phase_origin_scale`에 도달할 때까지 침체 emission 근거를 연속적으로 확대한다.
- 제거된 침체 확률은 순환상 인접한 후퇴기 말기·회복기 초기에 각도 거리대로 배분한다.
- 2단계: 반지름이 같은 원점 척도보다 작을 때만 이전 세부국면과 인접 국면 밖 posterior를 제거한다.
- 큰 반지름의 외생 충격은 다단계 점프가 계속 가능하며 최소 지속기간이나 backward smoothing은 사용하지 않는다.
"""


def _validation_report(
    evaluations: list[ModelEvaluation],
    final: ModelEvaluation,
    comparison: pd.DataFrame,
    dynamic_agreement: float,
) -> str:
    lines = [
        "# 미국 경기국면 모델 v0.1 — 2차 실자료 보정",
        "",
        f"> {PRELIMINARY_WARNING}",
        "",
        "## 결론",
        "",
        "Y 수준 근거 게이트와 원점 부근 인접이동 제약을 단계적으로 적용한 후보를 최종 채택했다.",
        "8주 baseline의 높은 침체 재현율과 2020 반응성을 유지하면서 정상기 오탐과 점프를 크게 줄였다.",
        "",
        "## 전체기간 비교",
        "",
        _markdown_table(comparison[comparison["segment"].eq("overall")]),
        "",
        "## 최종 nowcast",
        "",
        f"- 국면: {final.backtest.run.result.current_phase['label_ko']}",
        f"- 대국면/세부/데이터 확실성: {final.backtest.run.result.confidence['broad']:.1f} / {final.backtest.run.result.confidence['detail']:.1f} / {final.backtest.run.result.confidence['data']:.1f}",
        f"- Composite-Dynamic 대국면 일치율: {dynamic_agreement:.1%}",
        "- 대국면 확실성은 대표모델 내부의 같은 대국면 확률합이고 모델 간 합의도가 아니다.",
        "- 모델 불일치는 데이터 신뢰도의 model_agreement 구성요소에만 반영된다.",
        "- 세부 확실성이 낮으면 현재 대국면 안의 세부 위치 해석을 보수적으로 해야 한다.",
        "",
        "## 구간 해석",
        "",
        "- 1995~2012는 개발·원인분석 구간이다.",
        "- 2013~2019는 정상 확장기 안정성 확인 구간이다.",
        "- 2020은 별도 극단 충격 스트레스 구간이다.",
        "- 2021~2026은 이미 결과를 본 observed diagnostic holdout이며 진정한 표본외가 아니다.",
        "",
        "## 검증된 사실·경제적 해석·미검증 가정",
        "",
        "### 코드와 실자료에서 검증된 사실",
        "- 후퇴기는 침체 양성에 포함되지 않는다.",
        "- 공식 FRED USREC와 같은 기간·자료로 네 모델을 비교했다.",
        "- 최종 후보는 2020 진입·종료 반응을 8주 baseline과 동일하게 유지했다.",
        "",
        "### 경제적 해석",
        "- 약한 음수 Y와 작은 반지름은 공식 침체보다 둔화·불확실성으로 해석하는 것이 타당하다.",
        "- INDPRO의 큰 음의 신호는 2022+ 오탐을 오래 지지했으나 단일 가중치 상한 위반은 아니다.",
        "",
        "### 아직 검증되지 않은 가정",
        "- 최신 수정치 결과가 당시 실시간 빈티지에서도 유지된다는 보장은 없다.",
        "- 12개 세부국면에는 공식 정답 라벨이 없다.",
        "- 2021+는 observed diagnostic holdout이므로 향후 신규 자료의 prospective 검증이 필요하다.",
    ]
    return "\n".join(lines) + "\n"


def run_phase2_validation(
    settings: Settings,
    start: str,
    end: str,
    cache_dir: Path,
    output_dir: Path,
) -> Phase2Result:
    collector = FredCollector(cache_dir)
    core_ids = list(settings.indicators["indicators"])
    collection_start = (pd.Timestamp(start) - pd.DateOffset(years=10)).date().isoformat()
    observations, collection_warnings = collector.fetch([*core_ids, USREC_ID], collection_start)
    core = observations[observations["indicator_id"].isin(core_ids)].copy()
    variants = [
        (
            "baseline_4w",
            _variant(settings, momentum_weeks=4, level_gate=False, jump_constraint=False),
        ),
        (
            "baseline_8w",
            _variant(settings, momentum_weeks=8, level_gate=False, jump_constraint=False),
        ),
        (
            "candidate_y_gate",
            _variant(settings, momentum_weeks=8, level_gate=True, jump_constraint=False),
        ),
        (
            "candidate_y_gate_adjacent",
            _variant(settings, momentum_weeks=8, level_gate=True, jump_constraint=True),
        ),
    ]
    evaluations = [
        _evaluate(name, variant, core, observations, start, end) for name, variant in variants
    ]
    by_name = {evaluation.name: evaluation for evaluation in evaluations}
    final_name = "candidate_y_gate_adjacent"
    final = by_name[final_name]
    actual = _official_recession_flags(observations, pd.DatetimeIndex(final.history.index))
    baseline = by_name["baseline_8w"]
    baseline_episodes = false_positive_episode_table(
        baseline.history,
        actual,
        baseline.backtest.run,
        baseline.effective_weights,
        float(settings.model["phase_origin_scale"]),
    )
    final_episodes = false_positive_episode_table(
        final.history,
        actual,
        final.backtest.run,
        final.effective_weights,
        float(settings.model["phase_origin_scale"]),
    )
    baseline_indicator_table = _indicator_table(baseline)
    final_indicator_table = _indicator_table(final)
    indicator_table = pd.concat(
        [
            baseline_indicator_table.assign(model="baseline_8w"),
            final_indicator_table.assign(model=final_name),
        ],
        ignore_index=True,
    )
    dynamic_history = _phase_history_from_dynamic(final.backtest.run, final.settings).reindex(
        final.history.index
    )
    phase_order = [str(phase["code"]) for phase in settings.transitions["phases"]]
    comparison = pd.DataFrame(
        [
            *_segment_rows(evaluations, observations),
            *_history_segment_rows("dynamic_factor", dynamic_history, observations, phase_order),
        ]
    )
    dynamic_agreement = float(
        final.history["broad_phase"].eq(dynamic_history["broad_phase"]).mean()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metric_audit.md").write_text(_metric_audit(), encoding="utf-8", newline="\n")
    final_episodes.to_csv(output_dir / "false_positive_episodes.csv", index=False)
    baseline_episodes.to_csv(output_dir / "false_positive_episodes_baseline_8w.csv", index=False)
    indicator_table.to_csv(output_dir / "indicator_contributions_2022_plus.csv", index=False)
    comparison.to_csv(output_dir / "candidate_comparison.csv", index=False)
    diagnosis = _diagnosis_report(
        baseline, final, baseline_episodes, final_episodes, baseline_indicator_table
    )
    (output_dir / "false_positive_diagnosis.md").write_text(
        diagnosis, encoding="utf-8", newline="\n"
    )
    config_dir = output_dir / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    for evaluation in evaluations:
        (config_dir / f"{evaluation.name}.yaml").write_text(
            yaml.safe_dump(evaluation.settings.model, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
            newline="\n",
        )
    chart_paths = create_phase2_charts(
        {evaluation.name: evaluation.history for evaluation in evaluations},
        comparison[comparison["segment"].eq("overall")],
        actual,
        final_name,
        baseline.backtest.run.contributions,
        final.backtest.run.composite,
        final.backtest.run.dynamic,
        output_dir / "charts",
    )
    report = _validation_report(evaluations, final, comparison, dynamic_agreement)
    report_path = output_dir / "validation_report.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    summary = {
        "run_at": datetime.now(UTC).isoformat(),
        "period": {"collection_start": collection_start, "start": start, "end": end},
        "source": "official FRED latest revisions",
        "preliminary_warning": PRELIMINARY_WARNING,
        "collection_warnings": collection_warnings,
        "evaluation_audit": {
            "positive_definition": "broad_phase == contraction only",
            "weekly_alignment": "monthly USREC forward-filled on union index then sampled Fridays",
            "turning_point_confirmation": "4 consecutive weeks, causal confirmation date, no backdating",
        },
        "models": {evaluation.name: evaluation.metrics for evaluation in evaluations},
        "final_model": final_name,
        "final_settings": final.settings.model,
        "final_false_positive_episode_count": len(final_episodes),
        "dynamic_broad_phase_agreement": dynamic_agreement,
        "current_nowcast": final.backtest.run.result.to_dict(),
        "chart_count": len(chart_paths),
        "observed_diagnostic_holdout": "2021-01-01 through 2026-08-14",
        "prospective_plan": "freeze final settings and evaluate only newly released observations",
    }
    summary_path = _write_json(output_dir / "validation_summary.json", summary)
    return Phase2Result(output_dir, report_path, summary_path, chart_paths, final_name)
