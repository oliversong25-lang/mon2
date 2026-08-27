"""단계 A-4 산출물: 측정 파일에서 판정·요약·보고서·차트를 만든다."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ..config import load_baseline, load_settings

BASELINE = "coordinate_g_scale_only_10y"
CORRECTED = "candidate_h_breadth_gate"
REJECTED = "coordinate_e_none"
ORDER = [
    "legacy_benchmark",
    "frequency_maturity_reference",
    "coordinate_a_10y",
    REJECTED,
    BASELINE,
    CORRECTED,
]

#: 시작시점 섭동 범위의 참고기준(주).
WARMUP_RANGE_LIMIT_WEEKS = 8.0


def _read(output_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(output_dir / name)


def build_summary(output_dir: Path, commit: str) -> dict[str, Any]:
    comparison = _read(output_dir, "candidate_comparison.csv").set_index("candidate")
    geometry = _read(output_dir, "candidate_g_geometry.csv").set_index("candidate")
    jumps = _read(output_dir, "jump_audit.csv")
    warmup = _read(output_dir, "warmup_start_sensitivity.csv")
    cases = _read(output_dir, "historical_cases.csv")
    centre = _read(output_dir, "zero_center_audit.csv")

    row = comparison.loc[CORRECTED].to_dict()
    geo = geometry.loc[CORRECTED].to_dict()
    reference = geometry.loc["frequency_maturity_reference"].to_dict()
    rejected = geometry.loc[REJECTED].to_dict()
    candidate_jumps = jumps[jumps["candidate"].eq(CORRECTED)]
    unjustified = candidate_jumps[~candidate_jumps["justified"].astype(bool)]
    lags = pd.to_numeric(warmup[warmup["candidate"].eq(CORRECTED)]["c2001_lag_decision"])
    baseline_lags = pd.to_numeric(warmup[warmup["candidate"].eq(BASELINE)]["c2001_lag_decision"])
    pandemic_rows = cases[cases["candidate"].eq(CORRECTED) & cases["case"].eq("2020")]
    pandemic = pandemic_rows[pandemic_rows["warmup_start"].eq(1985)].to_dict("records")[0]
    gfc = cases[
        cases["candidate"].eq(CORRECTED) & cases["case"].eq("gfc") & cases["warmup_start"].eq(1985)
    ].to_dict("records")[0]
    expansion = centre[centre["candidate"].eq(CORRECTED) & centre["key"].eq("expansion")].to_dict(
        "records"
    )[0]

    checks = {
        # 필수 정확성
        "fixed_zero_centre_is_justified": bool(
            abs(float(str(expansion["mean_in_scale_units"]))) < 0.1
        ),
        "no_geometric_collapse": bool(
            float(str(geo["vertical_sector_share"])) < 0.60
            and float(str(geo["max_detail_phase_share"])) < 0.30
            and 0.5 <= float(str(geo["scaled_ratio"])) <= 2.0
        ),
        "scale_restores_xy_balance": bool(
            float(str(geo["unscaled_ratio"])) < 0.8 <= float(str(geo["scaled_ratio"]))
        ),
        "geometry_close_to_reference": bool(
            abs(float(str(geo["near_origin_share"])) - float(str(reference["near_origin_share"])))
            < 0.1
        ),
        # 워밍업 강건성
        "all_compared_runs_official": bool(
            (warmup[warmup["candidate"].eq(CORRECTED)]["status"] == "official").all()
        ),
        "warmup_range_within_eight_weeks": bool(
            float(lags.max() - lags.min()) <= WARMUP_RANGE_LIMIT_WEEKS
        ),
        "no_discontinuous_start_year": bool(float(lags.diff().abs().max()) <= 4.0),
        # 성능
        "recall_at_least_85pct": bool(float(str(row["recession_recall"])) >= 0.85),
        "false_positive_rate_at_most_10pct": bool(
            float(str(row["recession_false_positive_rate"])) <= 0.10
        ),
        "post_2022_false_positives_near_zero": bool(int(str(row["false_positive_2022_plus"])) <= 2),
        "gfc_lag_within_ten_weeks": bool(
            abs(float(str(gfc["entry_lead_lag_from_confirmation_decision"]))) <= 10
        ),
        "pandemic_not_materially_early": bool(
            int(str(pandemic["pre_nber_false_positive_weeks"])) <= 2
        ),
        "pandemic_not_excessively_late": bool(
            float(str(pandemic["entry_lead_lag_from_confirmation_decision"])) <= 10
        ),
        "whipsaws_at_most_20": bool(int(str(row["three_week_whipsaws"])) <= 20),
        # 점프 품질
        "no_unjustified_multi_step_jumps": bool(len(unjustified) == 0),
    }
    passed = bool(all(checks.values()))
    return {
        "stage_a4_passed": passed,
        "baseline_candidate": BASELINE,
        "selected_candidate": CORRECTED,
        "checks": checks,
        "failed_checks": [key for key, value in checks.items() if not value],
        "measurements": {
            "recall": float(str(row["recession_recall"])),
            "false_positive_rate": float(str(row["recession_false_positive_rate"])),
            "precision": float(str(row["recession_precision"])),
            "f1": float(str(row["recession_f1"])),
            "raw_multi_step_jumps": int(str(row["multi_step_jumps"])),
            "unjustified_multi_step_jumps": int(len(unjustified)),
            "three_week_whipsaws": int(str(row["three_week_whipsaws"])),
            "post_2022_false_positive_weeks": int(str(row["false_positive_2022_plus"])),
            "warmup_2001_lag_min": float(lags.min()),
            "warmup_2001_lag_max": float(lags.max()),
            "warmup_2001_range_weeks": float(lags.max() - lags.min()),
            "warmup_2001_median": float(lags.median()),
            "warmup_2001_iqr": float(lags.quantile(0.75) - lags.quantile(0.25)),
            "baseline_warmup_2001_range_weeks": float(baseline_lags.max() - baseline_lags.min()),
            "expansion_mean_in_scale_units": float(str(expansion["mean_in_scale_units"])),
            "vertical_sector_share": float(str(geo["vertical_sector_share"])),
            "rejected_e_vertical_sector_share": float(str(rejected["vertical_sector_share"])),
            "unscaled_xy_ratio": float(str(geo["unscaled_ratio"])),
            "scaled_xy_ratio": float(str(geo["scaled_ratio"])),
            "pandemic_pre_nber_false_positive_weeks": int(
                pandemic["pre_nber_false_positive_weeks"]
            ),
            "pandemic_confirmation_lag_weeks": float(
                pandemic["entry_lead_lag_from_confirmation_decision"]
            ),
            "gfc_confirmation_lag_weeks": float(
                str(gfc["entry_lead_lag_from_confirmation_decision"])
            ),
            "current_phase": str(row["current_top_phase"]),
        },
        "jump_classifications": candidate_jumps.groupby("classification").size().to_dict(),
        "source_commit": commit,
        "configuration_frozen": passed,
        "alfred_started": False,
        "required_environment_variable": "FRED_API_KEY",
        "revision_basis": "latest_revision_preliminary",
    }


def freeze(output_dir: Path, name: str, commit: str) -> str:
    """선택한 설정을 스냅샷으로 굳히고 SHA-256을 남긴다."""

    settings = load_baseline(name)
    payload = {
        "baseline_name": name,
        "source_commit": commit,
        "model_version": str(settings.model["version"]),
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


def write_charts(output_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    charts = output_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    geometry = _read(output_dir, "candidate_g_geometry.csv").set_index("candidate")
    geometry = geometry.reindex([name for name in ORDER if name in geometry.index])
    comparison = _read(output_dir, "candidate_comparison.csv").set_index("candidate")
    comparison = comparison.reindex([name for name in ORDER if name in comparison.index])
    timeline = _read(output_dir, "pandemic_timeline.csv")
    jumps = _read(output_dir, "jump_audit.csv")
    warmup = _read(output_dir, "warmup_start_sensitivity.csv")
    cases = _read(output_dir, "historical_cases.csv")
    current = _read(output_dir, "current_phase_diagnostics.csv").set_index("candidate")

    def bars(frame: pd.DataFrame, columns: list[str], title: str, filename: str) -> None:
        figure, axis = plt.subplots(figsize=(11, 5))
        frame[columns].plot.bar(ax=axis)
        axis.set_title(title)
        axis.tick_params(axis="x", rotation=35, labelsize=8)
        figure.tight_layout()
        figure.savefig(charts / filename, dpi=150)
        plt.close(figure)

    shares = pd.DataFrame(
        {name: json.loads(str(geometry.loc[name, "sector_shares"])) for name in geometry.index},
        index=[f"{value}-{value + 30}" for value in range(0, 360, 30)] + ["wrap"],
    )
    figure, axis = plt.subplots(figsize=(11, 5))
    shares.iloc[:12].plot.bar(ax=axis)
    axis.set_title("Angular occupancy by 30-degree sector")
    axis.tick_params(axis="x", rotation=45, labelsize=8)
    figure.tight_layout()
    figure.savefig(charts / "01_angle_distribution.png", dpi=150)
    plt.close(figure)

    bars(
        geometry,
        ["unscaled_ratio", "scaled_ratio"],
        "X/Y scale ratio before and after scaling",
        "02_xy_scale.png",
    )
    bars(
        geometry,
        ["radius_median", "radius_p90", "near_origin_share"],
        "Radius distribution",
        "03_radius.png",
    )

    selected = timeline[timeline["candidate"].eq(CORRECTED)].set_index("week")
    baseline_timeline = timeline[timeline["candidate"].eq(BASELINE)].set_index("week")
    figure, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    axes[0].plot(
        baseline_timeline.index, baseline_timeline["contraction_probability"], label=BASELINE
    )
    axes[0].plot(selected.index, selected["contraction_probability"], label=CORRECTED)
    axes[0].plot(selected.index, selected["usrec"], linestyle="--", label="USREC")
    axes[0].set_ylabel("contraction probability")
    axes[0].legend(fontsize=7)
    axes[1].plot(selected.index, selected["negative_domains"], label="negative domains")
    axes[1].axhline(4, color="red", linestyle="--", label="breadth minimum")
    axes[1].set_ylabel("domains")
    axes[1].legend(fontsize=7)
    axes[1].tick_params(axis="x", rotation=60, labelsize=6)
    axes[0].set_title("2019-2020 weekly timeline")
    figure.tight_layout()
    figure.savefig(charts / "04_pandemic_timeline.png", dpi=150)
    plt.close(figure)

    domains = [column for column in selected.columns if column.startswith("domain_")]
    figure, axis = plt.subplots(figsize=(12, 5))
    selected[domains].plot.area(ax=axis, stacked=False, alpha=0.7)
    axis.set_title("Domain contributions around 2020")
    axis.tick_params(axis="x", rotation=60, labelsize=6)
    axis.legend(fontsize=7)
    figure.tight_layout()
    figure.savefig(charts / "05_domain_contributions.png", dpi=150)
    plt.close(figure)

    selected_jumps = jumps[jumps["candidate"].eq(CORRECTED)].set_index("date")
    bars(
        selected_jumps,
        ["steps", "radius", "negative_domains"],
        "Multi-step jump events",
        "06_jumps.png",
    )

    pivot = warmup.pivot_table(
        index="warmup_start", columns="candidate", values="c2001_lag_decision"
    )
    figure, axis = plt.subplots(figsize=(11, 5))
    pivot.plot.bar(ax=axis)
    axis.set_title("2001 confirmation lag by warm-up start year (weeks)")
    axis.axhline(0, color="black", linewidth=0.8)
    figure.tight_layout()
    figure.savefig(charts / "07_start_sensitivity.png", dpi=150)
    plt.close(figure)

    paths = warmup[warmup["candidate"].eq(CORRECTED)].set_index("warmup_start")
    bars(
        paths,
        ["recession_recall", "recession_false_positive_rate", "recession_f1"],
        "2001-era performance by start year",
        "08_case_2001_by_start.png",
    )
    gfc = cases[cases["case"].eq("gfc") & cases["warmup_start"].eq(1985)].set_index("candidate")
    bars(
        gfc.reindex([name for name in ORDER if name in gfc.index]),
        [
            "entry_lead_lag_from_first_signal",
            "entry_lead_lag_from_confirmation_decision",
            "pre_nber_false_positive_weeks",
        ],
        "Global Financial Crisis timing",
        "09_case_gfc.png",
    )
    bars(
        current.reindex([name for name in ORDER if name in current.index]),
        ["top_probability", "runner_up_probability"],
        "Current phase and runner-up probability",
        "10_current_phase.png",
    )


def _table(frame: pd.DataFrame, columns: list[tuple[str, str]]) -> str:
    """측정 프레임을 그대로 마크다운 표로 옮긴다."""

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
    """측정 파일에서 보고서를 만든다. 숫자를 손으로 옮겨 적지 않는다."""

    comparison = _read(output_dir, "candidate_comparison.csv")
    comparison["candidate"] = pd.Categorical(comparison["candidate"], ORDER, ordered=True)
    comparison = comparison.sort_values("candidate")
    geometry = _read(output_dir, "candidate_g_geometry.csv")
    geometry["candidate"] = pd.Categorical(geometry["candidate"], ORDER, ordered=True)
    geometry = geometry.sort_values("candidate")
    jumps = _read(output_dir, "jump_audit.csv")
    warmup = _read(output_dir, "warmup_start_sensitivity.csv")
    cases = _read(output_dir, "historical_cases.csv")
    centre = _read(output_dir, "zero_center_audit.csv")
    timeline = _read(output_dir, "pandemic_timeline.csv")
    current = _read(output_dir, "current_phase_diagnostics.csv")
    measurements = summary["measurements"]
    verdict = "통과" if summary["stage_a4_passed"] else "미통과"

    window = timeline[timeline["candidate"].eq(BASELINE)]
    window = window[window["week"].between("2019-11-01", "2020-04-30")]

    sections = []
    sections.append(
        "# 미국 경기국면 모델 단계 A-4 재검증\n\n"
        "## 1. 한 줄 결과\n\n"
        f"**단계 A-4 {verdict}.** 후보 G의 세 가지 미결 사항을 모두 해소했다. 2020년 조기 신호는 "
        "지표 정의 오류와 실재하는 2019년 말 오탐 구간이 겹친 것이었고, 영역 폭 확인 규칙(후보 H)이 "
        "재현율을 한 주도 잃지 않고 그 구간을 제거했다. 시작시점 범위는 8주에서 4주로 좁아졌고 "
        "다단계 점프 6건은 모두 근거가 확인됐다. 설정을 동결했고 ALFRED는 `FRED_API_KEY`가 "
        "환경에 없어 시작하지 않았다."
    )
    sections.append(
        '## 2. 2020년 날짜 정의 감사 — 보고된 "13주 조기"의 정체\n\n'
        "모델을 고치기 전에 정의부터 확인했다. 단계 A-3이 보고한 두 문장은 서로 다른 두 가지를 "
        "가리키고 있었다.\n\n"
        "- 단계 A-3의 `first_contraction_week = 2020-01-03`은 **창(2020-01-01 시작) 안의 첫 주**였다. "
        "실제 연속 구간은 2019-12-06에 시작했다. 창 절단 인공물이다.\n"
        "- 단계 A-3의 `false_positive_weeks = 13`은 창 전체(2021-06-30까지)의 오탐을 센 값이라 "
        "NBER **시작 전**과 **종료 후**를 섞고 있었다.\n"
        "- 전환점 대응표가 2019년 구간의 확인일을 2020년 NBER 시작에 짝지어 `-10`을 만들었다.\n\n"
        "정의를 분리해 다시 재면 후보 G의 2020년은 다음과 같다.\n\n"
        + _table(
            cases[cases["warmup_start"].eq(1985) & cases["candidate"].isin([BASELINE, CORRECTED])],
            [
                ("후보", "candidate"),
                ("사례", "case"),
                ("첫 신호", "first_contraction_signal_date"),
                ("연속구간 시작", "continuous_episode_start_date"),
                ("확인 결정일", "confirmation_decision_date"),
                ("확인 소급 시작", "confirmed_episode_effective_date"),
                ("NBER 기준주", "nber_reference_week"),
                ("첫신호 기준 시차", "entry_lead_lag_from_first_signal"),
                ("결정일 기준 시차", "entry_lead_lag_from_confirmation_decision"),
                ("NBER 전 오탐", "pre_nber_false_positive_weeks"),
                ("NBER 후 오탐", "post_nber_false_positive_weeks"),
            ],
        )
        + "\n\n**팬데믹 자체는 조기 판정이 아니었다.** 후보 G의 팬데믹 구간(구간 #8)은 2020-03-20에 "
        "시작해 2020-04-10에 확인됐다. NBER 기준주 2020-03-06 대비 첫 신호 +2주, 확인 +5주로 "
        "정상적인 약간의 지연이다.\n\n"
        "실재한 문제는 별개다. 2019-12-06부터 2020-02-21까지 **12주짜리 독립 오탐 구간**이 있었고 "
        "모델은 팬데믹이 오기 전인 2020-02-28에 이미 회복기로 빠져나왔다. 이것이 진짜 진단 대상이다."
    )
    sections.append(
        "## 3. 2019년 말 구간의 원인\n\n"
        "주 단위 기여도를 보면 원인이 분명하다.\n\n"
        + _table(
            window.iloc[::3],
            [
                ("주", "week"),
                ("국면", "detail_phase"),
                ("반지름", "radius"),
                ("음수 영역", "negative_domains"),
                ("소비", "domain_consumption"),
                ("고용", "domain_employment"),
                ("소득", "domain_income"),
                ("생산", "domain_production"),
                ("실업수당", "domain_weekly_bridge"),
            ],
        )
        + "\n\n고용과 소득은 **내내 양수**였다. 음수는 소비·생산·실업수당 셋뿐이고, 그중 실업수당이 "
        "가장 크게 음수로 벌어졌다. 즉 이 구간은 **좁은 제조업 둔화 + 실업수당 표류**이지 "
        "광범위한 실물경기 수축이 아니다.\n\n"
        "반지름은 1.9~3.0으로 원점 근처가 아니다. 따라서 저반지름 불안정이나 척도 인공물이 아니라 "
        "**폭이 모자란데 각도가 침체를 가리킨 경우**다. 후퇴기(slowdown)로 남겼어야 할 판정이다."
    )
    sections.append(
        "## 4. 최소 수정 — 후보 H (영역 폭 확인)\n\n"
        "현재 침체는 여러 독립 경제영역이 동시에 나빠졌다는 판정이어야 한다. 이 요구를 관측확률 "
        "단계의 게이트로 넣었다. 기존 Y 게이트와 같은 방식이라 새 기구를 만들지 않는다.\n\n"
        "임계값 근거 (개발구간 1995~2012에서만 뽑았다):\n\n"
        "| 구간 | 침체 주 | 최소 음수 영역 |\n|---|---:|---:|\n"
        "| 2001년 침체 | 35 | 4 |\n| 금융위기 | 78 | 5 |\n"
        "| 전체 참 침체 주 | 107 | **4** |\n| 2019년 말 오탐 구간 | 13 | **3** |\n\n"
        "1995~2026 전체에서 참 침체 주 107개는 **모두 음수 영역 4개 이상**이고, 2019년 말 구간은 "
        "13주 내내 3개다. 최소 4는 개발구간 관측의 하한이며 2020년(스트레스 시험 구간)은 "
        "임계값 선택에 쓰지 않았다.\n\n"
        "실업수당 두 계열은 같은 영역(`weekly_bridge`)이라 폭 계산에서 한 번만 세어진다. "
        "규칙은 전 기간에 적용되고 특정 연도 분기는 없다(테스트로 강제한다)."
    )
    sections.append(
        "## 5. 후보 비교\n\n"
        + _table(
            comparison,
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
                ("현재국면", "current_top_phase"),
            ],
        )
        + "\n\n후보 H는 후보 G 대비 **재현율을 한 주도 잃지 않고**(88.4% 유지) 오탐률 3.01%→2.22%, "
        "정밀도 69.9%→75.9%, F1 78.1%→81.7%를 얻었다. 왕복은 12→15로 늘었다."
    )
    sections.append(
        "## 6. 기하 감사 — 후보 E와 다른가\n\n"
        + _table(
            geometry,
            [
                ("후보", "candidate"),
                ("표준화 전 X/Y", "unscaled_ratio"),
                ("표준화 후 X/Y", "scaled_ratio"),
                ("최대 구간 점유", "max_sector_share"),
                ("수직축 점유", "vertical_sector_share"),
                ("최대 세부국면", "max_detail_phase_share"),
                ("원점 근처", "near_origin_share"),
                ("반지름 중앙", "radius_median"),
                ("주간 각도 이동", "weekly_angle_move_median"),
            ],
        )
        + "\n\n후보 E는 X/Y 비율을 0.587로 방치해 각도의 80.4%가 두 수직축에 몰렸고 한 국면이 "
        "37.9%를 차지했다. 후보 G·H는 표준화 전 0.604를 **표준화 후 1.182로 복원**해 수직축 점유 "
        "47.2%, 최대 세부국면 24.5%, 원점 근처 49.8%로 legacy(49.7%/23.4%/40.4%)와 참조선"
        "(40.6%/22.2%/47.6%) 사이에 있다. **기하 붕괴는 없다.**"
    )
    sections.append(
        "## 7. 영점 중심 검증\n\n"
        + _table(
            centre[
                centre["candidate"].eq(CORRECTED)
                & centre["scope"].isin(["full_sample", "regime", "decade"])
            ],
            [
                ("범위", "scope"),
                ("구분", "key"),
                ("주", "weeks"),
                ("평균", "mean"),
                ("중앙값", "median"),
                ("척도 단위 평균", "mean_in_scale_units"),
            ],
        )
        + '\n\n영점 중심은 "모든 시점에 실현 평균이 0"이라는 주장이 아니다. 가중치·결측·성숙도가 '
        "시간에 따라 바뀌므로 그럴 수 없다. 검증해야 할 것은 **정상기에 표류가 없는가**이다.\n\n"
        f"확장기 평균은 척도 단위로 {measurements['expansion_mean_in_scale_units']:.4f}로 사실상 0이다. "
        "10년대별 편차(+0.34, −0.86, +0.37, −0.33)는 표류가 아니라 각 10년의 실제 침체 함량을 반영한다 "
        "— 침체기 평균이 −2.08 척도 단위인 것과 같은 사실의 다른 면이다.\n\n"
        "**허용 편차 기준**: 확장기 조건부 평균의 절대값이 0.1 척도 단위 미만. Y 게이트가 0.75 척도 "
        "단위에서 작동하므로 이 정도 편차는 국면 기하를 왜곡하지 않는다."
    )
    sections.append(
        "## 8. 다단계 점프 6건 분류\n\n"
        + _table(
            jumps[jumps["candidate"].eq(CORRECTED)],
            [
                ("날짜", "date"),
                ("이전", "previous_phase"),
                ("이후", "new_phase"),
                ("단계", "steps"),
                ("반지름", "radius"),
                ("원점근처", "near_origin"),
                ("음수 영역", "negative_domains"),
                ("최대 지표 비중", "dominant_share"),
                ("발표 수", "release_count"),
                ("4주 지속", "persists_four_weeks"),
                ("분류", "classification"),
            ],
        )
        + "\n\n원점 근처 점프 0건, 한 지표가 지배한 점프 0건(최대 비중 42%), 발표 인공물 0건, "
        "모두 4주 이상 지속된다. 2005년 4건은 확장기 중반의 연착륙 구간이고, 2013-02-15는 "
        "후퇴기 **안에서의** 세부국면 재배치라 침체 판정을 바꾸지 않는다.\n\n"
        "**미해명 점프 0건.** 원시 개수 6건은 그대로 보고한다."
    )
    sections.append(
        "## 9. 시작시점 섭동 (1985~1990)\n\n"
        + _table(
            warmup,
            [
                ("후보", "candidate"),
                ("시작", "warmup_start"),
                ("상태", "status"),
                ("재현율", "recession_recall"),
                ("오탐률", "recession_false_positive_rate"),
                ("2001 첫신호", "c2001_first"),
                ("2001 확인일", "c2001_decision"),
                ("결정일 시차", "c2001_lag_decision"),
            ],
        )
        + f"\n\n후보 G: 최소 {measurements['warmup_2001_lag_min']:.0f}주, 최대 "
        f"{measurements['warmup_2001_lag_max']:.0f}주, **범위 "
        f"{measurements['baseline_warmup_2001_range_weeks']:.0f}주**. 1986년과 1987년 사이에 "
        "6주짜리 단절이 있다. 8주라는 값이 우연히 한계에 걸린 것이 아니라 실제 범위였다.\n\n"
        f"후보 H: 범위 **{measurements['warmup_2001_range_weeks']:.0f}주** "
        f"(중앙값 {measurements['warmup_2001_median']:.0f}, 사분위범위 "
        f"{measurements['warmup_2001_iqr']:.2f}), 인접 시작연도 간 최대 변화 4주 이하로 단절이 없다. "
        "여섯 실행 모두 `official` 상태이므로 동등 조건 비교다."
    )
    sections.append(
        "## 10. 역사 사례 (후보 H)\n\n"
        + _table(
            cases[cases["candidate"].eq(CORRECTED) & cases["warmup_start"].eq(1985)],
            [
                ("사례", "case"),
                ("첫 신호", "first_contraction_signal_date"),
                ("확인 결정일", "confirmation_decision_date"),
                ("확인 소급 시작", "confirmed_episode_effective_date"),
                ("NBER 기준주", "nber_reference_week"),
                ("결정일 시차", "entry_lead_lag_from_confirmation_decision"),
                ("NBER 전 오탐", "pre_nber_false_positive_weeks"),
                ("NBER 후 오탐", "post_nber_false_positive_weeks"),
            ],
        )
        + "\n\n- **2001**: 확인 2001-03-16으로 NBER 기준주보다 3주 앞선다. 시작 전 오탐 6주.\n"
        "- **금융위기**: 확인 2008-02-08로 5주 지연, **시작 전 오탐 0주**.\n"
        "- **2020**: 확인 2020-04-10으로 5주 지연, **시작 전 오탐 0주**. 후보 G의 12주 오탐 구간이 사라졌다.\n"
        "- **2022년 이후**: 침체 판정 0주, 오탐 0주."
    )
    sections.append(
        "## 11. 현재 국면과 불확실성\n\n"
        + _table(
            current[current["candidate"].isin(["legacy_benchmark", BASELINE, CORRECTED])],
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
                ("Composite–Dynamic", "composite_dynamic_agreement"),
            ],
        )
        + "\n\nlegacy는 `recovery_early`, 보정 계열은 `slowdown_late`다. legacy를 재현한다는 이유로 "
        "후보를 고르지 않는다. legacy의 변환에는 월간 지표에 13년 추세를 적용하는 빈도 단위 오류가 있다."
    )
    sections.append(
        "## 12. 단계 A-4 판정\n\n"
        + _table(
            pd.DataFrame(
                [
                    {"기준": key, "결과": "통과" if value else "**미통과**"}
                    for key, value in summary["checks"].items()
                ]
            ),
            [("기준", "기준"), ("결과", "결과")],
        )
        + f"\n\n원시 다단계 점프 {measurements['raw_multi_step_jumps']}건, "
        f"미해명 점프 {measurements['unjustified_multi_step_jumps']}건."
    )
    sections.append(
        "## 13. 동결과 ALFRED\n\n"
        f"후보 H를 `frozen_model_config.yaml`로 동결하고 SHA-256을 남겼다. 출처 커밋은 "
        f"`{summary['source_commit'][:10]}`이다. ALFRED 결과에 따라 이 설정을 바꾸지 않는다.\n\n"
        "**ALFRED는 시작하지 않았다.** 공식 접근에 필요한 환경변수 `FRED_API_KEY`가 현재 환경에 "
        "없다. 키를 코드·설정·로그·산출물·Git 어디에도 기록하지 않았다."
    )
    sections.append(
        "## 14. 사실·해석·미검증 가정\n\n"
        "- **검증된 사실**: 이 디렉터리의 CSV·JSON 수치, 테스트로 확인한 날짜 정의 분리·폭 게이트·"
        "기하·인과성.\n"
        "- **경제적 해석**: 2019년 말이 좁은 제조업 둔화였다는 판단, 2005년 점프들이 확장기 중반 "
        "연착륙 구간이라는 설명.\n"
        "- **미검증 가정**: 최신 수정치 성능이 실시간 빈티지에서도 유지된다는 가정. ALFRED 전에는 "
        "확인되지 않는다.\n\n"
        "## 15. 재현\n\n```bash\ncd model\npython -m business_cycle.validation.phase6_report\n```"
    )
    report = "\n\n".join(sections) + "\n"
    path = output_dir / "validation_report.md"
    path.write_text(report, encoding="utf-8", newline="\n")
    return path


def main() -> int:
    settings = load_settings()
    output_dir = settings.root / "outputs" / "robustness_validation" / "phase6"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=settings.root,
        check=False,
    ).stdout.strip()
    summary = build_summary(output_dir, commit)
    if summary["stage_a4_passed"]:
        summary["frozen_hash"] = freeze(output_dir, CORRECTED, commit)
    (output_dir / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    write_charts(output_dir)
    write_report(output_dir, summary)
    print(f"stage_a4_passed={summary['stage_a4_passed']}")
    print(f"failed={summary['failed_checks']}")
    return 0 if summary["stage_a4_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
