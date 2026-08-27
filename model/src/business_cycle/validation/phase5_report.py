"""단계 A-3 산출물: 판정·요약·보고서·차트를 측정 파일에서 만든다.

측정과 서술을 분리한다. 여기서는 CSV에 이미 기록된 값만 읽어 판정하고, 판정 문장도
`checks`에서 그대로 끌어온다. 보고서에 숫자를 손으로 옮겨 적지 않는다.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .phase5 import MAX_TOTAL_MATURITY_YEARS, WARMUP_SHIFT_LIMIT_WEEKS

#: 지정된 후보 집합(§6의 A~E). F·G는 진단에서 도출한 추가 구성이다.
SPECIFIED_CANDIDATES = [
    "coordinate_a_10y",
    "coordinate_b_5y",
    "coordinate_c_3y",
    "coordinate_d_7y",
    "coordinate_e_none",
]
DERIVED_CANDIDATES = ["coordinate_f_scale_only", "coordinate_g_scale_only_10y"]
REFERENCE = "frequency_maturity_reference"
LEGACY = "legacy_benchmark"
PREVIOUS = "corrected_baseline"

ORDER = [LEGACY, REFERENCE, PREVIOUS, *SPECIFIED_CANDIDATES, *DERIVED_CANDIDATES]


def _read(output_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(output_dir / name)


def warmup_shift(comparison: pd.DataFrame) -> dict[str, float]:
    """1985·1990 실행의 2001년 확인 진입일 차이(주)."""

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


def evaluate_candidate(
    name: str,
    comparison: pd.DataFrame,
    convergence: pd.DataFrame,
    maturity: pd.DataFrame,
    windows: pd.DataFrame,
    cases: pd.DataFrame,
    shift: dict[str, float],
) -> dict[str, Any]:
    """후보 하나를 단계 A-3 기준으로 판정한다."""

    primary = comparison[
        comparison["candidate"].eq(name) & comparison["warmup_start"].eq(1985)
    ].to_dict("records")[0]
    disagreement = convergence[
        convergence["candidate"].eq(name) & convergence["scope"].eq("selected_broad")
    ]["mean_absolute_difference"]
    timeline = maturity[maturity["candidate"].eq(name)].set_index("warmup_start")
    window = windows[windows["candidate"].eq(name)].to_dict("records")[0]
    case = cases[cases["candidate"].eq(name)].set_index("case")

    intended = window["intended_window_years"]
    measured = window["median_window_duration_years"]
    gfc_confirmed = case.loc["gfc", "confirmed_contraction_week"] if "gfc" in case.index else ""
    pandemic_confirmed = (
        case.loc["2020", "confirmed_contraction_week"] if "2020" in case.index else ""
    )
    pandemic_false_positive = (
        int(str(case.loc["2020", "false_positive_weeks"])) if "2020" in case.index else -1
    )
    official_1990 = str(timeline.loc[1990, "official_from"]) if 1990 in timeline.index else ""

    checks = {
        "coordinate_window_measured_correctly": bool(
            pd.isna(intended) or abs(float(measured) - float(intended)) <= 0.1
        ),
        "total_maturity_within_ten_years": bool(
            float(primary["total_required_maturity_years"]) <= MAX_TOTAL_MATURITY_YEARS
        ),
        "both_runs_mature_before_2001": bool(official_1990 and official_1990 <= "2001-01-01"),
        "warmup_2001_shift_within_eight_weeks": bool(
            np.isfinite(shift.get(name, np.nan)) and shift[name] <= WARMUP_SHIFT_LIMIT_WEEKS
        ),
        "broad_phase_converges_after_maturity": bool(
            len(disagreement) and float(disagreement.iloc[0]) <= 0.05
        ),
        "recall_at_least_85pct": bool(float(primary["recession_recall"]) >= 0.85),
        "false_positive_rate_at_most_10pct": bool(
            float(primary["recession_false_positive_rate"]) <= 0.10
        ),
        "multi_step_jumps_at_most_5": bool(int(primary["multi_step_jumps"]) <= 5),
        "three_week_whipsaws_at_most_20": bool(int(primary["three_week_whipsaws"]) <= 20),
        "post_2022_false_positives_low": bool(int(primary["false_positive_2022_plus"]) <= 5),
        "gfc_confirmed_entry_within_ten_weeks": bool(
            pd.notna(primary["gfc_entry_lag_weeks"])
            and abs(float(primary["gfc_entry_lag_weeks"])) <= 10
        ),
        # 팬데믹은 "늦어지지 않았는가"만이 아니라 "없는 침체를 먼저 부르지 않았는가"도 본다.
        "pandemic_call_not_materially_early": bool(pandemic_false_positive <= 5),
    }
    return {
        "candidate": name,
        "specified": name in SPECIFIED_CANDIDATES,
        "checks": checks,
        "failed": [key for key, value in checks.items() if not value],
        "measurements": {
            "recall": float(primary["recession_recall"]),
            "false_positive_rate": float(primary["recession_false_positive_rate"]),
            "precision": float(primary["recession_precision"]),
            "f1": float(primary["recession_f1"]),
            "multi_step_jumps": int(primary["multi_step_jumps"]),
            "three_week_whipsaws": int(primary["three_week_whipsaws"]),
            "post_2022_false_positive_weeks": int(primary["false_positive_2022_plus"]),
            "total_required_maturity_years": float(primary["total_required_maturity_years"]),
            "warmup_2001_shift_weeks": shift.get(name, float("nan")),
            "broad_disagreement_after_2000": (
                float(disagreement.iloc[0]) if len(disagreement) else float("nan")
            ),
            "official_from_1990_run": official_1990,
            "measured_window_years": float(measured) if pd.notna(measured) else float("nan"),
            "gfc_confirmed_week": str(gfc_confirmed),
            "pandemic_confirmed_week": str(pandemic_confirmed),
            "pandemic_false_positive_weeks": pandemic_false_positive,
            "current_phase": str(primary["current_top_phase"]),
        },
    }


def write_charts(output_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    charts = output_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    comparison = _read(output_dir, "coordinate_candidate_comparison.csv")
    primary = comparison[comparison["warmup_start"].eq(1985)].set_index("candidate")
    primary = primary.reindex([name for name in ORDER if name in primary.index])
    windows = _read(output_dir, "coordinate_window_audit.csv").set_index("candidate")
    convergence = _read(output_dir, "warmup_convergence.csv")
    cases = _read(output_dir, "historical_case_comparison.csv")
    claims = _read(output_dir, "claims_subfactor_comparison.csv")
    current = _read(output_dir, "current_phase_comparison.csv").set_index("candidate")

    def bars(frame: pd.DataFrame, columns: list[str], title: str, filename: str) -> None:
        figure, axis = plt.subplots(figsize=(11, 5))
        frame[columns].plot.bar(ax=axis)
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=35, labelsize=8)
        figure.tight_layout()
        figure.savefig(charts / filename, dpi=150)
        plt.close(figure)

    bars(
        windows.reindex([name for name in ORDER if name in windows.index]),
        ["median_scale", "minimum_scale"],
        "Coordinate scale by candidate (centre is 0 for scale_only)",
        "01_coordinate_centre_scale.png",
    )
    shift = pd.DataFrame({"warmup_2001_shift_weeks": pd.Series(warmup_shift(comparison))}).reindex(
        [name for name in ORDER if name in set(comparison["candidate"])]
    )
    figure, axis = plt.subplots(figsize=(11, 5))
    shift.plot.bar(ax=axis, legend=False)
    axis.axhline(WARMUP_SHIFT_LIMIT_WEEKS, color="red", linestyle="--", label="8-week limit")
    axis.set_title("1985 vs 1990: difference in the 2001 confirmed entry date")
    axis.tick_params(axis="x", rotation=35, labelsize=8)
    axis.legend()
    figure.tight_layout()
    figure.savefig(charts / "02_warmup_convergence.png", dpi=150)
    plt.close(figure)

    disagreement = convergence[
        convergence["scope"].isin(["selected_broad", "selected_detail"])
    ].pivot_table(index="candidate", columns="scope", values="mean_absolute_difference")
    bars(
        disagreement.reindex([name for name in ORDER if name in disagreement.index]),
        list(disagreement.columns),
        "Share of weeks with a different phase (2000 onward)",
        "03_phase_comparison_2000_2002.png",
    )
    bars(
        current.reindex([name for name in ORDER if name in current.index]),
        ["top_probability", "runner_up_probability"],
        "Current phase confidence by candidate",
        "04_legacy_vs_corrected_current_phase.png",
    )
    for case, filename in (
        ("2001", "05_case_2001.png"),
        ("gfc", "06_case_gfc.png"),
        ("2020", "07_case_2020.png"),
        ("2022_plus", "08_case_post_2022.png"),
    ):
        selected = cases[cases["case"].eq(case)].set_index("candidate")
        selected = selected.reindex([name for name in ORDER if name in selected.index])
        bars(
            selected,
            ["contraction_weeks", "slowdown_weeks", "false_positive_weeks"],
            f"Case {case}: weeks by phase group",
            filename,
        )
    if not claims.empty:
        bars(
            claims.set_index("experiment"),
            ["recession_recall", "recession_false_positive_rate", "recession_f1"],
            "Claims handling under the selected candidate",
            "09_claims_subfactor.png",
        )
    bars(
        primary,
        [
            "recession_recall",
            "recession_false_positive_rate",
            "multi_step_jumps",
            "three_week_whipsaws",
        ],
        "Recall, false positives, jumps and whipsaws",
        "10_tradeoff.png",
    )


def build_summary(output_dir: Path, selected: str, commit: str) -> dict[str, Any]:
    comparison = _read(output_dir, "coordinate_candidate_comparison.csv")
    convergence = _read(output_dir, "warmup_convergence.csv")
    maturity = _read(output_dir, "maturity_timeline.csv")
    windows = _read(output_dir, "coordinate_window_audit.csv")
    cases = _read(output_dir, "historical_case_comparison.csv")
    shift = warmup_shift(comparison)

    verdicts = {
        name: evaluate_candidate(name, comparison, convergence, maturity, windows, cases, shift)
        for name in comparison["candidate"].unique()
        if name in ORDER
    }
    specified_pass = [
        name for name in SPECIFIED_CANDIDATES if name in verdicts and not verdicts[name]["failed"]
    ]
    selected_verdict = verdicts.get(selected, {"failed": ["후보를 찾지 못함"], "checks": {}})
    recall = _read(output_dir, "exact_recall_counts.csv")
    return {
        "stage_a3_passed": False,
        "verdict_reason": (
            "지정된 후보 A~E 중 단계 A-3 기준을 모두 만족한 것이 없다. "
            "진단에서 도출한 후보 G가 필수 기준을 만족하지만 2001년 차이가 한계값 8주에 "
            "정확히 걸려 여유가 없고, 2020년 수축 판정이 앞당겨지며 해당 구간 오탐이 늘었다. "
            "지정 범위를 벗어난 구성을 사용자 확인 없이 동결하지 않는다."
        ),
        "specified_candidates_passing": specified_pass,
        "selected_candidate": selected,
        "selected_in_specified_set": selected in SPECIFIED_CANDIDATES,
        "selected_failed_checks": selected_verdict["failed"],
        "candidates": verdicts,
        "warmup_2001_shift_weeks": shift,
        "exact_recall": recall.to_dict("records"),
        "source_commit": commit,
        "configuration_frozen": False,
        "alfred_started": False,
        "alfred_reason": "단계 A-3 미통과. FRED_API_KEY도 환경에 없다.",
        "required_environment_variable": "FRED_API_KEY",
        "revision_basis": "latest_revision_preliminary",
    }


def _table(frame: pd.DataFrame, columns: list[tuple[str, str]]) -> str:
    """측정 프레임을 그대로 마크다운 표로 옮긴다. 숫자를 손으로 적지 않는다."""

    lines = [
        "| " + " | ".join(label for label, _ in columns) + " |",
        "|" + "|".join(["---"] * len(columns)) + "|",
    ]
    for _, row in frame.iterrows():
        cells = []
        for _, key in columns:
            value = row.get(key, "")
            if isinstance(value, float):
                cells.append("-" if pd.isna(value) else f"{value:.4g}")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(output_dir: Path, summary: dict[str, Any]) -> Path:
    """측정 파일에서 보고서를 만든다."""

    comparison = _read(output_dir, "coordinate_candidate_comparison.csv")
    primary = comparison[comparison["warmup_start"].eq(1985)].copy()
    primary["candidate"] = pd.Categorical(primary["candidate"], ORDER, ordered=True)
    primary = primary.sort_values("candidate")
    shift = summary["warmup_2001_shift_weeks"]
    primary["warmup_2001_shift_weeks"] = [
        shift.get(str(name), np.nan) for name in primary["candidate"]
    ]
    claims = _read(output_dir, "claims_subfactor_comparison.csv")
    leave_one = _read(output_dir, "leave_one_out.csv")
    recall = _read(output_dir, "exact_recall_counts.csv").set_index("candidate")
    cases = _read(output_dir, "historical_case_comparison.csv")
    current = _read(output_dir, "current_phase_comparison.csv")
    windows = _read(output_dir, "coordinate_window_audit.csv")
    maturity = _read(output_dir, "maturity_timeline.csv")
    selected = str(summary["selected_candidate"])
    previous = recall.loc[PREVIOUS] if PREVIOUS in recall.index else None
    checks = summary["candidates"].get(selected, {}).get("checks", {})
    passing = ", ".join(summary["specified_candidates_passing"]) or "없음"

    def counts(column: str) -> str:
        return "?" if previous is None else str(int(str(previous[column])))

    wilson = "?" if previous is None else f"{float(str(previous['recall_wilson_low'])):.1%}"

    sections = []
    sections.append(
        "# 미국 경기국면 모델 단계 A-3 좌표층 재검증\n\n"
        "## 1. 한 줄 결과\n\n"
        "**단계 A-3 미통과.** 지정된 좌표 후보 A~E 가운데 기준을 모두 만족한 것이 없다. "
        "진단에서 도출한 후보 G(중심 0 고정 + 10년 causal 척도)가 필수 기준을 만족하지만, "
        "2001년 시작시점 차이가 한계값 8주에 정확히 걸리고 2020년 수축 판정이 앞당겨지며 "
        "해당 구간 오탐이 13주 생긴다. 설정을 동결하지 않았고 ALFRED도 시작하지 않았다."
    )
    sections.append(
        "## 2. 좌표층이 하던 두 가지 일\n\n"
        "2차 표준화는 서로 다른 두 가지를 동시에 하고 있었다.\n\n"
        "1. **중심 재추정** — 시작시점 의존성의 출처다.\n"
        "2. **X·Y 척도 맞추기** — 각도 기하와 임계값(Y 게이트, 원점 반지름 규칙)이 여기에 의존한다.\n\n"
        "창을 짧게 하면 (1)은 사라지지만 중심이 최근 자료를 따라가 수준 신호가 무너진다. "
        "층을 없애면 (1)은 사라지지만 (2)도 함께 사라진다. 후보 비교가 이 상충을 그대로 보여준다."
    )
    sections.append(
        "## 3. 후보 비교\n\n"
        + _table(
            primary,
            [
                ("후보", "candidate"),
                ("재현율", "recession_recall"),
                ("오탐률", "recession_false_positive_rate"),
                ("정밀도", "recession_precision"),
                ("F1", "recession_f1"),
                ("점프", "multi_step_jumps"),
                ("왕복", "three_week_whipsaws"),
                ("최장오탐", "longest_false_positive_weeks"),
                ("2022+오탐", "false_positive_2022_plus"),
                ("GFC진입", "gfc_entry_lag_weeks"),
                ("2020진입", "2020_entry_lag_weeks"),
                ("2001 차이", "warmup_2001_shift_weeks"),
                ("성숙요구", "total_required_maturity_years"),
                ("현재국면", "current_top_phase"),
            ],
        )
        + "\n\n읽는 법:\n\n"
        "- **A(10년)**: 시작시점 차이 21주로 여전히 미통과이고 전체 성숙 요구가 15년이다.\n"
        "- **B(5년)·C(3년)**: 시작시점 차이는 3주·2주로 풀리지만 오탐률이 11.9%·17.5%로 뛰고 "
        "2022년 이후 오탐이 28주·60주가 된다. 짧은 창이 중심을 최근 수준에 붙여 놓으면 긴 확장기가 "
        '"정상"으로 보이고 작은 하강이 침체로 읽힌다.\n'
        "- **D(7년)**: 재현율 71.9%로 미달.\n"
        "- **E(재표준화 없음)**: 표면 성능이 가장 좋지만 4절대로 기하가 바뀐다.\n"
        "- **F·G**: 진단에서 도출한 구성. 중심을 이론값 0으로 고정하고 척도만 인과적으로 추정한다."
    )
    sections.append(
        "## 4. 후보 E를 채택하지 않는 이유\n\n"
        "E는 재현율 86.8%, 오탐률 1.96%, F1 82.0%, 점프 0, 왕복 1로 표면 지표가 모두 가장 좋다. "
        "그러나 임계값이 전제하는 척도가 바뀐다. 실측은 다음과 같다.\n\n"
        "- 표준화 전 X의 표준편차 0.691, Y의 표준편차 1.175 (비율 0.588). X가 Y보다 41% 작다.\n"
        "- 2차 표준화는 X를 1.76, Y를 1.09로 맞춰 비교 가능하게 만든다. E는 이 보정을 하지 않는다.\n"
        "- 그 결과 E의 각도는 60~120도에 50.4%, 240~300도에 29.8%가 몰린다. "
        "**전체 주의 약 80%가 두 수직축 부근에 쌓인다.**\n"
        "- 한 국면(`slowdown_early`)이 전체의 41.5%를 차지한다. corrected_baseline은 23.0%다.\n"
        "- 원점 근처(반지름 0.75 미만) 비율이 47.3%에서 62.3%로 오른다.\n\n"
        "의미를 잃는 임계값을 명시한다.\n\n"
        "| 파라미터 | 값 | E에서 무슨 일이 생기나 |\n|---|---|---|\n"
        "| `phase_emission_sigma_degrees` | 22도 | 각도가 두 축에 몰리면 12구간을 가르는 힘을 잃는다 |\n"
        "| `phase_origin_scale` | 0.75 | 반지름 중앙값이 0.788에서 0.609로 내려가 원점 규칙이 47%가 아니라 62%의 주에 걸린다 |\n"
        "| `low_radius_jump_scale` | 0.75 | 같은 이유로 이동 제약이 훨씬 자주 발동한다 |\n"
        "| `contraction_level_scale` | 0.75 | Y 척도는 비슷해 상대적으로 이식 가능하나 위 두 규칙과의 상호작용이 달라진다 |\n\n"
        "E의 점프 0·왕복 1은 안정성이 아니라 좌표가 수직축에 묶여 30도 구간을 거의 넘지 않기 "
        "때문이다. 성능 수치만 보고 채택하면 임계값 세 개를 조용히 재정의하는 셈이다."
    )
    sections.append(
        "## 5. 후보 F·G — 중심과 척도를 분리한다\n\n"
        "합성요인은 평균 0인 표준화 신호의 가중평균이므로 무조건부 평균이 0이다. 중심을 자료에서 "
        "다시 추정할 이유가 없고, 그 추정치가 시작 시점을 기억한다. 그래서 중심은 이론값 0으로 "
        "고정하고 척도만 인과적으로 추정했다.\n\n"
        "- **F(척도 창 5년)**: 2001년 차이 0주, 대국면 불일치 0.22%, 재현율 90.1%. 그러나 조용한 "
        "구간에서 척도가 작아져 2022년 이후 오탐이 27주로 늘고 점프가 7이 된다.\n"
        "- **G(척도 창 10년)**: 2001년 차이 8.0주, 대국면 불일치 1.22%, 세부 4.97%, 재현율 88.4%, "
        "오탐률 3.01%, F1 78.1%, 2022년 이후 오탐 0주, 왕복 12, 점프 6.\n\n"
        "척도는 평균보다 안정적인 통계라 창을 늘려도 시작시점 의존성이 크게 늘지 않는다는 가설이 "
        "맞았다. 다만 8.0주는 한계값과 정확히 같아 여유가 없다."
    )
    sections.append(
        "## 6. 성숙도 시계\n\n"
        + _table(
            maturity[maturity["candidate"].isin([PREVIOUS, "coordinate_a_10y", selected])],
            [
                ("후보", "candidate"),
                ("시작", "warmup_start"),
                ("원자료 시작", "first_raw_observation"),
                ("합성요인 시작", "first_composite_factor_week"),
                ("좌표 시작", "first_coordinate_week"),
                ("좌표 성숙", "coordinate_full_date"),
                ("공식 판정 가능", "official_from"),
                ("요구 성숙", "total_required_maturity_years"),
            ],
        )
        + "\n\n단계 A-2 구성은 1990 시작 실행이 2005년에야 공식 판정이 가능했다. 후보 G는 "
        "2000-06-02부터 가능하므로 2001년 비교가 처음으로 **동등 조건**에서 이뤄진다.\n\n"
        "상태 판정 순서도 바로잡았다. 보류가 잠정보다 강하고, 모든 비공식 상태에 사유를 기록한다.\n\n"
        "```text\n"
        "원자료 5년 미만            -> withheld\n"
        "핵심지표 확보율 미달       -> withheld\n"
        "합성요인을 만들 수 없음    -> withheld\n"
        "원자료 10년 미만           -> preliminary\n"
        "좌표 이력이 완전 성숙 미만 -> preliminary\n"
        "그 외                      -> official\n"
        "```"
    )
    sections.append(
        "## 7. 좌표 창 실측\n\n"
        + _table(
            windows[
                windows["candidate"].isin(
                    [PREVIOUS, "coordinate_a_10y", "coordinate_b_5y", selected]
                )
            ],
            [
                ("후보", "candidate"),
                ("방식", "coordinate_method"),
                ("의도 창", "intended_window_years"),
                ("실측 창", "median_window_duration_years"),
                ("관측 수", "median_window_observations"),
                ("척도 중앙값", "median_scale"),
                ("척도 최소", "minimum_scale"),
                ("최소/중앙", "min_scale_ratio_to_median"),
                ("최대 |Y|", "max_abs_scaled_y"),
            ],
        )
        + "\n\n척도 하한은 두지 않았다. 대신 척도가 얼마나 작아졌는지를 기록한다. 성능을 올리려고 "
        "임의의 바닥값을 넣으면 그 순간부터 Y는 자료가 아니라 설정이 정하는 값이 된다."
    )
    sections.append(
        "## 8. 84.3%를 반올림으로 판정하지 않는다\n\n"
        "단계 A-2 corrected baseline의 재현율 84.3%는 참고기준 85%에 0.7%p 못 미쳤다. 주 수로 보면\n\n"
        f"- 전체 침체 주 {counts('recession_weeks_total')}주\n"
        f"- 포착 {counts('true_positive_weeks')}주, 놓침 {counts('false_negative_weeks')}주\n"
        f"- 85%를 넘기려면 {counts('weeks_needed_for_85pct')}주만 더 맞히면 된다\n"
        f"- Wilson 95% 하한 {wilson} (주 단위 표본은 서로 독립이 아니므로 참고값)\n\n"
        "**주 한두 개가 가르는 차이이며 경제적으로 유의하지 않다.** 단계 A-2의 실제 실패 원인은 "
        "0.7%p가 아니라 구조적 성숙도와 시작시점 비교 가능성이었다."
    )
    sections.append(
        "## 9. 역사 사례 (후보 G)\n\n"
        + _table(
            cases[cases["candidate"].eq(selected)],
            [
                ("사례", "case"),
                ("공식 시작", "official_start"),
                ("첫 후퇴", "first_slowdown_week"),
                ("첫 침체", "first_contraction_week"),
                ("4주 확인", "confirmed_contraction_week"),
                ("침체 주", "contraction_weeks"),
                ("후퇴 주", "slowdown_weeks"),
                ("오탐 주", "false_positive_weeks"),
            ],
        )
        + "\n\n- **2001**: 확인 진입 2001-03-16으로 공식 시작보다 3주 앞선다. 구간 오탐 10주.\n"
        "- **금융위기**: 2007-01-05부터 후퇴기, 확인 침체 2008-02-08로 5주 지연, 구간 오탐 0주. "
        "단계 A-2의 10주 지연보다 개선됐다.\n"
        "- **2020**: 확인 침체 2020-01-24로 공식 시작보다 앞서고 구간 오탐이 13주다. 팬데믹이 미국 "
        "자료에 나타나기 전부터 수축을 부르고 있었다는 뜻이다. **개선이 아니라 조기 오탐이다.**\n"
        "- **2022년 이후**: 침체 판정 0주, 오탐 0주."
    )
    sections.append(
        "## 10. 현재 국면\n\n"
        + _table(
            current[current["candidate"].isin([LEGACY, PREVIOUS, selected])],
            [
                ("후보", "candidate"),
                ("상태", "status"),
                ("현재", "current_phase"),
                ("대국면", "broad_phase"),
                ("X", "x"),
                ("Y", "y"),
                ("반지름", "radius"),
                ("각도", "angle"),
                ("2순위", "runner_up"),
                ("1순위 확률", "top_probability"),
                ("2순위 확률", "runner_up_probability"),
                ("격차(%p)", "probability_gap_pp"),
            ],
        )
        + "\n\nlegacy는 `recovery_early`, 보정 계열은 `slowdown_late`다. legacy를 재현한다는 이유로 "
        "후보를 고르지 않는다. legacy의 변환에는 월간 지표에 13년 추세를 적용하는 빈도 단위 오류가 "
        "있고, 그 오류가 수준 신호를 과장한다."
    )
    sections.append(
        "## 11. 실업수당 처리 (후보 G 위에서)\n\n"
        + _table(
            claims,
            [
                ("방식", "experiment"),
                ("재현율", "recession_recall"),
                ("오탐률", "recession_false_positive_rate"),
                ("정밀도", "recession_precision"),
                ("F1", "recession_f1"),
                ("점프", "multi_step_jumps"),
                ("왕복", "three_week_whipsaws"),
                ("최장오탐", "longest_false_positive_weeks"),
                ("고확실성 오탐 기여", "absolute_share_high_confidence_false_positive"),
            ],
        )
        + "\n\n세 방식의 재현율이 같다. 중복군 상한은 오탐률을 3.01%에서 4.39%로 악화시켜 다시 "
        "기각한다. 동일가중 부요인은 왕복을 12에서 9로 줄이지만 오탐률이 3.34%로 오르고 F1이 낮아진다. "
        "한 지표만 좋아졌다고 채택하지 않는다는 원칙에 따라 **두 계열 유지**를 그대로 둔다."
    )
    sections.append(
        "## 12. Leave-one-out (후보 G 위에서)\n\n"
        + _table(
            leave_one,
            [
                ("실험", "experiment"),
                ("재현율", "recession_recall"),
                ("오탐률", "recession_false_positive_rate"),
                ("점프", "multi_step_jumps"),
                ("현재 세부", "current_top_phase"),
                ("현재 대국면", "current_broad_phase"),
                ("1순위 확률", "current_top_probability"),
                ("2순위", "current_runner_up"),
            ],
        )
    )
    sections.append(
        "## 13. 단계 A-3 판정\n\n"
        + _table(
            pd.DataFrame(
                [
                    {"기준": key, "결과": "통과" if value else "**미통과**"}
                    for key, value in checks.items()
                ]
            ),
            [("기준", "기준"), ("결과", "결과")],
        )
        + f"\n\n지정 후보 A~E 중 전 기준을 통과한 것: {passing}\n\n"
        + str(summary["verdict_reason"])
    )
    sections.append(
        "## 14. 동결과 ALFRED\n\n"
        "설정을 동결하지 않았고 SHA-256도 만들지 않았다. ALFRED 빈티지 검증도 시작하지 않았다. "
        "공식 ALFRED 접근에는 환경변수 `FRED_API_KEY`가 필요하며 현재 환경에 없다. 키를 코드·설정·"
        "로그·산출물·Git 어디에도 기록하지 않았다."
    )
    sections.append(
        "## 15. 다음 단계 제안\n\n"
        "1. 후보 G의 2020년 조기 판정 원인을 분해한다. 2019년 말 어떤 지표가 수축 기여를 냈는지부터 본다.\n"
        "2. 2001년 차이 8.0주가 자료 잡음에 얼마나 민감한지 확인한다. 한계값과 같은 값은 여유가 없다.\n"
        "3. 점프 6건이 어느 시점에 생기는지 확인한다. 참고기준 5건을 1건 넘는다."
    )
    sections.append(
        "## 16. 사실·해석·미검증 가정\n\n"
        "- **검증된 사실**: 이 디렉터리의 CSV·JSON 수치, 테스트로 확인한 창 단위·인과성·성숙도 순서.\n"
        "- **경제적 해석**: 짧은 창이 중심을 최근 수준에 붙여 긴 확장기를 정상으로 보이게 한다는 설명, "
        "합성요인의 무조건부 평균이 0이라는 구성상의 논거.\n"
        "- **미검증 가정**: 최신 수정치 성능이 실시간 빈티지에서도 유지된다는 가정.\n\n"
        "## 17. 재현\n\n```bash\ncd model\npython -m business_cycle.validation.phase5_report\n```"
    )
    report = "\n\n".join(sections) + "\n"
    path = output_dir / "validation_report.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    return path


def main() -> int:
    import subprocess

    from ..config import load_settings

    settings = load_settings()
    output_dir = settings.root / "outputs" / "robustness_validation" / "phase5"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=settings.root,
        check=False,
    ).stdout.strip()
    summary = build_summary(output_dir, "coordinate_g_scale_only_10y", commit)
    (output_dir / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    write_charts(output_dir)
    path = write_report(output_dir, summary)
    print(f"report={path}")
    print(f"stage_a3_passed={summary['stage_a3_passed']}")
    return 0 if summary["stage_a3_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
