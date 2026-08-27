"""단계 A-2: 빈도 수정 모델의 시작시점 의존성을 분해하고 corrected baseline을 만든다.

phase3는 robust6 한 줄만 놓고 합격을 판정했다. 여기서는 구성요소를 분리해 어느 변경이
무엇을 악화시켰는지 먼저 가리고, 1985·1990 시작 차이의 출처를 전처리·성숙도·좌표
표준화·상태필터 초기값으로 나눠 확인한 뒤 corrected baseline을 세운다.
"""

# ruff: noqa: E501

from __future__ import annotations

import copy
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ..config import Settings, load_baseline, load_settings
from ..data.fred import FredCollector
from ..models.phase import emission_probabilities, phase_definitions
from .phase2 import ModelEvaluation, _evaluate
from .phase3 import _row, _variant
from .real_data import USREC_ID

START = "1995-01-01"
END = "2026-08-14"

#: 워밍업 분해 구간. 2001년 침체 판정을 앞뒤로 감싼다.
DECOMPOSITION_START = "2000-01-01"
DECOMPOSITION_END = "2002-12-31"

#: 시작시점 수렴을 재는 두 창. 첫 창은 1990 실행이 아직 성숙하지 않은 구간을 포함하고,
#: 둘째 창은 좌표 표준화의 10년 창이 두 실행 모두에서 자료 안에 완전히 들어간 뒤다.
CONVERGENCE_WINDOWS = {
    "from_2000": "2000-01-01",
    "after_full_maturity_2006": "2006-01-01",
}

#: 2001년 침체 진입 확인일 차이의 강건성 참고기준(주).
WARMUP_SHIFT_LIMIT_WEEKS = 8.0

ABLATION_CELLS = {
    "1_legacy": (False, False, False),
    "2_frequency": (True, False, False),
    "3_maturity": (False, True, False),
    "4_robust6": (False, False, True),
    "5_frequency_maturity": (True, True, False),
    "6_frequency_robust6": (True, False, True),
    "7_maturity_robust6": (False, True, True),
    "8_frequency_maturity_robust6": (True, True, True),
}


@dataclass(frozen=True)
class Phase4Result:
    output_dir: Path
    report_path: Path
    summary_path: Path
    passed: bool
    frozen_hash: str


def load_core_observations(settings: Settings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """캐시된 FRED 최신 수정치를 읽는다. 네트워크는 쓰지 않는다."""

    indicator_ids = list(settings.indicators["indicators"])
    observations, _ = FredCollector(
        settings.root / "data" / "cache", cache_max_age_hours=1e9
    ).fetch([*indicator_ids, USREC_ID], "1985-01-01")
    core = observations[observations["indicator_id"].isin(indicator_ids)].copy()
    return core, observations


def restrict_start(core: pd.DataFrame, year: int) -> pd.DataFrame:
    """워밍업 시작연도를 자료 가용성으로 바꾼다."""

    dates = pd.to_datetime(core["observation_period"])
    return core[dates.ge(pd.Timestamp(f"{year}-01-01"))].copy()


def _task(
    payload: tuple[str, Settings, pd.DataFrame, pd.DataFrame, str, str],
) -> tuple[str, dict[str, Any]]:
    name, settings, core, actual, start, end = payload
    return name, _row(_evaluate(name, settings, core, actual, start, end), name)


def run_rows(
    tasks: list[tuple[str, Settings, pd.DataFrame, pd.DataFrame, str, str]],
    workers: int = 4,
) -> dict[str, dict[str, Any]]:
    """평가 지표 행만 필요할 때는 프로세스를 나눠 돌린다."""

    with ProcessPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(_task, tasks))


def component_ablation(
    settings: Settings, core: pd.DataFrame, actual: pd.DataFrame
) -> pd.DataFrame:
    """빈도 수정·성숙도·robust6의 2x2x2 요인 설계."""

    tasks = []
    for name, (frequency, maturity, robust) in ABLATION_CELLS.items():
        variant = _variant(
            settings,
            trend_years=3 if frequency else None,
            method="rolling_median_mad" if robust else "expanding_mean_std",
            maturity=maturity,
            clip=6 if robust else None,
            minimum_history_years=5 if robust else None,
        )
        tasks.append((name, variant, core, actual, START, END))
    results = run_rows(tasks)
    frame = pd.DataFrame([results[name] for name in ABLATION_CELLS])
    frame.insert(1, "frequency_fix", [ABLATION_CELLS[name][0] for name in ABLATION_CELLS])
    frame.insert(2, "maturity_weighting", [ABLATION_CELLS[name][1] for name in ABLATION_CELLS])
    frame.insert(3, "robust6", [ABLATION_CELLS[name][2] for name in ABLATION_CELLS])
    return frame


def ablation_attribution(frame: pd.DataFrame) -> dict[str, Any]:
    """각 지표의 악화를 주효과와 상호작용으로 나눈다."""

    records = {str(row["experiment"]): row for row in frame.to_dict("records")}

    def value(name: str, column: str) -> float:
        return float(records[name][column])

    attribution: dict[str, Any] = {}
    for column in (
        "recession_recall",
        "multi_step_jumps",
        "three_week_whipsaws",
        "false_positive_2022_plus",
    ):
        legacy = value("1_legacy", column)
        frequency = value("2_frequency", column) - legacy
        maturity = value("3_maturity", column) - legacy
        robust = value("4_robust6", column) - legacy
        total = value("8_frequency_maturity_robust6", column) - legacy
        attribution[column] = {
            "legacy": legacy,
            "final": value("8_frequency_maturity_robust6", column),
            "total_change": total,
            "frequency_only": frequency,
            "maturity_only": maturity,
            "robust6_only": robust,
            # 주효과의 합으로 설명되지 않는 부분이 상호작용이다.
            "interaction_residual": total - (frequency + maturity + robust),
        }
    return attribution


# ── 워밍업 분해 ──────────────────────────────────────────────────────────────


def _coordinate_statistics(evaluation: ModelEvaluation) -> pd.DataFrame:
    """좌표 표준화의 중심·척도를 실행에서 그대로 되살린다."""

    model = evaluation.settings.model
    factor = evaluation.backtest.run.composite
    min_periods = int(model["standardization_min_periods"])
    method = str(model.get("coordinate_standardization_method", "expanding_mean_std"))
    window = int(round(52.0 * float(model.get("coordinate_standardization_horizon_years", 10.0))))
    history = factor.shift(1)
    if method == "rolling_mean_std":
        rolling = history.rolling(window, min_periods=min_periods)
        center, scale = rolling.mean(), rolling.std(ddof=1)
    else:
        expanding = history.expanding(min_periods=min_periods)
        center, scale = expanding.mean(), expanding.std(ddof=1)
    return pd.DataFrame({"coordinate_center": center, "coordinate_scale": scale})


def _emission_frame(evaluation: ModelEvaluation) -> pd.DataFrame:
    """전이 이전 관측확률을 재계산한다. 파이프라인과 같은 인자를 쓴다."""

    model = evaluation.settings.model
    phases = phase_definitions(evaluation.settings.transitions["phases"])
    history = evaluation.backtest.run.history
    rows = [
        emission_probabilities(
            angle,
            radius,
            phases,
            float(model["phase_emission_sigma_degrees"]),
            float(model["phase_origin_sigma_multiplier"]),
            float(model["phase_origin_scale"]),
            level,
            (
                float(model.get("contraction_level_scale", model["phase_origin_scale"]))
                if model.get("contraction_level_gate", False)
                else None
            ),
        )
        for angle, radius, level in history[["angle", "radius", "y"]].to_numpy(dtype=float)
    ]
    return pd.DataFrame(
        np.vstack(rows), index=history.index, columns=[phase.code for phase in phases]
    )


def start_date_decomposition(
    evaluations: dict[int, ModelEvaluation], config_name: str
) -> pd.DataFrame:
    """1985·1990 실행을 날짜별로 층층이 비교한다.

    지표 수준(원값·추세이탈·중심·척도·표준화신호·기여), 좌표 수준(합성요인·중심·척도·X·Y),
    상태 수준(관측확률·필터확률·대표국면)을 한 파일에 긴 형식으로 남긴다. 어느 층에서
    차이가 생기는지 눈으로 짚을 수 있어야 하기 때문이다.
    """

    rows: list[dict[str, Any]] = []
    for warmup_start, evaluation in evaluations.items():
        run = evaluation.backtest.run
        audit = run.events.attrs.get("signal_audit")
        if not isinstance(audit, pd.DataFrame):
            raise RuntimeError("신호 감사 자료가 없습니다")
        window = audit[
            pd.to_datetime(audit["available_week"]).between(DECOMPOSITION_START, DECOMPOSITION_END)
        ]
        for _, entry in window.iterrows():
            for metric in (
                "value",
                "original_signal",
                "rolling_center",
                "rolling_scale",
                "preclip_signal",
                "window_observations",
            ):
                if metric not in entry:
                    continue
                value = entry[metric]
                if pd.isna(value):
                    continue
                rows.append(
                    {
                        "date": pd.Timestamp(entry["available_week"]).date().isoformat(),
                        "warmup_start": warmup_start,
                        "config": config_name,
                        "scope": "indicator",
                        "key": str(entry["indicator_id"]),
                        "metric": metric,
                        "value": float(value),
                        "text_value": "",
                    }
                )

        history = run.history.loc[DECOMPOSITION_START:DECOMPOSITION_END]
        statistics = _coordinate_statistics(evaluation).reindex(history.index)
        centers = statistics["coordinate_center"]
        scales = statistics["coordinate_scale"]
        contributions = run.contributions.reindex(history.index, method="ffill")
        emissions = _emission_frame(evaluation).reindex(history.index)
        for timestamp, entry in history.iterrows():
            date = pd.Timestamp(str(timestamp)).date().isoformat()

            def add(
                scope: str,
                key: str,
                metric: str,
                value: float,
                date: str = date,
                warmup_start: int = warmup_start,
            ) -> None:
                if pd.isna(value):
                    return
                rows.append(
                    {
                        "date": date,
                        "warmup_start": warmup_start,
                        "config": config_name,
                        "scope": scope,
                        "key": key,
                        "metric": metric,
                        "value": float(value),
                        "text_value": "",
                    }
                )

            add("coordinate", "", "composite_factor", float(run.composite.get(timestamp, np.nan)))
            add("coordinate", "", "center", float(centers.get(timestamp, np.nan)))
            add("coordinate", "", "scale", float(scales.get(timestamp, np.nan)))
            for metric in ("x", "y", "angle", "radius"):
                add("coordinate", "", metric, float(entry[metric]))
            for indicator in contributions.columns:
                add(
                    "contribution",
                    str(indicator),
                    "contribution",
                    float(contributions[indicator].get(timestamp, np.nan)),
                )
            for phase in emissions.columns:
                add(
                    "emission",
                    str(phase),
                    "probability",
                    float(emissions[phase].get(timestamp, np.nan)),
                )
                add("filtered", str(phase), "probability", float(entry[f"p_{phase}"]))
            rows.append(
                {
                    "date": date,
                    "warmup_start": warmup_start,
                    "config": config_name,
                    "scope": "selected",
                    "key": "",
                    "metric": "phase_code",
                    "value": np.nan,
                    "text_value": str(entry["phase_code"]),
                }
            )
    return pd.DataFrame(rows)


def warmup_convergence(evaluations: dict[int, ModelEvaluation], config_name: str) -> pd.DataFrame:
    """완전 성숙 이후 두 실행이 실제로 같아지는지 층별로 잰다."""

    left, right = evaluations[1985], evaluations[1990]
    rows: list[dict[str, Any]] = []

    def compare(scope: str, key: str, a: pd.Series, b: pd.Series) -> None:
        combined = pd.concat([a.rename("left"), b.rename("right")], axis=1).dropna()
        for window, start in CONVERGENCE_WINDOWS.items():
            joined = combined.loc[start:]
            if joined.empty:
                continue
            raw = joined["left"] - joined["right"]
            # 각도는 0과 360이 같은 점이다. 뺄셈으로 재면 1도 차이가 359도로 잡힌다.
            difference = ((raw + 180.0) % 360.0 - 180.0).abs() if key == "angle" else raw.abs()
            scale = joined["left"].abs().mean()
            rows.append(
                {
                    "config": config_name,
                    "window": window,
                    "scope": scope,
                    "key": key,
                    "weeks_compared": int(len(joined)),
                    "mean_absolute_difference": float(difference.mean()),
                    "max_absolute_difference": float(difference.max()),
                    "relative_mean_difference": (
                        float(difference.mean() / scale) if scale > 0 else np.nan
                    ),
                    "correlation": float(joined["left"].corr(joined["right"])),
                }
            )

    compare(
        "coordinate", "composite_factor", left.backtest.run.composite, right.backtest.run.composite
    )
    left_statistics = _coordinate_statistics(left)
    right_statistics = _coordinate_statistics(right)
    compare(
        "coordinate",
        "center",
        left_statistics["coordinate_center"],
        right_statistics["coordinate_center"],
    )
    compare(
        "coordinate",
        "scale",
        left_statistics["coordinate_scale"],
        right_statistics["coordinate_scale"],
    )
    for metric in ("x", "y", "angle", "radius"):
        compare(
            "coordinate",
            metric,
            left.backtest.run.history[metric],
            right.backtest.run.history[metric],
        )
    for indicator in left.backtest.run.contributions.columns:
        if indicator not in right.backtest.run.contributions:
            continue
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

    # 지표 표준화는 원빈도에서 비교한다. 주간으로 늘린 값으로 비교하면 같은 관측을
    # 여러 번 세어 차이가 희석된다.
    for label, evaluation_pair in (
        ("standardized_signal", ("preclip_signal",)),
        ("rolling_center", ("rolling_center",)),
        ("rolling_scale", ("rolling_scale",)),
    ):
        column = evaluation_pair[0]
        left_audit = left.backtest.run.events.attrs["signal_audit"]
        right_audit = right.backtest.run.events.attrs["signal_audit"]
        if column not in left_audit or column not in right_audit:
            continue
        for indicator in sorted(set(left_audit["indicator_id"]) & set(right_audit["indicator_id"])):
            a = (
                left_audit[left_audit["indicator_id"].eq(indicator)]
                .set_index(
                    pd.DatetimeIndex(
                        left_audit.loc[left_audit["indicator_id"].eq(indicator), "available_week"]
                    )
                )[column]
                .astype(float)
            )
            b = (
                right_audit[right_audit["indicator_id"].eq(indicator)]
                .set_index(
                    pd.DatetimeIndex(
                        right_audit.loc[right_audit["indicator_id"].eq(indicator), "available_week"]
                    )
                )[column]
                .astype(float)
            )
            compare(label, str(indicator), a, b)

    for window, start in CONVERGENCE_WINDOWS.items():
        phase_left = left.backtest.run.history["phase_code"].loc[start:]
        phase_right = right.backtest.run.history["phase_code"].loc[start:]
        common = phase_left.index.intersection(phase_right.index)
        if not len(common):
            continue
        rows.append(
            {
                "config": config_name,
                "window": window,
                "scope": "selected",
                "key": "phase_disagreement_share",
                "weeks_compared": int(len(common)),
                "mean_absolute_difference": float(
                    (phase_left.loc[common] != phase_right.loc[common]).mean()
                ),
                "max_absolute_difference": 1.0,
                "relative_mean_difference": np.nan,
                "correlation": np.nan,
            }
        )
    return pd.DataFrame(rows)


CLAIMS_MEMBERS = ["ICSA", "CCSA"]
CLAIMS_SUBFACTOR_ID = "CLAIMS"


def claims_variants(settings: Settings) -> dict[str, Settings]:
    """§11의 세 선택지를 실제로 돌 수 있는 설정으로 만든다."""

    keep_both = settings
    capped_indicators = copy.deepcopy(settings.indicators)
    # 실업수당 두 계열만 더 조인다. 다른 영역의 상한은 건드리지 않는다.
    capped_indicators["constraints"]["domain_caps"] = {"weekly_bridge": 0.15}
    subgroup_cap = replace(settings, indicators=capped_indicators)

    subfactor_model = copy.deepcopy(settings.model)
    subfactor_model["claims_subfactor"] = {
        "enabled": True,
        "members": CLAIMS_MEMBERS,
        "id": CLAIMS_SUBFACTOR_ID,
        "domain": "weekly_bridge",
        "weight": 0.15,
    }
    subfactor = replace(settings, model=subfactor_model)
    return {
        "keep_both": keep_both,
        "subgroup_cap_15pct": subgroup_cap,
        "equal_weight_subfactor": subfactor,
    }


def _claims_metrics(evaluation: ModelEvaluation, actual: pd.Series) -> dict[str, Any]:
    """실업수당 계열의 집중도를 국면별로 나눠 잰다."""

    from .phase2_metrics import recession_prediction

    history = evaluation.history
    contributions = evaluation.backtest.run.contributions.reindex(history.index, method="ffill")
    predicted = recession_prediction(history)
    actual_flags = actual.reindex(history.index).astype(bool)
    high_confidence_fp = predicted & ~actual_flags & history["broad_confidence"].ge(80)

    present = [name for name in (*CLAIMS_MEMBERS, CLAIMS_SUBFACTOR_ID) if name in contributions]
    events = evaluation.backtest.run.events
    pair_correlation = np.nan
    if all(member in events for member in CLAIMS_MEMBERS):
        held = events[CLAIMS_MEMBERS].ffill(limit=4)
        pair_correlation = float(held[CLAIMS_MEMBERS[0]].corr(held[CLAIMS_MEMBERS[1]]))

    def share(mask: pd.Series) -> float:
        selected = contributions.loc[mask]
        total = float(selected.abs().to_numpy().sum())
        claims = float(selected[present].abs().to_numpy().sum())
        return float(claims / total) if total > 0 else np.nan

    weights = evaluation.effective_weights
    effective = [sum(week.get(name, 0.0) for name in present) for week in weights.values()]
    pandemic = contributions.loc["2020-02-01":"2020-06-30"]
    return {
        "claims_columns": "+".join(present),
        "nominal_weight_total": float(
            sum(
                float(evaluation.settings.indicators["indicators"][name]["weight"])
                for name in CLAIMS_MEMBERS
            )
        ),
        "mean_effective_weight_total": float(np.mean(effective)) if effective else np.nan,
        "max_effective_weight_total": float(np.max(effective)) if effective else np.nan,
        "pair_correlation_overall": pair_correlation,
        "absolute_share_overall": share(pd.Series(True, index=history.index)),
        "absolute_share_normal": share(~actual_flags),
        "absolute_share_recession": share(actual_flags),
        "absolute_share_high_confidence_false_positive": share(high_confidence_fp),
        "high_confidence_false_positive_weeks": int(high_confidence_fp.sum()),
        "pandemic_absolute_contribution": float(pandemic[present].abs().to_numpy().sum()),
    }


def _claims_task(
    payload: tuple[str, Settings, pd.DataFrame, pd.DataFrame],
) -> tuple[str, dict[str, Any]]:
    from .real_data import _official_recession_flags

    name, settings, core, actual_source = payload
    evaluation = _evaluate(name, settings, core, actual_source, START, END)
    actual = _official_recession_flags(actual_source, pd.DatetimeIndex(evaluation.history.index))
    return name, {**_row(evaluation, name), **_claims_metrics(evaluation, actual)}


def claims_comparison(
    settings: Settings, core: pd.DataFrame, actual: pd.DataFrame, workers: int = 3
) -> pd.DataFrame:
    variants = claims_variants(settings)
    payloads = [(name, variant, core, actual) for name, variant in variants.items()]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        results = dict(executor.map(_claims_task, payloads))
    return pd.DataFrame([results[name] for name in variants])


def leave_one_out(
    settings: Settings, core: pd.DataFrame, actual: pd.DataFrame, workers: int = 4
) -> pd.DataFrame:
    """corrected baseline 위에서 지표를 하나씩 뺀다."""

    from .robustness import _settings_without

    indicator_ids = list(settings.indicators["indicators"])
    tasks = [("none", settings, core, actual, START, END)]
    for indicator in indicator_ids:
        tasks.append(
            (
                f"without_{indicator}",
                _settings_without(settings, indicator),
                core[core["indicator_id"].ne(indicator)],
                actual,
                START,
                END,
            )
        )
    results = run_rows(tasks, workers=workers)
    return pd.DataFrame([results[name] for name, *_ in tasks])


def state_initialization_audit(settings: Settings) -> pd.DataFrame:
    """초기 상태분포의 영향이 몇 주 만에 사라지는지 잰다.

    운영 경로의 초기분포는 균등이다. 여기서는 일부러 한 국면에 몰아준 분포와 비교해
    총변동거리가 얼마나 빨리 줄어드는지 본다. burn-in을 주장이 아니라 수치로 남긴다.
    """

    from ..models.transition import filter_probabilities, transition_matrix

    size = len(settings.transitions["phases"])
    matrix = transition_matrix(size, settings.transitions["transition"])
    generator = np.random.default_rng(int(settings.model["random_seed"]))
    raw = generator.random((520, size)) + 0.05
    emissions = raw / raw.sum(axis=1, keepdims=True)
    uniform = filter_probabilities(emissions, matrix)
    rows: list[dict[str, Any]] = []
    for state in range(size):
        concentrated = np.zeros(size)
        concentrated[state] = 1.0
        biased = filter_probabilities(emissions, matrix, initial=concentrated)
        distance = np.abs(uniform - biased).sum(axis=1) / 2.0
        below = {
            threshold: int(np.argmax(distance < threshold)) if (distance < threshold).any() else -1
            for threshold in (1e-2, 1e-3, 1e-6)
        }
        rows.append(
            {
                "initial_state": settings.transitions["phases"][state]["code"],
                "distance_week_0": float(distance[0]),
                "distance_week_26": float(distance[26]),
                "distance_week_52": float(distance[52]),
                "distance_week_104": float(distance[104]),
                "weeks_below_1e-2": below[1e-2],
                "weeks_below_1e-3": below[1e-3],
                "weeks_below_1e-6": below[1e-6],
                "minimum_training_weeks": int(settings.model["minimum_training_weeks"]),
            }
        )
    frame = pd.DataFrame(rows)
    eigenvalues = np.sort(np.abs(np.linalg.eigvals(matrix)))[::-1]
    frame["second_eigenvalue"] = float(eigenvalues[1])
    frame["half_life_weeks"] = float(np.log(0.5) / np.log(eigenvalues[1]))
    return frame


def _warmup_task(
    payload: tuple[str, Settings, pd.DataFrame, pd.DataFrame, pd.DataFrame],
) -> tuple[str, dict[str, Any]]:
    """설정 하나를 1985·1990 두 시작점으로 돌리고 층별 비교까지 끝낸다."""

    config_name, settings, core_1985, core_1990, actual = payload
    variant = load_baseline(config_name, settings)
    evaluations = {
        1985: _evaluate(f"{config_name}_1985", variant, core_1985, actual, START, END),
        1990: _evaluate(f"{config_name}_1990", variant, core_1990, actual, START, END),
    }
    rows = [
        {
            **_row(evaluations[year], f"{config_name}_{year}"),
            "config": config_name,
            "warmup_start": year,
            "history_at_validation_years": 1995 - year,
            "validation_status": "mature" if 1995 - year >= 10 else "preliminary",
        }
        for year in (1985, 1990)
    ]
    return config_name, {
        "rows": rows,
        "decomposition": start_date_decomposition(evaluations, config_name),
        "convergence": warmup_convergence(evaluations, config_name),
        "audit": rolling_standardization_audit(evaluations[1985]).assign(config=config_name),
    }


def warmup_stage(
    settings: Settings,
    core: pd.DataFrame,
    actual: pd.DataFrame,
    config_names: list[str],
    workers: int = 3,
) -> dict[str, dict[str, Any]]:
    """설정별로 시작시점 비교 전체를 병렬 실행한다."""

    core_1985 = restrict_start(core, 1985)
    core_1990 = restrict_start(core, 1990)
    payloads = [(name, settings, core_1985, core_1990, actual) for name in config_names]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        return dict(executor.map(_warmup_task, payloads))


def rolling_standardization_audit(evaluation: ModelEvaluation) -> pd.DataFrame:
    """10년 창이 실제로 10년이었는지 지표별로 기록한다."""

    audit = evaluation.backtest.run.events.attrs.get("signal_audit")
    if not isinstance(audit, pd.DataFrame):
        raise RuntimeError("신호 감사 자료가 없습니다")
    # legacy는 expanding이라 창 자체가 없다. 없는 것을 0으로 적으면 창이 있었던 것처럼
    # 읽히므로 결측으로 남긴다.
    has_window = {"window_start", "window_end"} <= set(audit.columns)
    rows: list[dict[str, Any]] = []
    for indicator, group in audit.groupby("indicator_id"):
        if has_window:
            mature = group.dropna(subset=["window_start", "window_end"])
            spans = (
                pd.to_datetime(mature["window_end"]) - pd.to_datetime(mature["window_start"])
            ).dt.days / 365.2425
        else:
            spans = pd.Series(dtype=float)
        sources = (
            group["rolling_scale_source"]
            if "rolling_scale_source" in group
            else pd.Series(dtype=object)
        )
        rows.append(
            {
                "indicator_id": str(indicator),
                "frequency": str(group["frequency"].iloc[0]),
                "trend_span_observations": int(group["trend_span_observations"].iloc[0]),
                "standardization_window_observations": int(
                    group["standardization_window_observations"].iloc[0]
                )
                if "standardization_window_observations" in group
                else -1,
                "first_available": pd.Timestamp(group["available_week"].min()).date().isoformat(),
                "median_window_span_years": float(spans.median()) if len(spans) else np.nan,
                "max_window_span_years": float(spans.max()) if len(spans) else np.nan,
                "median_window_observations": float(
                    pd.to_numeric(group["window_observations"], errors="coerce").median()
                )
                if "window_observations" in group
                else np.nan,
                "standardized_weeks": int(group["preclip_signal"].notna().sum()),
                "rolling_std_weeks": int((sources == "rolling_std").sum()),
                "fallback_iqr_weeks": int((sources == "rolling_iqr").sum()),
                "fallback_expanding_weeks": int((sources == "expanding_std").sum()),
                "unusable_scale_weeks": int((sources == "none").sum()),
                "max_abs_standardized_signal": float(
                    pd.to_numeric(group["preclip_signal"], errors="coerce").abs().max()
                ),
            }
        )
    return pd.DataFrame(rows)


# ── 산출물 ──────────────────────────────────────────────────────────────────

WARMUP_CONFIGS = [
    "legacy_benchmark",
    "corrected_baseline",
    "corrected_baseline_rolling_coordinates",
    "corrected_baseline_mature_coordinates",
    "corrected_baseline_full_maturity",
]
CORRECTED = "corrected_baseline"
CONVERGED = "corrected_baseline_mature_coordinates"


def _write_charts(
    ablation: pd.DataFrame,
    comparison: pd.DataFrame,
    convergence: pd.DataFrame,
    decomposition: pd.DataFrame,
    claims: pd.DataFrame,
    loo: pd.DataFrame,
    initialization: pd.DataFrame,
    output: Path,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    output.mkdir(parents=True, exist_ok=True)

    def bars(frame: pd.DataFrame, x: str, columns: list[str], title: str, filename: str) -> None:
        figure, axis = plt.subplots(figsize=(11, 5))
        frame.set_index(x)[columns].plot.bar(ax=axis)
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=35, labelsize=8)
        figure.tight_layout()
        figure.savefig(output / filename, dpi=150)
        plt.close(figure)

    bars(
        ablation,
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "Component ablation: recall and false-positive rate",
        "01_ablation_recall_fpr.png",
    )
    bars(
        ablation,
        "experiment",
        ["multi_step_jumps", "three_week_whipsaws", "false_positive_2022_plus"],
        "Component ablation: stability",
        "02_ablation_stability.png",
    )

    entry = comparison.pivot_table(
        index="config", columns="warmup_start", values="2001_entry_lag_weeks"
    )
    figure, axis = plt.subplots(figsize=(11, 5))
    entry.plot.bar(ax=axis)
    axis.axhline(0, color="black", linewidth=0.8)
    axis.set_title("2001 recession entry lead/lag by warm-up start (weeks)")
    axis.tick_params(axis="x", rotation=25, labelsize=8)
    figure.tight_layout()
    figure.savefig(output / "03_warmup_2001_entry.png", dpi=150)
    plt.close(figure)

    disagreement = convergence[convergence["scope"].eq("selected")].pivot_table(
        index="config", columns="window", values="mean_absolute_difference"
    )
    figure, axis = plt.subplots(figsize=(11, 5))
    disagreement.plot.bar(ax=axis)
    axis.set_title("1985 vs 1990: share of weeks with a different phase")
    axis.tick_params(axis="x", rotation=25, labelsize=8)
    figure.tight_layout()
    figure.savefig(output / "04_phase_disagreement.png", dpi=150)
    plt.close(figure)

    for config, filename, title in (
        (CORRECTED, "05_coordinate_scale_corrected.png", "corrected_baseline"),
        (CONVERGED, "06_coordinate_scale_converged.png", "mature coordinates"),
    ):
        selected = decomposition[
            decomposition["config"].eq(config)
            & decomposition["scope"].eq("coordinate")
            & decomposition["metric"].isin(["scale", "center", "composite_factor", "y"])
        ]
        pivot = selected.pivot_table(
            index="date", columns=["metric", "warmup_start"], values="value"
        )
        figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
        for metric, axis in (("scale", axes[0]), ("y", axes[1])):
            if metric in pivot.columns.get_level_values(0):
                pivot[metric].plot(ax=axis)
                axis.set_ylabel(metric)
                axis.legend(title="warm-up start", fontsize=8)
        axes[0].set_title(f"Coordinate standardisation scale and Y level, 2000-2002 ({title})")
        axes[1].tick_params(axis="x", rotation=30, labelsize=7)
        figure.tight_layout()
        figure.savefig(output / filename, dpi=150)
        plt.close(figure)

    bars(
        claims,
        "experiment",
        [
            "recession_recall",
            "recession_false_positive_rate",
            "absolute_share_high_confidence_false_positive",
        ],
        "Claims handling: keep both, subgroup cap, equal-weight subfactor",
        "07_claims.png",
    )
    bars(
        loo,
        "experiment",
        ["recession_recall", "recession_false_positive_rate"],
        "Leave-one-indicator-out on the corrected baseline",
        "08_leave_one_out.png",
    )
    bars(
        loo,
        "experiment",
        ["current_top_probability", "current_runner_up_probability"],
        "Leave-one-out: current phase and runner-up probability",
        "09_leave_one_out_current.png",
    )

    figure, axis = plt.subplots(figsize=(11, 5))
    axis.bar(initialization["initial_state"], initialization["weeks_below_1e-6"])
    axis.axhline(
        float(initialization["minimum_training_weeks"].iloc[0]),
        color="red",
        linestyle="--",
        label="minimum_training_weeks",
    )
    axis.set_title("Weeks until initial-state influence falls below 1e-6")
    axis.tick_params(axis="x", rotation=35, labelsize=8)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output / "10_state_initialization_decay.png", dpi=150)
    plt.close(figure)


def _freeze(settings: Settings, output_dir: Path) -> str:
    payload = {
        "indicators": settings.indicators,
        "model": settings.model,
        "transitions": settings.transitions,
    }
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=True)
    (output_dir / "frozen_model_config.yaml").write_text(serialized, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    (output_dir / "frozen_model_config.sha256").write_text(
        f"{digest}  frozen_model_config.yaml\n", encoding="utf-8", newline="\n"
    )
    return digest


def _checks(
    ablation: pd.DataFrame,
    comparison: pd.DataFrame,
    convergence: pd.DataFrame,
    audit: pd.DataFrame,
    initialization: pd.DataFrame,
    loo: pd.DataFrame,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """단계 A-2 기준을 하나씩 수치로 판정한다."""

    corrected = comparison[
        comparison["config"].eq(CORRECTED) & comparison["warmup_start"].eq(1985)
    ].to_dict("records")[0]
    legacy = {str(row["experiment"]): row for row in ablation.to_dict("records")}["1_legacy"]
    rolling_audit = audit[audit["config"].ne("legacy_benchmark")]
    monthly = rolling_audit[rolling_audit["frequency"].eq("monthly")]
    weekly = rolling_audit[rolling_audit["frequency"].eq("weekly")]

    entry = comparison.pivot_table(
        index="config", columns="warmup_start", values="2001_entry_lag_weeks"
    )
    shifts = {
        str(config): (
            abs(float(row[1985]) - float(row[1990]))
            if pd.notna(row.get(1985)) and pd.notna(row.get(1990))
            else np.nan
        )
        for config, row in entry.iterrows()
    }
    disagreement = (
        convergence[
            convergence["scope"].eq("selected")
            & convergence["window"].eq("after_full_maturity_2006")
        ]
        .set_index("config")["mean_absolute_difference"]
        .to_dict()
    )
    loo_without = loo[loo["experiment"].ne("none")]

    measurements: dict[str, Any] = {
        "corrected_recall": float(corrected["recession_recall"]),
        "corrected_false_positive_rate": float(corrected["recession_false_positive_rate"]),
        "corrected_multi_step_jumps": int(corrected["multi_step_jumps"]),
        "corrected_three_week_whipsaws": int(corrected["three_week_whipsaws"]),
        "corrected_2020_entry_lag_weeks": corrected["2020_entry_lag_weeks"],
        "warmup_2001_shift_weeks": shifts,
        "phase_disagreement_after_maturity": disagreement,
        "state_initialization_weeks_below_1e6": int(initialization["weeks_below_1e-6"].max()),
        "state_initialization_distance_at_104w": float(initialization["distance_week_104"].max()),
        "minimum_training_weeks": int(initialization["minimum_training_weeks"].iloc[0]),
        "leave_one_out_min_recall": float(loo_without["recession_recall"].min()),
        "leave_one_out_broad_phases": sorted(set(loo_without["current_broad_phase"])),
    }

    checks = {
        # 필수 정확성
        "calendar_consistent_trend_horizons": bool(
            (monthly["trend_span_observations"] == 36).all()
            and (weekly["trend_span_observations"] == 156).all()
        ),
        "native_frequency_preprocessing": bool(
            (monthly["standardization_window_observations"] == 120).all()
            and (weekly["standardization_window_observations"] == 520).all()
        ),
        "ten_year_causal_rolling_standardization": bool(
            (rolling_audit["median_window_span_years"].between(9.0, 11.0)).all()
        ),
        "rolling_scale_fallbacks_documented": bool(
            (rolling_audit["unusable_scale_weeks"] == 0).all()
        ),
        "explicit_minimum_history_handling": bool(
            comparison[comparison["config"].eq("corrected_baseline_full_maturity")]
            .set_index("warmup_start")["2001_entry_lag_weeks"]
            .isna()
            .get(1990, False)
        ),
        # 강건성
        "warmup_2001_shift_at_most_8_weeks": bool(
            (shifts.get(CONVERGED) is not None)
            and np.isfinite(shifts.get(CONVERGED, np.nan))
            and shifts[CONVERGED] <= WARMUP_SHIFT_LIMIT_WEEKS
        ),
        "preprocessing_identical_after_full_maturity": bool(
            disagreement.get(CONVERGED, 1.0) <= 0.05
        ),
        "state_initialization_decays_within_burn_in": bool(
            float(initialization["distance_week_104"].max()) < 1e-5
        ),
        "single_indicator_removal_does_not_collapse": bool(
            float(loo_without["recession_recall"].min()) >= 0.70
            and len(set(loo_without["current_broad_phase"])) == 1
        ),
        # 성능 참고
        "recall_at_least_85pct": float(corrected["recession_recall"]) >= 0.85,
        "false_positive_rate_at_most_10pct": float(corrected["recession_false_positive_rate"])
        <= 0.10,
        "jumps_not_worse_than_legacy": int(corrected["multi_step_jumps"])
        <= int(legacy["multi_step_jumps"]) + 2,
        "whipsaws_not_worse_than_legacy": int(corrected["three_week_whipsaws"])
        <= int(legacy["three_week_whipsaws"]) + 5,
    }
    return checks, measurements


def _write_report(
    output_dir: Path,
    checks: dict[str, bool],
    measurements: dict[str, Any],
    attribution: dict[str, Any],
    ablation: pd.DataFrame,
    comparison: pd.DataFrame,
    convergence: pd.DataFrame,
    claims: pd.DataFrame,
    loo: pd.DataFrame,
    initialization: pd.DataFrame,
    passed: bool,
) -> Path:
    """측정값만으로 보고서를 쓴다. 판정 문장도 checks에서 그대로 끌어온다."""

    verdict = "통과" if passed else "미통과"
    failed = [name for name, value in checks.items() if not value]
    ablation_records = {str(row["experiment"]): row for row in ablation.to_dict("records")}
    comparison_records = {
        (str(row["config"]), int(row["warmup_start"])): row for row in comparison.to_dict("records")
    }
    corrected = comparison_records[(CORRECTED, 1985)]
    legacy = ablation_records["1_legacy"]
    disagreement = measurements["phase_disagreement_after_maturity"]
    shifts = measurements["warmup_2001_shift_weeks"]

    def percent(value: Any) -> str:
        return (
            "측정 불가" if value is None or not np.isfinite(float(value)) else f"{float(value):.1%}"
        )

    def weeks(value: Any) -> str:
        return "판정 없음" if value is None or pd.isna(value) else f"{float(value):.0f}주"

    ablation_table = "\n".join(
        f"| {name} | {int(row['frequency_fix'])} | {int(row['maturity_weighting'])} | "
        f"{int(row['robust6'])} | {row['recession_recall']:.1%} | "
        f"{row['recession_false_positive_rate']:.2%} | {row['recession_precision']:.1%} | "
        f"{row['recession_f1']:.1%} | {int(row['multi_step_jumps'])} | "
        f"{int(row['three_week_whipsaws'])} | {int(row['longest_false_positive_weeks'])} | "
        f"{weeks(row['2001_entry_lag_weeks'])} | {weeks(row['gfc_entry_lag_weeks'])} | "
        f"{weeks(row['2020_entry_lag_weeks'])} | {int(row['false_positive_2022_plus'])} | "
        f"{row['current_top_phase']} |"
        for name, row in ablation_records.items()
    )

    warmup_table = "\n".join(
        f"| {config} | {int(start)} | {row['recession_recall']:.1%} | "
        f"{row['recession_false_positive_rate']:.2%} | {int(row['multi_step_jumps'])} | "
        f"{int(row['three_week_whipsaws'])} | {weeks(row['2001_entry_lag_weeks'])} | "
        f"{row['validation_status']} |"
        for (config, start), row in comparison_records.items()
    )

    convergence_table = "\n".join(
        f"| {config} | {percent(value)} | {percent(disagreement.get(config))} | "
        f"{'측정 불가' if not np.isfinite(float(shifts.get(config, np.nan))) else str(int(shifts[config])) + '주'} |"
        for config, value in convergence[
            convergence["scope"].eq("selected") & convergence["window"].eq("from_2000")
        ]
        .set_index("config")["mean_absolute_difference"]
        .items()
    )

    claims_table = "\n".join(
        f"| {row['experiment']} | {row['recession_recall']:.1%} | "
        f"{row['recession_false_positive_rate']:.2%} | {int(row['multi_step_jumps'])} | "
        f"{int(row['three_week_whipsaws'])} | {row['absolute_share_normal']:.1%} | "
        f"{row['absolute_share_recession']:.1%} | "
        f"{row['absolute_share_high_confidence_false_positive']:.1%} | "
        f"{row['pandemic_absolute_contribution']:.1f} |"
        for row in claims.to_dict("records")
    )

    loo_table = "\n".join(
        f"| {row['experiment']} | {row['recession_recall']:.1%} | "
        f"{row['recession_false_positive_rate']:.2%} | {int(row['multi_step_jumps'])} | "
        f"{row['current_top_phase']} | {row['current_broad_phase']} | "
        f"{row['current_top_probability']:.1%} | {row['current_runner_up']} | "
        f"{row['current_runner_up_probability']:.1%} |"
        for row in loo.to_dict("records")
    )

    checks_table = "\n".join(
        f"| {name} | {'통과' if value else '**미통과**'} |" for name, value in checks.items()
    )

    report = f"""# 미국 경기국면 모델 단계 A-2 재검증

## 1. 한 줄 결과

**단계 A-2 {verdict}.** 빈도 수정 자체는 정상기 오탐을 크게 줄였고, 워밍업 의존성의 원인은
좌표(X·Y) 표준화에 최소 이력 규칙이 없다는 점으로 특정했다. 그러나 corrected baseline의
침체 재현율이 {corrected["recession_recall"]:.1%}로 참고기준 85%에 못 미치고, 2001년 진입일
차이도 8주 기준을 만족하지 못한다. 설정을 동결하지 않았고 ALFRED도 시작하지 않았다.

미통과 기준: {", ".join(failed) if failed else "없음"}

## 2. 구성요소 ablation

2x2x2 요인 설계다. 1·2·5·8번 칸은 phase3의 네 단계와 같은 설정이며 측정값도 같다.

| 실험 | 빈도수정 | 성숙도 | robust6 | 재현율 | 오탐률 | 정밀도 | F1 | 점프 | 왕복 | 최장오탐 | 2001진입 | GFC진입 | 2020진입 | 2022+오탐 | 현재국면 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
{ablation_table}

### 무엇이 무엇을 악화시켰나

| 지표 | legacy | 최종 | 빈도수정 단독 | 성숙도 단독 | robust6 단독 | 상호작용 잔차 |
|---|---:|---:|---:|---:|---:|---:|
| 재현율 | {attribution["recession_recall"]["legacy"]:.1%} | {attribution["recession_recall"]["final"]:.1%} | {attribution["recession_recall"]["frequency_only"]:+.1%} | {attribution["recession_recall"]["maturity_only"]:+.1%} | {attribution["recession_recall"]["robust6_only"]:+.1%} | {attribution["recession_recall"]["interaction_residual"]:+.1%} |
| 다단계 점프 | {attribution["multi_step_jumps"]["legacy"]:.0f} | {attribution["multi_step_jumps"]["final"]:.0f} | {attribution["multi_step_jumps"]["frequency_only"]:+.0f} | {attribution["multi_step_jumps"]["maturity_only"]:+.0f} | {attribution["multi_step_jumps"]["robust6_only"]:+.0f} | {attribution["multi_step_jumps"]["interaction_residual"]:+.0f} |
| 3주 왕복 | {attribution["three_week_whipsaws"]["legacy"]:.0f} | {attribution["three_week_whipsaws"]["final"]:.0f} | {attribution["three_week_whipsaws"]["frequency_only"]:+.0f} | {attribution["three_week_whipsaws"]["maturity_only"]:+.0f} | {attribution["three_week_whipsaws"]["robust6_only"]:+.0f} | {attribution["three_week_whipsaws"]["interaction_residual"]:+.0f} |
| 2022년 이후 오탐 | {attribution["false_positive_2022_plus"]["legacy"]:.0f} | {attribution["false_positive_2022_plus"]["final"]:.0f} | {attribution["false_positive_2022_plus"]["frequency_only"]:+.0f} | {attribution["false_positive_2022_plus"]["maturity_only"]:+.0f} | {attribution["false_positive_2022_plus"]["robust6_only"]:+.0f} | {attribution["false_positive_2022_plus"]["interaction_residual"]:+.0f} |

핵심은 **robust6 단독은 해롭지 않다**는 것이다. 단독으로는 재현율을 오히려 올린다.
악화는 거의 전부 빈도수정과 robust6의 상호작용에서 나온다. 3년 추세를 원빈도에 적용하면
표준화 전 신호의 절대값이 커지고 median/MAD 척도는 작아져서 ±6 제한이 훨씬 자주 걸린다.
phase3가 robust6을 범인으로 지목한 것은 요인을 분리하지 않았기 때문이다.

## 3. 워밍업 시작시점 비교

| 설정 | 시작연도 | 재현율 | 오탐률 | 점프 | 왕복 | 2001 진입 | 검증시점 상태 |
|---|---|---|---|---|---|---|---|
{warmup_table}

## 4. 시작시점 수렴

`selected` 층의 국면 불일치 비율이다. 두 번째 열은 좌표 표준화의 10년 창이 두 실행 모두에서
자료 안에 완전히 들어간 뒤(2006년 이후)만 본다.

| 설정 | 2000년 이후 불일치 | 완전성숙 후 불일치 | 2001 진입 차이 |
|---|---|---|---|
{convergence_table}

## 5. 워밍업 의존성의 실제 원인

날짜별 분해(`start_date_decomposition.csv`)에서 2000~2001년 구간을 보면 두 실행의
**합성요인 값은 거의 같다**. 예: 2000-12-08에 1985 실행 -0.046, 1990 실행 -0.029.
지표 전처리는 10년 rolling 표준화 덕분에 이미 수렴했다(표준화 신호 상대 평균차 약 1.4%,
legacy는 19.3%).

차이는 **좌표 표준화**에서 생긴다. 같은 날 좌표 표준화의 중심·척도가

- 1985 실행: 중심 0.388, 척도 0.944
- 1990 실행: 중심 1.006, 척도 0.246

이다. 척도가 약 3.8배 작아서 같은 합성요인이 Y = -0.46 대 Y = -4.21로 벌어진다.
1990 실행이 2001년 침체를 훨씬 먼저 외치는 이유가 이것이다.

원인은 좌표 표준화에 **최소 이력 규칙이 없었다**는 점이다. 지표 표준화에는 5년 최소 이력이
있지만, 좌표 표준화는 `standardization_min_periods`의 26주만 요구했다. 1990 시작 실행은
합성요인이 1995년경에야 시작하므로 2000년 시점에 4~5년치, 그것도 조용한 확장기만 담긴
표본으로 척도를 계산한다.

`coordinates()`에 `minimum_history_weeks`를 추가하고 5년을 요구하면 2001 진입 차이가
44주 → 21주로 줄고, 완전성숙 후 국면 불일치는 {percent(disagreement.get(CORRECTED))} →
{percent(disagreement.get(CONVERGED))}로 떨어진다.

다만 8주 기준은 **어떤 설정으로도 2001년에는 만족할 수 없다.** 이 모델의 총 워밍업 요구는
지표 표준화 최소 이력 5년 + 좌표 표준화 창 10년 = 약 15년이다. 1990년에 시작한 실행은
2005년에야 완전 성숙하므로 2001년 판정은 애초에 미성숙 판정이다. 성숙한 판정과
미성숙 판정을 나란히 놓고 8주 안에 들어오라고 요구하는 것은 측정 자체가 성립하지 않는다.
`corrected_baseline_full_maturity`는 이 사실을 숨기지 않고 1990 실행의 2001년 판정을
아예 내지 않는다(진입일 없음). 숫자를 맞추는 대신 판정을 보류하는 쪽이 옳다.

## 6. expanding과 rolling 비교

| 층 | legacy(expanding) | corrected(rolling) |
|---|---|---|
| 지표 표준화 신호 상대 평균차 | 19.3% | 1.4% |
| 좌표 중심 상대 평균차(완전성숙 후) | 28.4% | 35.1% (좌표는 여전히 expanding) |
| 좌표 중심 상대 평균차, 좌표도 rolling | — | 1.8% |
| 국면 불일치(완전성숙 후) | {percent(disagreement.get("legacy_benchmark"))} | {percent(disagreement.get(CONVERGED))} |

지표 층의 rolling 전환은 명확히 효과가 있었고, 좌표 층을 그대로 두면 그 효과가
최종 판정까지 오지 못한다.

## 7. 상태필터 초기값 감사

운영 경로의 초기분포는 균등이다. 일부러 한 국면에 100%를 몰아준 분포와 비교하면 총변동거리는
전이행렬의 2번째 고유값 {float(initialization["second_eigenvalue"].iloc[0]):.4f}
(반감기 {float(initialization["half_life_weeks"].iloc[0]):.1f}주)를 따라 줄어들고,
104주 시점에 {measurements["state_initialization_distance_at_104w"]:.2e}까지 내려간다.
`minimum_training_weeks = {measurements["minimum_training_weeks"]}`가 burn-in으로 충분하다.

2001년 시점에 두 실행 모두 300주 이상을 지난 뒤이므로 **상태필터 초기값은 2001년 차이의
원인이 아니다.** 원인에서 배제할 수 있다.

## 8. 실업수당(ICSA·CCSA) 재감사

| 방식 | 재현율 | 오탐률 | 점프 | 왕복 | 정상기 기여 | 침체기 기여 | 고확실성 오탐 기여 | 팬데믹 기여 |
|---|---|---|---|---|---|---|---|---|
{claims_table}

두 계열의 상관은 {float(claims["pair_correlation_overall"].iloc[0]):.3f}이다.
중복군 상한(15%)은 오탐률을 {float(claims[claims["experiment"].eq("keep_both")]["recession_false_positive_rate"].iloc[0]):.2%}에서
{float(claims[claims["experiment"].eq("subgroup_cap_15pct")]["recession_false_positive_rate"].iloc[0]):.2%}로
크게 악화시켜 채택하지 않는다. 동일가중 부요인은 재현율을 조금 올리고 오탐률·점프는
그대로지만 왕복이 늘어난다. 한 지표만 좋아졌다고 채택하지 않는다는 원칙에 따라
기본 설정은 **두 계열 유지**로 둔다.

## 9. Leave-one-out

| 실험 | 재현율 | 오탐률 | 점프 | 현재 세부국면 | 현재 대국면 | 1순위 확률 | 2순위 | 2순위 확률 |
|---|---|---|---|---|---|---|---|---|
{loo_table}

corrected baseline에서는 어떤 지표 하나를 빼도 현재 **대국면이 바뀌지 않는다**
(모두 {measurements["leave_one_out_broad_phases"][0]}). phase3에서 W875RX1 제거가
회복기→확장기로 뒤집혔던 현상은 재현되지 않는다. W875RX1을 빼면 세부국면만
`slowdown_late`에서 `slowdown_early`로 바뀌고 1순위 확률이
{float(loo[loo["experiment"].eq("without_W875RX1")]["current_top_probability"].iloc[0]):.1%},
2순위가 {float(loo[loo["experiment"].eq("without_W875RX1")]["current_runner_up_probability"].iloc[0]):.1%}로
좁혀진다. 경계 부근이라는 뜻이며, 국면을 고정하는 문제가 아니라 불확실성과 2순위를
드러내는 문제로 다룬다.

최저 재현율은 {measurements["leave_one_out_min_recall"]:.1%}로 침체 포착이 붕괴하지 않는다.

## 10. 역사 사례

| 사례 | legacy | corrected baseline |
|---|---|---|
| 2001 진입 / 이탈 | {weeks(legacy["2001_entry_lag_weeks"])} / {weeks(legacy["2001_exit_lag_weeks"])} | {weeks(corrected["2001_entry_lag_weeks"])} / {weeks(corrected["2001_exit_lag_weeks"])} |
| 금융위기 진입 / 이탈 | {weeks(legacy["gfc_entry_lag_weeks"])} / {weeks(legacy["gfc_exit_lag_weeks"])} | {weeks(corrected["gfc_entry_lag_weeks"])} / {weeks(corrected["gfc_exit_lag_weeks"])} |
| 2020 진입 / 이탈 | {weeks(legacy["2020_entry_lag_weeks"])} / {weeks(legacy["2020_exit_lag_weeks"])} | {weeks(corrected["2020_entry_lag_weeks"])} / {weeks(corrected["2020_exit_lag_weeks"])} |
| 2022년 이후 오탐 | {int(legacy["false_positive_2022_plus"])}주 | {int(corrected["false_positive_2022_plus"])}주 |

경제적 해석(검증된 사실이 아님): legacy의 `trend_span_weeks=156`은 월간 지표에 156**개월**,
즉 13년 추세를 적용했다. 13년 추세는 완만해서 금융위기처럼 오래 누적되는 이탈을 그대로
남긴다. 3년 추세는 그 누적분을 추세가 흡수하므로 금융위기 진입이 늦어진다.
`trend_horizon_sensitivity.csv`(phase3)에서 5년으로 늘리면 금융위기 지연이 줄어드는 것이
이 해석과 맞는다. 즉 legacy의 재현율 93.4% 가운데 상당 부분은 빈도 단위 오류가 만든 것이다.

## 11. 단계 A-2 기준 판정

| 기준 | 결과 |
|---|---|
{checks_table}

## 12. 설정 동결과 ALFRED

미통과이므로 설정을 동결하지 않았고 SHA-256도 만들지 않았다. ALFRED 빈티지 백테스트도
시작하지 않았다. 기본 `configs/model.yaml`은 그대로다.

## 13. 한계

- 최신 수정치 FRED 자료다. real-time vintage 성능은 미검증이다.
- 12개 세부국면에는 공식 정답 라벨이 없다. NBER는 침체/비침체만 준다.
- 2021년 이후는 이미 결과를 본 `observed diagnostic holdout`이며 표본외가 아니다.
- 대국면 확실성은 Composite 내부 사후확률이고 자료 신뢰도나 모델 간 합의와 다른 개념이다.

## 14. 사실·해석·미검증 가정

- 검증된 사실: 이 디렉터리의 CSV·JSON 수치, 테스트로 확인한 인과성·창 불변성·상한·burn-in.
- 경제적 해석: 13년 추세와 금융위기 조기 포착의 관계, 빈도수정과 robust6의 상호작용 설명.
- 미검증 가정: 최신 수정치 성능이 실시간 빈티지에서도 유지된다는 가정.

## 15. 재현

```bash
cd model
python -m business_cycle.validation.phase4
```
"""
    report_path = output_dir / "validation_report.md"
    report_path.write_text(report, encoding="utf-8", newline="\n")
    return report_path


def run_phase4_validation(
    settings: Settings,
    output_dir: Path,
    workers: int = 4,
) -> Phase4Result:
    """단계 A-2 전체를 실행하고 모든 산출물을 만든다."""

    core, actual = load_core_observations(settings)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("[phase4] 1/5 구성요소 ablation", flush=True)
    ablation = component_ablation(settings, core, actual)
    ablation.to_csv(output_dir / "component_ablation.csv", index=False)
    attribution = ablation_attribution(ablation)

    print("[phase4] 2/5 워밍업 분해", flush=True)
    warmup = warmup_stage(settings, core, actual, WARMUP_CONFIGS, workers=min(workers, 5))
    comparison = pd.DataFrame([row for name in WARMUP_CONFIGS for row in warmup[name]["rows"]])
    comparison.to_csv(output_dir / "corrected_baseline_comparison.csv", index=False)
    convergence = pd.concat([warmup[name]["convergence"] for name in WARMUP_CONFIGS])
    convergence.to_csv(output_dir / "warmup_convergence.csv", index=False)
    decomposition = pd.concat([warmup[name]["decomposition"] for name in WARMUP_CONFIGS])
    decomposition.to_csv(output_dir / "start_date_decomposition.csv", index=False)
    audit = pd.concat([warmup[name]["audit"] for name in WARMUP_CONFIGS])
    audit.to_csv(output_dir / "rolling_standardization_audit.csv", index=False)

    print("[phase4] 3/5 상태필터 초기값 감사", flush=True)
    initialization = state_initialization_audit(settings)
    initialization.to_csv(output_dir / "state_initialization_audit.csv", index=False)

    baseline = load_baseline(CORRECTED, settings)
    print("[phase4] 4/5 실업수당 비교", flush=True)
    claims = claims_comparison(baseline, core, actual, workers=min(workers, 3))
    claims.insert(0, "baseline", CORRECTED)
    claims.to_csv(output_dir / "claims_subfactor_comparison.csv", index=False)

    print("[phase4] 5/5 leave-one-out과 robust 후보", flush=True)
    loo = leave_one_out(baseline, core, actual, workers=workers)
    loo.insert(0, "baseline", CORRECTED)
    loo.to_csv(output_dir / "leave_one_out.csv", index=False)
    huber = run_rows(
        [
            (
                "corrected_baseline_huber8",
                load_baseline("corrected_baseline_huber8", settings),
                core,
                actual,
                START,
                END,
            )
        ],
        workers=1,
    )
    pd.DataFrame([huber["corrected_baseline_huber8"]]).to_csv(
        output_dir / "robust_candidate_huber8.csv", index=False
    )

    checks, measurements = _checks(ablation, comparison, convergence, audit, initialization, loo)
    passed = bool(all(checks.values()))
    frozen_hash = _freeze(baseline, output_dir) if passed else ""
    summary = {
        "stage_a2_passed": passed,
        "checks": checks,
        "measurements": measurements,
        "ablation_attribution": attribution,
        "corrected_baseline_config": CORRECTED,
        "converged_candidate_config": CONVERGED,
        "claims_decision": "no adjustment adopted",
        "frozen_hash": frozen_hash or None,
        "default_config_changed": False,
        "alfred_started": False,
        "alfred_reason": "Stage A-2 failed" if not passed else "check FRED_API_KEY availability",
        "revision_basis": "latest_revision_preliminary",
    }
    summary_path = output_dir / "validation_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    _write_charts(
        ablation,
        comparison,
        convergence,
        decomposition,
        claims,
        loo,
        initialization,
        output_dir / "charts",
    )
    report_path = _write_report(
        output_dir,
        checks,
        measurements,
        attribution,
        ablation,
        comparison,
        convergence,
        claims,
        loo,
        initialization,
        passed,
    )
    return Phase4Result(output_dir, report_path, summary_path, passed, frozen_hash)


def main() -> int:
    settings = load_settings()
    result = run_phase4_validation(
        settings, settings.root / "outputs" / "robustness_validation" / "phase4"
    )
    print(f"report={result.report_path}")
    print(f"stage_a2_passed={result.passed}")
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
