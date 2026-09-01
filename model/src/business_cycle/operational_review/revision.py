"""§8. 개정 위험 평가. 최신 수정치 결론과 실시간 결론을 섞지 않는다.

직접 개정과, 앞선 개정이 만든 필터 경로 의존은 **다른 원인**이다. 하나로 합치면
"자료가 바뀌어서"와 "상태가 한 번 갈라진 뒤 스스로 유지돼서"를 구분할 수 없게 된다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..four_phase.contract import PHASES

CAUSES: Final[tuple[str, ...]] = (
    "direct_data_revision",
    "filter_path_dependence_after_revision",
    "freshness_or_status_difference",
    "unresolved",
)

#: 직접 개정으로 볼 최소 총량 차이. 이보다 작으면 값이 사실상 같다는 뜻이므로
#: 국면이 갈린 이유를 자료 개정으로 돌릴 수 없다.
MATERIAL_LEVEL_DIFFERENCE: Final[float] = 0.05


def align(realtime: pd.DataFrame, latest: pd.DataFrame) -> pd.DataFrame:
    """as-of 시점에 모델이 실제로 본 마지막 주를 최신 수정치의 같은 주와 맞댄다."""

    rows: list[dict[str, Any]] = []
    for moment in realtime.index:
        modelled = pd.Timestamp(str(realtime.at[moment, "last_modelled_week"]))
        if modelled not in latest.index:
            continue
        reference = latest.loc[modelled]
        rows.append(
            {
                "as_of": str(pd.Timestamp(moment).date()),
                "last_modelled_week": str(modelled.date()),
                "phase_status": str(realtime.at[moment, "phase_status"]),
                "information_lag_weeks": int(str(realtime.at[moment, "information_lag_weeks"])),
                "realtime_raw": str(realtime.at[moment, "raw_phase"]),
                "latest_raw": str(reference["raw_phase"]),
                "realtime_official": str(realtime.at[moment, "official_phase"]),
                "latest_official": str(reference["official_phase"]),
                "realtime_alert": str(realtime.at[moment, "recession_alert"]),
                "latest_alert": str(reference["recession_alert"]),
                "level_difference": float(str(realtime.at[moment, "activity_level"]))
                - float(str(reference["activity_level"])),
                "momentum_difference": float(str(realtime.at[moment, "activity_momentum"]))
                - float(str(reference["activity_momentum"])),
                "breadth_difference": int(str(realtime.at[moment, "negative_level_domains"]))
                - int(str(reference["negative_level_domains"])),
                "concentration_difference": float(str(realtime.at[moment, "concentration"]))
                - float(str(reference["concentration"])),
                "usrec": int(str(reference["usrec"])),
            }
        )
    frame = pd.DataFrame(rows)
    frame["raw_agrees"] = frame["realtime_raw"] == frame["latest_raw"]
    frame["official_agrees"] = frame["realtime_official"] == frame["latest_official"]
    frame["alert_agrees"] = frame["realtime_alert"] == frame["latest_alert"]
    frame["cause"] = [classify(row) for _, row in frame.iterrows()]
    return frame


def classify(row: pd.Series) -> str:
    """불일치 하나의 주된 원인. 직접 개정과 경로 의존을 합치지 않는다."""

    if bool(row["official_agrees"]):
        return ""
    if str(row["phase_status"]) != "official":
        return "freshness_or_status_difference"
    material = (
        abs(float(row["level_difference"])) > MATERIAL_LEVEL_DIFFERENCE
        or int(row["breadth_difference"]) != 0
    )
    if not bool(row["raw_agrees"]) and material:
        return "direct_data_revision"
    if bool(row["raw_agrees"]) and material:
        # 관측 승자는 같은데 공식 국면이 다르다. 값은 개정됐고, 갈라진 것은 상태다.
        return "filter_path_dependence_after_revision"
    if bool(row["raw_agrees"]):
        return "filter_path_dependence_after_revision"
    return "unresolved"


def confusion(left: pd.Series, right: pd.Series, labels: tuple[str, ...]) -> dict[str, Any]:
    """혼동 행렬. 행이 실시간, 열이 최신 수정치."""

    return {
        row: {column: int(((left == row) & (right == column)).sum()) for column in labels}
        for row in labels
    }


def summarise(frame: pd.DataFrame, latest: pd.DataFrame, window: tuple[str, str]) -> dict[str, Any]:
    """§8이 요구한 항목. 후반 2019 개정 민감도 실패를 지우지 않는다."""

    disagree = ~frame["official_agrees"]
    runs: list[int] = []
    streak = 0
    for value in disagree:
        if bool(value):
            streak += 1
            continue
        if streak:
            runs.append(streak)
        streak = 0
    if streak:
        runs.append(streak)
    labels = (*PHASES, "")
    causes = {name: int((frame["cause"] == name).sum()) for name in CAUSES}
    late = frame[(frame["as_of"] >= "2019-07-01") & (frame["as_of"] <= "2019-12-31")]
    return {
        "window": list(window),
        "weeks": int(len(frame)),
        "raw_phase_agreement": round(float(frame["raw_agrees"].mean()), 6),
        "official_phase_agreement": round(float(frame["official_agrees"].mean()), 6),
        "contraction_agreement": round(
            float(
                (
                    frame["realtime_official"].eq("contraction")
                    == frame["latest_official"].eq("contraction")
                ).mean()
            ),
            6,
        ),
        "alert_agreement": round(float(frame["alert_agrees"].mean()), 6),
        "phase_confusion_matrix": confusion(
            frame["realtime_official"], frame["latest_official"], labels
        ),
        "contraction_confusion_matrix": {
            "realtime_contraction_latest_contraction": int(
                (
                    frame["realtime_official"].eq("contraction")
                    & frame["latest_official"].eq("contraction")
                ).sum()
            ),
            "realtime_contraction_latest_other": int(
                (
                    frame["realtime_official"].eq("contraction")
                    & ~frame["latest_official"].eq("contraction")
                ).sum()
            ),
            "realtime_other_latest_contraction": int(
                (
                    ~frame["realtime_official"].eq("contraction")
                    & frame["latest_official"].eq("contraction")
                ).sum()
            ),
            "realtime_other_latest_other": int(
                (
                    ~frame["realtime_official"].eq("contraction")
                    & ~frame["latest_official"].eq("contraction")
                ).sum()
            ),
        },
        "disagreement_causes": causes,
        "disagreement_duration_distribution": sorted(runs),
        "longest_disagreement_episode_weeks": max(runs) if runs else 0,
        "revision_changed_breadth_weeks": int((frame["breadth_difference"] != 0).sum()),
        "revision_changed_only_severity_weeks": int(
            ((frame["breadth_difference"] == 0) & (frame["level_difference"].abs() > 0.05)).sum()
        ),
        "revision_changed_the_official_phase_weeks": int(disagree.sum()),
        "revision_changed_the_alert_but_not_the_phase_weeks": int(
            (~frame["alert_agrees"] & frame["official_agrees"]).sum()
        ),
        "late_2019": {
            "weeks": int(len(late)),
            "realtime_contraction_weeks": int(late["realtime_official"].eq("contraction").sum()),
            "latest_vintage_contraction_weeks": int(
                late["latest_official"].eq("contraction").sum()
            ),
            "material_revision_sensitivity_failure": bool(
                late["latest_official"].eq("contraction").sum() > 0
                and late["realtime_official"].eq("contraction").sum() == 0
            ),
            "note": (
                "최신 수정치에만 있는 침체다. 실시간 운영 오탐은 아니지만, 개정 민감도 "
                "실패로 그대로 기록에 남는다."
            ),
        },
    }
