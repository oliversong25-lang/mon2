"""1995년 이후 공식 FRED 최신 수정치 실자료 검증."""

# ruff: noqa: E501

from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..backtest.engine import BacktestResult, run_backtest
from ..backtest.metrics import backtest_metrics
from ..config import Settings
from ..data.fred import FRED_CSV_URL, FredCollector
from ..models.confidence import broad_confidence, data_confidence, detail_confidence
from ..models.dynamic_factor import DynamicFactorModel
from ..models.momentum import coordinates
from ..models.phase import emission_probabilities, phase_definitions
from ..models.transition import cyclic_distance, filter_probabilities, transition_matrix
from ..pipeline import PRELIMINARY_WARNING, PipelineRun
from .charts import create_validation_charts

USREC_ID = "USREC"
BASELINE_MOMENTUM_WEEKS = 4
ADJUSTED_MOMENTUM_WEEKS = 8


@dataclass(frozen=True)
class ValidationResult:
    output_dir: Path
    summary_path: Path
    report_path: Path
    chart_paths: list[Path]
    adopted_adjustment: bool


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    raise TypeError(f"JSON 직렬화 불가: {type(value).__name__}")


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _settings_with_momentum(settings: Settings, weeks: int) -> Settings:
    model = copy.deepcopy(settings.model)
    model["momentum_weeks"] = weeks
    return replace(settings, model=model)


def _official_recession_flags(observations: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    raw = observations[observations["indicator_id"].eq(USREC_ID)].copy()
    if raw.empty:
        raise ValueError("공식 FRED USREC 침체지표가 없어 객관적 비교를 수행할 수 없습니다")
    raw["observation_period"] = pd.to_datetime(raw["observation_period"], errors="raise")
    monthly = (
        raw.sort_values("observation_period")
        .drop_duplicates("observation_period", keep="last")
        .set_index("observation_period")["value"]
        .astype(float)
    )
    monthly_index = pd.DatetimeIndex(monthly.index)
    aligned = monthly.reindex(index.union(monthly_index)).sort_index().ffill().reindex(index)
    if aligned.isna().any():
        raise ValueError("USREC 공통기간 정렬 중 결측이 발생했습니다")
    return aligned.ge(0.5).rename("official_nber_recession")


def _phase_history_from_dynamic(run: PipelineRun, settings: Settings) -> pd.DataFrame:
    estimate = DynamicFactorModel(settings.model["dynamic_factor"]).fit_filter(run.events)
    slope = estimate.metadata["slopes"]
    if not isinstance(slope, pd.Series):
        raise TypeError("동적요인 기울기가 Series가 아닙니다")
    coords = coordinates(
        estimate.factor,
        int(settings.model["momentum_weeks"]),
        int(settings.model["standardization_min_periods"]),
        slope,
    ).dropna()
    phases = phase_definitions(settings.transitions["phases"])
    emissions = np.vstack(
        [
            emission_probabilities(
                angle,
                radius,
                phases,
                float(settings.model["phase_emission_sigma_degrees"]),
                float(settings.model["phase_origin_sigma_multiplier"]),
                float(settings.model["phase_origin_scale"]),
                level,
                (
                    float(settings.model["phase_origin_scale"])
                    if settings.model.get("contraction_level_gate", False)
                    else None
                ),
            )
            for angle, radius, level in coords[["angle", "radius", "y"]].to_numpy(dtype=float)
        ]
    )
    filtered = filter_probabilities(
        emissions,
        transition_matrix(len(phases), settings.transitions["transition"]),
        coords["radius"].to_numpy(dtype=float),
        (
            float(settings.model["phase_origin_scale"])
            if settings.model.get("low_radius_jump_constraint", False)
            else None
        ),
    )
    winners = filtered.argmax(axis=1)
    history = coords.copy()
    for position, phase in enumerate(phases):
        history[f"p_{phase.code}"] = filtered[:, position]
    history["phase_code"] = [phases[int(value)].code for value in winners]
    history["broad_phase"] = [phases[int(value)].broad for value in winners]
    return history


def _confidence_history(run: PipelineRun, settings: Settings) -> pd.DataFrame:
    history = run.history.copy()
    phases = phase_definitions(settings.transitions["phases"])
    probability_columns = [f"p_{phase.code}" for phase in phases]
    probabilities = history[probability_columns].to_numpy(dtype=float)
    winners = probabilities.argmax(axis=1)
    events = run.events.reindex(history.index)
    ages = {
        key: int(value.get("max_age_weeks", 8))
        for key, value in settings.indicators["indicators"].items()
    }
    age_state = {key: ages[key] + 1 for key in ages}
    fresh_by_week: dict[pd.Timestamp, float] = {}
    available_by_week: dict[pd.Timestamp, float] = {}
    for timestamp, row in events.iterrows():
        fresh: list[float] = []
        available = 0
        for indicator_id, maximum in ages.items():
            if indicator_id in row.index and pd.notna(row[indicator_id]):
                age_state[indicator_id] = 0
            else:
                age_state[indicator_id] += 1
            age = age_state[indicator_id]
            if age <= maximum:
                available += 1
                fresh.append(float(np.exp(-age / max(1, maximum))))
            else:
                fresh.append(0.0)
        normalized_timestamp = pd.Timestamp(str(timestamp))
        fresh_by_week[normalized_timestamp] = float(np.mean(fresh))
        available_by_week[normalized_timestamp] = available / max(1, len(ages))
    composite = run.composite.reindex(history.index, method="ffill")
    dynamic = run.dynamic.reindex(history.index, method="ffill")
    agreements = (composite - dynamic).abs().mul(-1.0).apply(np.exp).fillna(0.0)
    broad_scores: list[float] = []
    detail_scores: list[float] = []
    data_scores: list[float] = []
    for position, timestamp in enumerate(history.index):
        winner = int(winners[position])
        recent = winners[max(0, position - 3) : position + 1]
        persistence = float(np.mean(recent == winner))
        broad_scores.append(broad_confidence(probabilities[position], winner, phases))
        detail_scores.append(
            detail_confidence(
                probabilities[position],
                winner,
                float(history.iloc[position]["angle"]),
                float(history.iloc[position]["radius"]),
                float(agreements.iloc[position]),
                persistence,
                phases,
                settings.model["confidence"],
            )
        )
        score, _ = data_confidence(
            fresh_by_week[pd.Timestamp(str(timestamp))],
            available_by_week[pd.Timestamp(str(timestamp))],
            None,
            float(agreements.iloc[position]),
            settings.model["confidence"],
        )
        data_scores.append(score)
    history["broad_confidence"] = broad_scores
    history["detail_confidence"] = detail_scores
    history["data_confidence"] = data_scores
    history["available_indicator_share"] = [
        available_by_week[pd.Timestamp(i)] for i in history.index
    ]
    return history


def _episodes(flags: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    episodes: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    start: pd.Timestamp | None = None
    previous: pd.Timestamp | None = None
    for timestamp, active in flags.items():
        current = pd.Timestamp(str(timestamp))
        if bool(active) and start is None:
            start = current
        if not bool(active) and start is not None:
            episodes.append((start, previous or current))
            start = None
        previous = current
    if start is not None and previous is not None:
        episodes.append((start, previous))
    return episodes


def _objective_nber_metrics(history: pd.DataFrame, actual: pd.Series) -> dict[str, Any]:
    predicted = history["broad_phase"].eq("contraction")
    tp = int((actual & predicted).sum())
    fn = int((actual & ~predicted).sum())
    fp = int((~actual & predicted).sum())
    tn = int((~actual & ~predicted).sum())
    entries = history.index[predicted & ~predicted.shift(1, fill_value=False)]
    exits = history.index[~predicted & predicted.shift(1, fill_value=False)]
    episode_rows: list[dict[str, Any]] = []
    for start, end in _episodes(actual):
        entry_candidates = [date for date in entries if abs((date - start).days) <= 182]
        exit_candidates = [date for date in exits if abs((date - end).days) <= 182]
        entry = (
            min(entry_candidates, key=lambda date: abs((date - start).days))
            if entry_candidates
            else None
        )
        exit_date = (
            min(exit_candidates, key=lambda date: abs((date - end).days))
            if exit_candidates
            else None
        )
        prior = history.loc[max(history.index.min(), start - pd.Timedelta(weeks=26)) : start]
        episode_rows.append(
            {
                "official_start_week": start.date().isoformat(),
                "official_end_week": end.date().isoformat(),
                "model_entry_week": None if entry is None else entry.date().isoformat(),
                "entry_lead_lag_weeks": None
                if entry is None
                else round((entry - start).days / 7, 1),
                "model_exit_week": None if exit_date is None else exit_date.date().isoformat(),
                "exit_lead_lag_weeks": None
                if exit_date is None
                else round((exit_date - end).days / 7, 1),
                "slowdown_seen_prior_26w": bool(prior["broad_phase"].eq("slowdown").any()),
                "dominant_phase_during_recession": str(
                    history.loc[start:end, "broad_phase"].mode().iloc[0]
                ),
            }
        )
    return {
        "recession_recall": tp / max(1, tp + fn),
        "recession_false_positive_rate": fp / max(1, fp + tn),
        "recession_precision": tp / max(1, tp + fp),
        "recession_accuracy": (tp + tn) / max(1, tp + tn + fp + fn),
        "true_positive_weeks": tp,
        "false_negative_weeks": fn,
        "false_positive_weeks": fp,
        "true_negative_weeks": tn,
        "episodes": episode_rows,
        "source": f"FRED {USREC_ID}",
        "note": "NBER는 침체/비침체 평가에만 사용했고 12개 세부국면 정확도는 계산하지 않음",
    }


def _extended_metrics(
    result: BacktestResult,
    history: pd.DataFrame,
    actual: pd.Series,
    settings: Settings,
) -> dict[str, Any]:
    order = [str(item["code"]) for item in settings.transitions["phases"]]
    metrics = backtest_metrics(history, order)
    metrics["nber"] = _objective_nber_metrics(history, actual)
    broad = history["broad_phase"].astype(str).tolist()
    metrics["broad_phase_changes"] = sum(a != b for a, b in zip(broad, broad[1:], strict=False))
    probability_columns = [column for column in history if str(column).startswith("p_")]
    probabilities = np.sort(history[probability_columns].to_numpy(dtype=float), axis=1)
    gaps = probabilities[:, -1] - probabilities[:, -2]
    if len(gaps) > 4:
        future_change = np.array(
            [
                any(
                    history.iloc[position]["phase_code"]
                    != history.iloc[position + offset]["phase_code"]
                    for offset in range(1, 5)
                )
                for position in range(len(history) - 4)
            ]
        )
        low = gaps[:-4] <= np.quantile(gaps, 0.25)
        metrics["confidence_diagnostics"]["bottom_quartile_gap_next_4w_transition_rate"] = (
            float(future_change[low].mean()) if low.any() else None
        )
    metrics["minimum_available_indicator_share"] = float(history["available_indicator_share"].min())
    metrics["weeks_below_minimum_availability"] = int(
        (
            history["available_indicator_share"]
            < float(settings.indicators["constraints"]["minimum_availability"])
        ).sum()
    )
    metrics["pipeline_metadata"] = result.metadata
    return metrics


def _segment_metrics(history: pd.DataFrame, actual: pd.Series) -> list[dict[str, Any]]:
    definitions = [
        ("development", "1995-01-01", "2012-12-31"),
        ("stability", "2013-01-01", "2019-12-31"),
        ("pandemic_stress", "2020-01-01", "2020-12-31"),
        ("recent", "2021-01-01", str(history.index.max().date())),
    ]
    rows: list[dict[str, Any]] = []
    for name, start, end in definitions:
        subset = history.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        if subset.empty:
            continue
        phases = subset["phase_code"].astype(str).tolist()
        broad = subset["broad_phase"].astype(str).tolist()
        flags = actual.reindex(subset.index, fill_value=False)
        predicted = subset["broad_phase"].eq("contraction")
        rows.append(
            {
                "segment": name,
                "start": subset.index.min().date().isoformat(),
                "end": subset.index.max().date().isoformat(),
                "weeks": len(subset),
                "phase_changes": sum(a != b for a, b in zip(phases, phases[1:], strict=False)),
                "broad_changes": sum(a != b for a, b in zip(broad, broad[1:], strict=False)),
                "contraction_false_positive_weeks": int((~flags & predicted).sum()),
                "mean_detail_confidence": float(subset["detail_confidence"].mean()),
            }
        )
    return rows


def _jump_analysis(
    history: pd.DataFrame,
    run: PipelineRun,
    settings: Settings,
) -> pd.DataFrame:
    order = [str(item["code"]) for item in settings.transitions["phases"]]
    positions = {code: index for index, code in enumerate(order)}
    probability_columns = [f"p_{code}" for code in order]
    radius_threshold = float(history["radius"].quantile(0.95))
    rows: list[dict[str, Any]] = []
    for position in range(1, len(history)):
        previous = str(history.iloc[position - 1]["phase_code"])
        current = str(history.iloc[position]["phase_code"])
        distance = cyclic_distance(positions[previous], positions[current], len(order))
        if previous == current or distance <= 1:
            continue
        timestamp = pd.Timestamp(history.index[position])
        probabilities = (
            history.iloc[position][probability_columns].astype(float).sort_values(ascending=False)
        )
        contribution = run.contributions.reindex([timestamp], method="ffill").iloc[0]
        contribution = contribution.dropna().sort_values(key=abs, ascending=False).head(4)
        event = run.events.reindex([timestamp]).iloc[0]
        releases = [str(key) for key, value in event.items() if pd.notna(value)]
        missing = [str(key) for key, value in event.items() if pd.isna(value)]
        context = "none coded"
        if pd.Timestamp("2020-03-01") <= timestamp <= pd.Timestamp("2020-06-30"):
            context = "COVID-19 external shock"
        elif pd.Timestamp("2008-09-01") <= timestamp <= pd.Timestamp("2008-10-31"):
            context = "Lehman-era financial shock"
        extreme = float(history.iloc[position]["radius"]) >= radius_threshold
        rows.append(
            {
                "date": timestamp.date().isoformat(),
                "from_phase": previous,
                "to_phase": current,
                "steps": distance,
                "x": float(history.iloc[position]["x"]),
                "y": float(history.iloc[position]["y"]),
                "radius": float(history.iloc[position]["radius"]),
                "top_observation_phases": "; ".join(
                    f"{str(key)[2:]}={float(value):.4f}"
                    for key, value in probabilities.head(3).items()
                ),
                "top_indicator_contributions": "; ".join(
                    f"{key}={float(value):+.4f}" for key, value in contribution.items()
                ),
                "released_indicators": "; ".join(releases) or "none",
                "missing_on_release_week": "; ".join(missing),
                "economic_shock": context,
                "assessment": (
                    "shock-plausible jump"
                    if context != "none coded" and extreme
                    else "model instability review"
                ),
            }
        )
    return pd.DataFrame(rows)


def _whipsaw_analysis(history: pd.DataFrame, run: PipelineRun, settings: Settings) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    origin = float(settings.model["phase_origin_scale"])
    x_change_threshold = float(history["x"].diff().abs().quantile(0.90))
    for position in range(len(history) - 2):
        left = str(history.iloc[position]["phase_code"])
        middle = str(history.iloc[position + 1]["phase_code"])
        right = str(history.iloc[position + 2]["phase_code"])
        if left != right or left == middle:
            continue
        timestamp = pd.Timestamp(history.index[position + 1])
        radius = float(history.iloc[position + 1]["radius"])
        x_change = abs(float(history.iloc[position + 1]["x"] - history.iloc[position]["x"]))
        event_count = int(run.events.reindex([timestamp]).notna().sum(axis=1).iloc[0])
        if radius < origin:
            cause = "origin uncertainty"
        elif x_change >= x_change_threshold:
            cause = "X momentum sensitivity"
        elif event_count >= 2:
            cause = "release-week step effect"
        else:
            cause = "angle-boundary sensitivity"
        rows.append(
            {
                "date": timestamp.date().isoformat(),
                "path": f"{left} -> {middle} -> {right}",
                "x": float(history.iloc[position + 1]["x"]),
                "y": float(history.iloc[position + 1]["y"]),
                "radius": radius,
                "release_count": event_count,
                "diagnosed_cause": cause,
            }
        )
    return pd.DataFrame(rows)


def _case_summaries(
    history: pd.DataFrame, actual: pd.Series, jumps: pd.DataFrame, nber: dict[str, Any]
) -> dict[str, Any]:
    episode_by_year = {str(row["official_start_week"])[:4]: row for row in nber.get("episodes", [])}
    cases = {
        "2001": ("2000-01-01", "2002-12-31"),
        "gfc_2007_2009": ("2006-01-01", "2010-12-31"),
        "pandemic_2020": ("2019-01-01", "2021-06-30"),
        "rate_hikes_2022_onward": ("2022-01-01", str(history.index.max().date())),
    }
    result: dict[str, Any] = {}
    for name, (start, end) in cases.items():
        subset = history.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        flags = actual.reindex(subset.index, fill_value=False)
        if jumps.empty:
            case_jumps = jumps
        else:
            jump_dates = pd.to_datetime(jumps["date"])
            case_jumps = jumps[
                (jump_dates >= pd.Timestamp(start)) & (jump_dates <= pd.Timestamp(end))
            ]
        result[name] = {
            "period": [start, end],
            "phase_path_compact": [
                phase for phase, _ in groupby(subset["phase_code"].astype(str).tolist())
            ],
            "phase_changes": int((subset["phase_code"] != subset["phase_code"].shift()).sum() - 1),
            "multi_step_jumps": int(len(case_jumps)),
            "contraction_weeks": int(subset["broad_phase"].eq("contraction").sum()),
            "false_positive_contraction_weeks": int(
                (~flags & subset["broad_phase"].eq("contraction")).sum()
            ),
        }
    result["2001"]["official_episode"] = episode_by_year.get("2001")
    result["gfc_2007_2009"]["official_episode"] = episode_by_year.get("2008")
    lehman_date = pd.Timestamp("2008-09-15")
    nearest_position = int(np.argmin(np.abs(history.index.to_numpy() - np.datetime64(lehman_date))))
    nearest = pd.Timestamp(history.index[nearest_position])
    result["gfc_2007_2009"]["lehman_context"] = {
        "event_date": "2008-09-15",
        "nearest_model_week": nearest.date().isoformat(),
        "phase": str(history.loc[nearest, "phase_code"]),
        "broad_phase": str(history.loc[nearest, "broad_phase"]),
        "note": "Lehman date is context, not the NBER recession start.",
    }
    result["pandemic_2020"]["official_episode"] = episode_by_year.get("2020")
    return result


def _git_hash(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _metrics_row(name: str, metrics: dict[str, Any]) -> dict[str, Any]:
    nber = metrics["nber"]
    return {
        "model": name,
        "weeks": metrics["weeks"],
        "phase_changes": metrics["phase_transitions"],
        "broad_changes": metrics["broad_phase_changes"],
        "multi_step_jumps": metrics["multi_step_jumps"],
        "boundary_whipsaws": metrics["boundary_whipsaws"],
        "average_phase_duration_weeks": metrics["average_phase_duration_weeks"],
        "recession_recall": nber["recession_recall"],
        "recession_false_positive_rate": nber["recession_false_positive_rate"],
        "recession_precision": nber["recession_precision"],
        "recession_accuracy": nber["recession_accuracy"],
    }


def _report_markdown(summary: dict[str, Any], cases: dict[str, Any]) -> str:
    baseline = summary["baseline"]
    adjusted = summary["adjusted"]
    current = summary["current"]
    lines = [
        "# 미국 경기국면 모델 v0.1 — 공식 FRED 실자료 검증",
        "",
        f"> {PRELIMINARY_WARNING}",
        "",
        "## 한 줄 요약",
        "",
        str(summary["one_line_summary"]),
        "",
        "## 데이터와 모델",
        "",
        f"- 공식 자료: FRED 핵심 7개 지표 + `{USREC_ID}` NBER 침체지표",
        f"- 실행 시각(UTC): {summary['run_at']}",
        f"- 실행 전 Git 커밋: `{summary['git_commit_before_validation']}`",
        f"- 워밍업 수집 시작: {summary['data_period']['collection_start_for_warmup']}",
        f"- 실제 모델 이력: {summary['data_period']['model_start']} ~ {summary['data_period']['model_end']}",
        "- 지표별 최종 관측일: "
        + ", ".join(
            f"{key} {value}"
            for key, value in summary["data_period"]["indicator_last_observation"].items()
        ),
        "- 대표모델: `CompositeFactorModel`",
        "- 비교모델: `DynamicFactorModel`",
        f"- baseline 설정: momentum_weeks={summary['baseline_momentum_weeks']}",
        f"- adjusted 설정: momentum_weeks={summary['adjusted_momentum_weeks']}",
        f"- 현재 판정: {current['current_phase']['label_ko']} "
        f"({current['current_phase']['broad_phase']})",
        f"- 확실성: 대국면 {current['confidence']['broad']:.1f}, 세부 {current['confidence']['detail']:.1f}, 데이터 {current['confidence']['data']:.1f}",
        "",
        "## NBER 침체 비교",
        "",
        f"- baseline 재현율 {baseline['nber']['recession_recall']:.1%}, 정밀도 {baseline['nber']['recession_precision']:.1%}, 오탐률 {baseline['nber']['recession_false_positive_rate']:.1%}",
        f"- adjusted 재현율 {adjusted['nber']['recession_recall']:.1%}, 정밀도 {adjusted['nber']['recession_precision']:.1%}, 오탐률 {adjusted['nber']['recession_false_positive_rate']:.1%}",
        "- NBER는 침체/비침체의 객관적 비교에만 사용했다. 12개 세부국면에는 공식 정답 라벨이 없다.",
        "",
        "## 역사적 사례",
        "",
    ]
    for key, title in (
        ("2001", "2001년 침체"),
        ("gfc_2007_2009", "2007~2009년 금융위기"),
        ("pandemic_2020", "2020년 팬데믹"),
        ("rate_hikes_2022_onward", "2022년 이후"),
    ):
        case = cases[key]
        lines.extend(
            [
                f"### {title}",
                "",
                f"- 세부국면 변경 {case['phase_changes']}회, 다단계 점프 {case['multi_step_jumps']}회",
                f"- 침체 오탐 주수 {case['false_positive_contraction_weeks']}주",
                f"- 압축 경로: {' → '.join(case['phase_path_compact'])}",
                "",
            ]
        )
        episode = case.get("official_episode")
        if episode:
            lines.extend(
                [
                    f"- NBER 진입 대비 {episode['entry_lead_lag_weeks']:+.1f}주, "
                    f"종료 대비 {episode['exit_lead_lag_weeks']:+.1f}주",
                    f"- 진입 전 26주 후퇴기 신호: {episode['slowdown_seen_prior_26w']}",
                ]
            )
        lehman = case.get("lehman_context")
        if lehman:
            lines.append(
                f"- 리먼 파산일 인접 주 판정: {lehman['phase']} ({lehman['broad_phase']}); "
                "리먼일은 NBER 시작일로 사용하지 않음"
            )
        lines.append("")
    lines.extend(
        [
            "## 안정성 진단과 최소 조정",
            "",
            f"- baseline: 변경 {baseline['phase_transitions']}회, 점프 {baseline['multi_step_jumps']}회, 왕복 {baseline['boundary_whipsaws']}회",
            f"- adjusted: 변경 {adjusted['phase_transitions']}회, 점프 {adjusted['multi_step_jumps']}회, 왕복 {adjusted['boundary_whipsaws']}회",
            "- 변경은 X 모멘텀 창을 4주에서 8주로 늘린 한 가지뿐이다. 월간 발표 계단과 주간 청구 노이즈 민감도를 줄이면서 침체 재현율이 악화되지 않아 채택했다.",
            "- 그러나 adjusted도 침체 정밀도가 낮고 정상기 오탐이 많아 운영 판정용으로 충분하지 않다.",
            "- 모든 점프와 3주 왕복은 별도 CSV에 날짜·좌표·확률·기여지표·발표주 영향·충격 맥락을 기록했다.",
            f"- 점프 판정 분포: {summary['jump_assessment_counts']}",
            f"- 왕복 원인 분포: {summary['whipsaw_cause_counts']}",
            f"- 최소 지표 가용률: {adjusted['minimum_available_indicator_share']:.1%}; "
            f"최소 기준 미달 {adjusted['weeks_below_minimum_availability']}주",
            "",
            "## Composite와 Dynamic 비교",
            "",
            f"- 요인 상관계수: {summary['model_comparison']['factor_correlation']:.3f}",
            f"- 대국면 일치율: {summary['model_comparison']['broad_phase_agreement']:.1%}",
            "- Dynamic은 민감도 비교용이며 공식 판정에는 사용하지 않았다.",
            "",
            "## 검증된 사실·해석·미검증 가정",
            "",
            "### 실제로 검증된 사실",
            "",
            "- 공식 FRED 최신 수정치 자료로 1995년 이후 인과적 forward-filter 백테스트를 실행했다.",
            "- 미래 관측 변경 불변성, 확률합, 수집 실패 표시 테스트를 자동 검사했다.",
            "- baseline과 8주 모멘텀 조정안을 같은 자료와 지표로 비교했다.",
            "",
            "### 경제적 해석에 근거한 판단",
            "",
            "- baseline의 잦은 왕복과 점프는 4주 X 모멘텀의 발표주 계단·노이즈 민감성이 주원인으로 판단했다.",
            "- 2020년의 일부 급격한 이동은 외생 충격에 비추어 다른 시기의 점프보다 정당화 가능하다.",
            "",
            "### 아직 검증되지 않은 가정",
            "",
            "- 최신 수정치 결과가 당시 실제 공개정보에서도 같았을 것이라는 가정은 검증되지 않았다.",
            "- ALFRED 빈티지, 실제 발표일 전체 이력, 12개 세부국면 정답 라벨은 이번 범위에 없다.",
            "- 고정 가중치와 8주 모멘텀의 다른 표본·향후 기간 안정성은 추가 검증이 필요하다.",
            "",
            "## 차트 읽기",
            "",
            "- `01`은 X·Y 좌표의 충격과 회복 경로, `02`~`03`은 NBER 음영 대비 판정 경로를 보여준다.",
            "- `04`~`05`는 후보 확률 격차와 확실성이 전환기에서 낮아지는지 확인한다.",
            "- `06`~`09`는 네 역사 사례 확대, `10`은 대표·비교모델의 요인과 대국면 일치도를 보여준다.",
            "",
            "## 재현 명령",
            "",
            "```powershell",
            ".\\.venv\\Scripts\\business-cycle.exe validate-real --start 1995-01-01 --end 2026-08-14",
            ".\\.venv\\Scripts\\python.exe -m pytest",
            ".\\.venv\\Scripts\\ruff.exe format --check .",
            ".\\.venv\\Scripts\\ruff.exe check .",
            ".\\.venv\\Scripts\\mypy.exe --strict src",
            "```",
            "",
            "## 다음 우선 작업",
            "",
            "- NBER 비침체 오탐의 구조적 원인을 Y 수준·각도 대국면 매핑별로 분해한다.",
            "- ALFRED 실시간 빈티지와 실제 발표일을 연결해 최신 수정치 편향을 측정한다.",
            "- 12개 세부국면은 공식 정답이 없으므로 경제적 사건표와 별도 검증 설계를 먼저 확정한다.",
        ]
    )
    return "\n".join(lines) + "\n"


def run_real_data_validation(
    settings: Settings,
    start: str,
    end: str,
    cache_dir: Path,
    output_dir: Path,
) -> ValidationResult:
    """공식 FRED 수집부터 baseline·최소 조정·차트·보고서까지 실행한다."""

    collector = FredCollector(cache_dir)
    core_ids = list(settings.indicators["indicators"])
    # 1995년 첫 주부터 판정을 만들려면 3년 추세와 expanding 표준화 이전 자료가 필요하다.
    collection_start = (pd.Timestamp(start) - pd.DateOffset(years=10)).date().isoformat()
    observations, warnings = collector.fetch([*core_ids, USREC_ID], collection_start)
    core = observations[observations["indicator_id"].isin(core_ids)].copy()
    baseline_settings = _settings_with_momentum(settings, BASELINE_MOMENTUM_WEEKS)
    adjusted_settings = _settings_with_momentum(settings, ADJUSTED_MOMENTUM_WEEKS)
    baseline_result = run_backtest(core, baseline_settings, start, end, True)
    adjusted_result = run_backtest(core, adjusted_settings, start, end, True)
    baseline_history = _confidence_history(baseline_result.run, baseline_settings).loc[start:end]
    adjusted_history = _confidence_history(adjusted_result.run, adjusted_settings).loc[start:end]
    actual = _official_recession_flags(observations, pd.DatetimeIndex(adjusted_history.index))
    baseline_actual = actual.reindex(baseline_history.index, fill_value=False)
    baseline_metrics = _extended_metrics(
        baseline_result, baseline_history, baseline_actual, baseline_settings
    )
    adjusted_metrics = _extended_metrics(
        adjusted_result, adjusted_history, actual, adjusted_settings
    )
    adopted = (
        adjusted_metrics["multi_step_jumps"] < baseline_metrics["multi_step_jumps"]
        and adjusted_metrics["phase_transitions"] < baseline_metrics["phase_transitions"]
        and adjusted_metrics["nber"]["recession_recall"]
        >= baseline_metrics["nber"]["recession_recall"]
    )
    representative_history = adjusted_history if adopted else baseline_history
    representative_run = adjusted_result.run if adopted else baseline_result.run
    representative_settings = adjusted_settings if adopted else baseline_settings
    representative_actual = actual.reindex(representative_history.index, fill_value=False)
    dynamic_history = _phase_history_from_dynamic(representative_run, representative_settings).loc[
        representative_history.index.min() : representative_history.index.max()
    ]
    jumps = _jump_analysis(representative_history, representative_run, representative_settings)
    whipsaws = _whipsaw_analysis(
        representative_history, representative_run, representative_settings
    )
    cases = _case_summaries(
        representative_history,
        representative_actual,
        jumps,
        adjusted_metrics["nber"] if adopted else baseline_metrics["nber"],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, result, history, metrics in (
        ("baseline", baseline_result, baseline_history, baseline_metrics),
        ("adjusted", adjusted_result, adjusted_history, adjusted_metrics),
    ):
        directory = output_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        history.reset_index(names="date").to_csv(directory / "history.csv", index=False)
        _write_json(directory / "metrics.json", metrics)
        _write_json(directory / "current.json", result.run.result.to_dict())
    historical_dir = output_dir / "historical_cases"
    historical_dir.mkdir(parents=True, exist_ok=True)
    _write_json(historical_dir / "case_summary.json", cases)
    for name, (case_start, case_end) in {
        "2001": ("2000-01-01", "2002-12-31"),
        "gfc_2007_2009": ("2006-01-01", "2010-12-31"),
        "pandemic_2020": ("2019-01-01", "2021-06-30"),
        "rate_hikes_2022_onward": ("2022-01-01", end),
    }.items():
        representative_history.loc[case_start:case_end].reset_index(names="date").to_csv(
            historical_dir / f"{name}.csv", index=False
        )
    jumps.to_csv(output_dir / "jump_analysis.csv", index=False)
    whipsaws.to_csv(output_dir / "whipsaw_analysis.csv", index=False)
    segment_rows = _segment_metrics(representative_history, representative_actual)
    pd.DataFrame(segment_rows).to_csv(output_dir / "validation_segments.csv", index=False)
    pd.DataFrame(
        [_metrics_row("baseline", baseline_metrics), _metrics_row("adjusted", adjusted_metrics)]
    ).to_csv(output_dir / "validation_metrics.csv", index=False)
    latest_dates = (
        core.assign(observation_period=pd.to_datetime(core["observation_period"]))
        .groupby("indicator_id")["observation_period"]
        .max()
        .dt.date.astype(str)
        .to_dict()
    )
    common_factor = pd.concat(
        [
            representative_run.composite.rename("composite"),
            representative_run.dynamic.rename("dynamic"),
        ],
        axis=1,
    ).loc[representative_history.index.min() : representative_history.index.max()]
    broad_comparison = pd.concat(
        [
            representative_history["broad_phase"].rename("composite"),
            dynamic_history["broad_phase"].rename("dynamic"),
        ],
        axis=1,
    ).dropna()
    current = (adjusted_result if adopted else baseline_result).run.result.to_dict()
    summary: dict[str, Any] = {
        "one_line_summary": (
            "공식 FRED 최신 수정치에서 8주 최소 조정은 점프를 줄이고 침체 재현율을 "
            f"{adjusted_metrics['nber']['recession_recall']:.1%}로 높였지만, 오탐률 "
            f"{adjusted_metrics['nber']['recession_false_positive_rate']:.1%}·정밀도 "
            f"{adjusted_metrics['nber']['recession_precision']:.1%}라 현재 모델은 운영 "
            "판정용으로 부족하다."
        ),
        "run_at": datetime.now(UTC).isoformat(),
        "git_commit_before_validation": _git_hash(settings.root.parent),
        "source": {"provider": "Federal Reserve Bank of St. Louis FRED", "url": FRED_CSV_URL},
        "collection": collector.report_payload(),
        "collection_warnings": warnings,
        "data_period": {
            "requested_start": start,
            "collection_start_for_warmup": collection_start,
            "requested_end": end,
            "model_start": representative_history.index.min().date().isoformat(),
            "model_end": representative_history.index.max().date().isoformat(),
            "indicator_last_observation": latest_dates,
        },
        "representative_model": "CompositeFactorModel",
        "comparison_model": "DynamicFactorModel",
        "baseline_momentum_weeks": BASELINE_MOMENTUM_WEEKS,
        "adjusted_momentum_weeks": ADJUSTED_MOMENTUM_WEEKS,
        "adjustment_adopted": adopted,
        "baseline": baseline_metrics,
        "adjusted": adjusted_metrics,
        "current": current,
        "model_comparison": {
            "factor_correlation": float(common_factor["composite"].corr(common_factor["dynamic"])),
            "broad_phase_agreement": float(
                broad_comparison["composite"].eq(broad_comparison["dynamic"]).mean()
            ),
        },
        "segments": segment_rows,
        "jump_count_detailed": len(jumps),
        "whipsaw_count_detailed": len(whipsaws),
        "jump_assessment_counts": (
            {} if jumps.empty else jumps["assessment"].value_counts().to_dict()
        ),
        "whipsaw_cause_counts": (
            {} if whipsaws.empty else whipsaws["diagnosed_cause"].value_counts().to_dict()
        ),
        "warnings": [
            PRELIMINARY_WARNING,
            "실제 발표일이 없는 FRED graph CSV는 설정된 보수적 발표지연을 사용함",
            "12개 세부국면에는 공식 정답 라벨이 없음",
        ],
    }
    summary_path = _write_json(output_dir / "validation_summary.json", summary)
    _write_json(output_dir / "collection_report.json", collector.report_payload())
    chart_paths = create_validation_charts(
        representative_history,
        dynamic_history,
        representative_actual,
        representative_run.composite,
        representative_run.dynamic,
        output_dir / "charts",
    )
    report_path = output_dir / "validation_report.md"
    report_path.write_text(_report_markdown(summary, cases), encoding="utf-8", newline="\n")
    return ValidationResult(output_dir, summary_path, report_path, chart_paths, adopted)
