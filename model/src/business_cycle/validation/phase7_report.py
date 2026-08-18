"""단계 B 산출물: 실시간 경로를 검증하고 최신 수정치 판정과 대조한다.

여기서 하는 일은 세 가지다.

1. 완성된 실시간 경로가 실제로 요구한 창을 빠짐없이 덮는지 검사한다.
2. 같은 주에 대해 "그때의 판정"과 "지금 자료의 판정"을 나란히 두고 차이를 센다.
3. 침체가 한 건뿐인 창이라는 사실을 결론에 그대로 박아 둔다.

동결 설정은 읽기만 한다. ALFRED 결과로 임계값을 조정하지 않는다.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import load_settings
from .phase7 import (
    FROZEN_CANDIDATE,
    NBER_2020,
    compare_with_latest_vintage,
    detection_summary,
    verify_frozen_configuration,
)

EXPECTED_WEEKS = 688
EXPECTED_FIRST = "2013-06-14"
EXPECTED_LAST = "2026-08-14"

REQUIRED_FILES = (
    "realtime_path.csv",
    "vintage_provenance.csv",
    "latest_vintage_history.csv",
)


def verify_outputs(output_dir: Path, frozen_dir: Path) -> dict[str, Any]:
    """완성된 결과가 쓸 수 있는 물건인지 먼저 확인한다."""

    realtime = pd.read_csv(output_dir / "realtime_path.csv")
    weeks = pd.DatetimeIndex(pd.to_datetime(realtime["as_of"]))
    expected = pd.date_range(EXPECTED_FIRST, EXPECTED_LAST, freq="W-FRI")
    missing = sorted(str(week.date()) for week in expected.difference(weeks))
    duplicated = sorted(realtime.loc[realtime["as_of"].duplicated(), "as_of"].astype(str))
    settings = load_settings()
    hash_matches, recorded, _ = verify_frozen_configuration(settings, frozen_dir)
    empty = [
        name
        for name in REQUIRED_FILES
        if not (output_dir / name).exists() or (output_dir / name).stat().st_size == 0
    ]
    errors = int((realtime["status"] == "error").sum()) if "status" in realtime else 0
    return {
        "week_count": int(len(realtime)),
        "week_count_matches_688": bool(len(realtime) == EXPECTED_WEEKS),
        "first_as_of": str(weeks.min().date()),
        "last_as_of": str(weeks.max().date()),
        "first_matches": bool(str(weeks.min().date()) == EXPECTED_FIRST),
        "last_matches": bool(str(weeks.max().date()) == EXPECTED_LAST),
        "missing_weeks": missing,
        "duplicate_weeks": duplicated,
        "error_weeks": errors,
        "empty_or_missing_files": empty,
        "frozen_hash": recorded,
        "frozen_hash_matches": hash_matches,
        "valid": bool(
            len(realtime) == EXPECTED_WEEKS
            and not missing
            and not duplicated
            and not empty
            and not errors
            and hash_matches
        ),
    }


def revision_effects(comparison: pd.DataFrame) -> dict[str, Any]:
    """수정 때문에 판정이 달라진 정도를 센다.

    같은 주를 두고 그때의 자료로는 A, 지금 자료로는 B라고 부른다면 그 차이는
    모델이 아니라 자료 수정에서 온다.
    """

    broad = comparison["broad_agrees"].astype(bool)
    detail = comparison["detail_agrees"].astype(bool)
    difference = pd.to_numeric(comparison["y_difference"], errors="coerce")
    switches = comparison.loc[~broad, ["as_of", "realtime_broad", "latest_broad"]]
    pairs = (
        switches.assign(pair=switches["realtime_broad"] + " -> " + switches["latest_broad"])["pair"]
        .value_counts()
        .to_dict()
    )
    return {
        "weeks_compared": int(len(comparison)),
        "broad_phase_agreement": float(broad.mean()),
        "detail_phase_agreement": float(detail.mean()),
        "broad_phase_disagreement_weeks": int((~broad).sum()),
        "detail_phase_disagreement_weeks": int((~detail).sum()),
        "broad_disagreement_pairs": pairs,
        "y_difference_mean": float(difference.mean()),
        "y_difference_mean_absolute": float(difference.abs().mean()),
        "y_difference_max_absolute": float(difference.abs().max()),
    }


def stability_diagnostics(realtime: pd.DataFrame) -> dict[str, Any]:
    """현재 국면 안정성과 영역 폭 게이트 안정성."""

    weeks = pd.DatetimeIndex(pd.to_datetime(realtime["as_of"]))
    detail = realtime["detail_phase"].astype(str)
    broad = realtime["broad_phase"].astype(str)
    breadth = pd.to_numeric(realtime["negative_domains"], errors="coerce")
    minimum = pd.to_numeric(realtime["breadth_minimum"], errors="coerce")
    contraction = broad.eq("contraction")
    gate_binding = contraction.to_numpy() & (breadth < minimum).to_numpy()
    recent = realtime.tail(52)
    return {
        "broad_phase_changes": int((broad != broad.shift()).sum() - 1),
        "detail_phase_changes": int((detail != detail.shift()).sum() - 1),
        "broad_phase_share": {
            key: round(value, 4) for key, value in broad.value_counts(normalize=True).items()
        },
        "last_52_week_broad_changes": int(
            (recent["broad_phase"] != recent["broad_phase"].shift()).sum() - 1
        ),
        "final_as_of": str(weeks.max().date()),
        "final_broad_phase": str(broad.iloc[-1]),
        "final_detail_phase": str(detail.iloc[-1]),
        "final_top_probability": float(realtime["top_probability"].iloc[-1]),
        "final_runner_up": str(realtime["runner_up"].iloc[-1]),
        "breadth_minimum_observed": (
            float(minimum.dropna().unique()[0]) if minimum.notna().any() else float("nan")
        ),
        "breadth_minimum_constant": bool(minimum.dropna().nunique() <= 1),
        "negative_domain_median": float(breadth.median()),
        "weeks_breadth_below_minimum": int((breadth < minimum).sum()),
        "contraction_weeks_with_insufficient_breadth": int(gate_binding.sum()),
        "official_status_weeks": int((realtime["status"] == "official").sum()),
        "non_official_status_weeks": int((realtime["status"] != "official").sum()),
    }


def build_summary(output_dir: Path, frozen_dir: Path) -> dict[str, Any]:
    realtime = pd.read_csv(output_dir / "realtime_path.csv")
    latest = pd.read_csv(output_dir / "latest_vintage_history.csv", index_col=0, parse_dates=True)
    comparison = compare_with_latest_vintage(realtime, latest)
    comparison.to_csv(output_dir / "realtime_vs_latest.csv", index=False)
    start, end = NBER_2020
    return {
        "evidence_layers": {
            "A_latest_vintage_development": "1995-01-01..2026-08-14 (기존 결과, 재생성하지 않음)",
            "B_strict_alfred_validation": f"{EXPECTED_FIRST}..{EXPECTED_LAST}",
            "C_earlier_partial_indicators": "만들지 않았다. 선행 계열 대체나 재정규화를 하지 않는다.",
        },
        "frozen_candidate": FROZEN_CANDIDATE,
        "thresholds_changed_for_alfred": False,
        "verification": verify_outputs(output_dir, frozen_dir),
        "detection_2020": detection_summary(realtime),
        "revision_effects": revision_effects(comparison),
        "stability": stability_diagnostics(realtime),
        "nber_window": {"start": str(start.date()), "end": str(end.date())},
        "statistical_limitation": (
            "엄격 창에는 NBER 침체가 2020년 하나뿐이다. 침체 한 건으로는 실시간 침체 탐지 "
            "성능을 일반화할 수 없다. 재현율·오탐률을 여러 순환에 걸쳐 검증했다고 말할 수 "
            "없으며, 2001년과 금융위기는 이 자료로 실시간 검증이 불가능하다. 여기서 확인할 수 "
            "있는 것은 2020년 탐지 시점, 침체 전후 오탐, 수정에 따른 판정 변화, 최초 공개와 "
            "최신 수정치의 불일치, 현재 국면 안정성, 영역 폭 게이트 안정성뿐이다."
        ),
    }


def write_charts(output_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    charts = output_dir / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    realtime = pd.read_csv(output_dir / "realtime_path.csv")
    weeks = pd.DatetimeIndex(pd.to_datetime(realtime["as_of"]))
    comparison = pd.read_csv(output_dir / "realtime_vs_latest.csv")
    start, end = NBER_2020
    # matplotlib 스텁은 축 좌표를 float로 받는다. 날짜를 축 좌표(1일=1.0)로 바꿔 넘긴다.
    epoch = pd.Timestamp("1970-01-01")
    span_start = float((start - epoch).days)
    span_end = float((end - epoch).days)

    figure, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    days = (weeks - epoch).days.to_numpy(dtype=float)
    axes[0].plot(days, realtime["contraction_probability"], label="contraction")
    axes[0].plot(days, realtime["slowdown_probability"], label="slowdown")
    axes[0].axvspan(span_start, span_end, color="red", alpha=0.2, label="NBER 2020")
    axes[0].set_ylabel("probability")
    axes[0].legend(fontsize=8)
    axes[0].set_title(
        "Real-time broad-phase probabilities (strict ALFRED window, x = days since 1970)"
    )
    axes[1].plot(days, realtime["y"], label="Y (real time)")
    axes[1].plot(days, realtime["radius"], label="radius")
    axes[1].axvspan(span_start, span_end, color="red", alpha=0.2)
    axes[1].legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(charts / "01_realtime_path.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(
        (pd.DatetimeIndex(pd.to_datetime(comparison["as_of"])) - epoch).days.to_numpy(dtype=float),
        pd.to_numeric(comparison["y_difference"], errors="coerce"),
    )
    axis.axhline(0, color="black", linewidth=0.8)
    axis.axvspan(span_start, span_end, color="red", alpha=0.2)
    axis.set_title("Y difference: real time minus latest vintage")
    figure.tight_layout()
    figure.savefig(charts / "02_revision_effect.png", dpi=150)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(12, 4))
    axis.plot(
        weeks,
        pd.to_numeric(realtime["negative_domains"], errors="coerce"),
        label="negative domains",
    )
    axis.plot(
        weeks,
        pd.to_numeric(realtime["breadth_minimum"], errors="coerce"),
        linestyle="--",
        label="gate minimum",
    )
    axis.axvspan(span_start, span_end, color="red", alpha=0.2)
    axis.legend(fontsize=8)
    axis.set_title("Domain breadth in real time")
    figure.tight_layout()
    figure.savefig(charts / "03_breadth_gate.png", dpi=150)
    plt.close(figure)

    window = (weeks >= pd.Timestamp("2019-06-01")) & (weeks <= pd.Timestamp("2021-06-30"))
    figure, axis = plt.subplots(figsize=(12, 5))
    axis.plot(
        weeks[window], realtime.loc[window, "contraction_probability"], marker="o", markersize=2
    )
    axis.axvspan(span_start, span_end, color="red", alpha=0.2, label="NBER 2020")
    axis.set_title("2020 detection in real time")
    axis.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(charts / "04_case_2020.png", dpi=150)
    plt.close(figure)


def main() -> int:
    settings = load_settings()
    output_dir = settings.root / "outputs" / "robustness_validation" / "phase7"
    frozen_dir = settings.root / "outputs" / "robustness_validation" / "phase6"
    summary = build_summary(output_dir, frozen_dir)
    (output_dir / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    write_charts(output_dir)
    verification = summary["verification"]
    print(f"valid={verification['valid']}")
    print(
        f"weeks={verification['week_count']} "
        f"{verification['first_as_of']}..{verification['last_as_of']} "
        f"missing={len(verification['missing_weeks'])} "
        f"duplicates={len(verification['duplicate_weeks'])} "
        f"frozen_hash_matches={verification['frozen_hash_matches']}"
    )
    return 0 if verification["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
