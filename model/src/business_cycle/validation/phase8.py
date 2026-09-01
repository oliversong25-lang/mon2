"""단계 A-5: 영역 폭 게이트를 바꾸기 전에 3영역 주를 전부 감사하고, 후보 H2를 만든다.

단계 B(ALFRED)는 후보 H를 실패로 판정했다. 2020년 공식 침체 8주 가운데 수축으로 부른
주가 하나도 없었기 때문이다. 여기서는 **게이트를 낮추기 전에** 왜 그랬는지부터 센다.

핵심 질문은 하나다. "음수 영역이 정확히 3개인 주"가 무엇이었나. 개발구간에서 그런 주는
어떤 모습이었고, 2020년 실시간에서는 어떤 모습이었나. 둘이 같은 것이라면 게이트를 낮추면
2019년 말 오탐이 돌아온다. 둘이 다른 것이라면 무엇이 다른지가 예외 규칙의 근거가 된다.

후보 H와 그 산출물은 건드리지 않는다. 실패한 검증 기록으로 보존해야 한다.
"""

# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings, load_baseline
from .phase2 import ModelEvaluation, _evaluate
from .phase4 import END, START, load_core_observations

#: 게이트를 건 후보와, 같은 설정에서 게이트만 뺀 대조군. 대조군이 "게이트 이전 국면"이다.
GATED = "candidate_h_breadth_gate"
UNGATED = "coordinate_g_scale_only_10y"
CORRECTED = "candidate_h2_systemic_override"

#: 임계값을 정할 때 쓰는 유일한 구간. 2013년 이후는 안정성 확인에만 쓴다.
DEVELOPMENT = ("1995-01-01", "2012-12-31")
STABILITY = ("2013-01-01", "2019-12-31")

#: 핵심 동행 영역. 주간 가교(실업수당)는 여기 들어가지 않는다.
CORE_DOMAINS = ("consumption", "employment", "income", "production")
BRIDGE_DOMAIN = "weekly_bridge"

#: 4주 연속 확인 규칙. 단계 A-4에서 별도 결함이 증명되지 않았으므로 바꾸지 않는다.
CONFIRMATION_WEEKS = 4

#: NBER 2020년 침체의 주간 기준. 단계 B와 같은 값을 쓴다.
NBER_2020 = (pd.Timestamp("2020-03-06"), pd.Timestamp("2020-04-24"))


@dataclass(frozen=True)
class Phase8Result:
    output_dir: Path
    latest_vintage_passed: bool
    alfred_passed: bool
    adopted: bool


def load_evaluations(
    settings: Settings,
) -> tuple[dict[str, ModelEvaluation], pd.DataFrame]:
    """최신 수정치 실행 세 개와 NBER 판정을 읽을 원본. 게이트 있음·없음·H2."""

    core, source = load_core_observations(settings)
    evaluations = {
        name: _evaluate(name, load_baseline(name, settings), core, source, START, END)
        for name in (GATED, UNGATED, CORRECTED)
    }
    return evaluations, source


def _domain_frame(evaluation: ModelEvaluation) -> pd.DataFrame:
    settings = evaluation.settings.indicators["indicators"]
    index = evaluation.history.index
    contributions = evaluation.backtest.run.contributions.reindex(index, method="ffill")
    domain_of = {str(key): str(value["domain"]) for key, value in settings.items()}
    return contributions.rename(columns=domain_of).T.groupby(level=0).sum().T


def _contraction_probability(frame: pd.DataFrame) -> pd.Series:
    columns = [column for column in frame.columns if str(column).startswith("p_contraction_")]
    return frame[columns].sum(axis=1)


def evidence_frame(
    gated: ModelEvaluation, ungated: ModelEvaluation, actual: pd.Series
) -> pd.DataFrame:
    """주별 증거 층을 한 표로 모은다. 3영역 감사와 임계값 도출이 같은 표를 쓴다."""

    history = gated.history
    index = history.index
    run = gated.backtest.run
    domains = _domain_frame(gated)
    core = domains[[column for column in CORE_DOMAINS if column in domains.columns]]
    magnitude = domains.abs()
    core_magnitude = core.abs()
    severity = run.breadth_audit.reindex(index)
    composite = run.composite.reindex(index, method="ffill")
    dynamic = run.dynamic.reindex(index, method="ffill")
    indicator_settings = gated.settings.indicators["indicators"]
    contributions = run.contributions.reindex(index, method="ffill")
    core_columns = [
        str(column)
        for column in contributions.columns
        if str(indicator_settings.get(str(column), {}).get("domain")) != BRIDGE_DOMAIN
    ]
    core_indicator_magnitude = contributions[core_columns].abs()

    frame = pd.DataFrame(
        {
            "usrec": actual.reindex(index).fillna(False).astype(int),
            "gated_broad_phase": history["broad_phase"],
            "gated_detail_phase": history["phase_code"],
            "ungated_broad_phase": ungated.history["broad_phase"].reindex(index),
            "ungated_detail_phase": ungated.history["phase_code"].reindex(index),
            "gated_contraction_probability": _contraction_probability(history),
            "ungated_contraction_probability": _contraction_probability(ungated.history).reindex(
                index
            ),
            "x": history["x"],
            "y": history["y"],
            "radius": history["radius"],
            "angle": history["angle"],
            "negative_domains": (domains < 0).sum(axis=1),
            "core_negative_domains": (core < 0).sum(axis=1),
            "negative_domain_names": domains.apply(
                lambda row: "|".join(sorted(str(k) for k, v in row.items() if v < 0)), axis=1
            ),
            "claims_contribution": domains[BRIDGE_DOMAIN],
            "claims_share": magnitude[BRIDGE_DOMAIN] / magnitude.sum(axis=1),
            "max_domain_share": magnitude.max(axis=1) / magnitude.sum(axis=1),
            "max_core_domain_share": core_magnitude.max(axis=1) / core_magnitude.sum(axis=1),
            "max_core_indicator_share": (
                core_indicator_magnitude.max(axis=1) / core_indicator_magnitude.sum(axis=1)
            ),
            "core_level": severity["core_level"],
            "leave_one_indicator_level": severity["leave_one_indicator_level"],
            "leave_one_domain_level": severity["leave_one_domain_level"],
            "composite": composite,
            "dynamic": dynamic,
            "composite_dynamic_agreement": np.exp(-(dynamic - composite).abs()),
        }
    )
    for column in domains.columns:
        frame[f"domain_{column}"] = domains[column]
    codes = history["phase_code"].astype(str)
    frame["persistence_four_weeks"] = [
        float((codes.iloc[max(0, position - 3) : position + 1] == codes.iloc[position]).mean())
        for position in range(len(codes))
    ]
    frame["next_four_weeks"] = [
        ">".join(codes.iloc[position + 1 : position + 5]) for position in range(len(codes))
    ]
    events = run.events.reindex(index)
    frame["released_this_week"] = events.apply(
        lambda row: "|".join(str(k) for k, v in row.items() if pd.notna(v)), axis=1
    )
    frame["missing_or_stale"] = contributions.apply(
        lambda row: "|".join(str(k) for k, v in row.items() if pd.isna(v) or float(v) == 0.0),
        axis=1,
    )
    frame["available_indicators"] = pd.Series(
        {timestamp: len(values) for timestamp, values in gated.effective_weights.items()}
    ).reindex(index)
    return frame


def three_domain_audit(evidence: pd.DataFrame) -> pd.DataFrame:
    """음수 영역이 정확히 3개인 모든 주를 구간 표시와 함께 남긴다."""

    selected = evidence[evidence["negative_domains"] == 3].copy()
    period = pd.Series("2020_and_after", index=selected.index, dtype=object)
    period.loc[: DEVELOPMENT[1]] = "development_1995_2012"
    period.loc[STABILITY[0] : STABILITY[1]] = "stability_2013_2019"
    selected.insert(0, "period", period)
    selected.insert(0, "week", [str(pd.Timestamp(str(i)).date()) for i in selected.index])
    selected.insert(1, "vintage_basis", "latest_revision")
    return selected


def severity_calibration(evidence: pd.DataFrame) -> pd.DataFrame:
    """임계값을 개발구간에서만 뽑는다. 어떤 통계를 어디서 읽었는지 함께 남긴다."""

    development = evidence.loc[DEVELOPMENT[0] : DEVELOPMENT[1]]
    expansion = development[development["usrec"] == 0]
    recession = development[development["usrec"] == 1]
    three = development[development["negative_domains"] == 3]
    bar = float(expansion["core_level"].min())
    severe = development[development["core_level"] <= bar]
    return pd.DataFrame(
        [
            {
                "constant": "core_level",
                "value": round(bar, 4),
                "source": "개발구간 비침체 주의 최저 핵심수준",
                "measured_at": str(pd.Timestamp(str(expansion["core_level"].idxmin())).date()),
                "development_weeks_behind_it": int(len(severe)),
                "note": (
                    f"3영역 주 {len(three)}개의 핵심수준은 "
                    f"{three['core_level'].min():.3f}~{three['core_level'].max():.3f}로 "
                    "이 값 근처에도 오지 않는다"
                ),
            },
            {
                "constant": "leave_one_indicator_level",
                "value": round(float(severe["leave_one_indicator_level"].max()), 4),
                "source": "심각도 기준을 넘은 개발구간 주 가운데 가장 약한 지표 제거 심각도",
                "measured_at": str(
                    pd.Timestamp(str(severe["leave_one_indicator_level"].idxmax())).date()
                ),
                "development_weeks_behind_it": int(len(severe)),
                "note": "최대 기여 지표 하나를 빼도 남는 값. 한 지표가 만든 신호를 걸러낸다",
            },
            {
                "constant": "leave_one_domain_level",
                "value": round(float(severe["leave_one_domain_level"].max()), 4),
                "source": "같은 구간에서 가장 약한 영역 제거 심각도",
                "measured_at": str(
                    pd.Timestamp(str(severe["leave_one_domain_level"].idxmax())).date()
                ),
                "development_weeks_behind_it": int(len(severe)),
                "note": "최대 기여 영역 하나를 빼도 남는 값",
            },
            {
                "constant": "minimum_ungated_contraction_probability",
                "value": 0.90,
                "source": "개발구간 3영역 주의 게이트 이전 침체확률 최댓값 대비",
                "measured_at": str(
                    pd.Timestamp(str(three["ungated_contraction_probability"].idxmax())).date()
                ),
                "development_weeks_behind_it": int(len(three)),
                "note": (
                    f"3영역 주 최댓값 {three['ungated_contraction_probability'].max():.3f}, "
                    f"침체 주 중앙값 {recession['ungated_contraction_probability'].median():.3f}"
                ),
            },
            {
                "constant": "minimum_core_negative_domains",
                "value": 2,
                "source": "개발구간 3영역 주의 핵심 음수영역 최솟값",
                "measured_at": DEVELOPMENT[0],
                "development_weeks_behind_it": int(len(three)),
                "note": (
                    f"관측 범위 {int(three['core_negative_domains'].min())}~"
                    f"{int(three['core_negative_domains'].max())}. 하한이며 맞춘 값이 아니다"
                ),
            },
        ]
    )


def concentration_reference(evidence: pd.DataFrame) -> pd.DataFrame:
    """최대 점유율 통계를 예외 조건으로 쓸 수 없는 이유를 수치로 남긴다.

    "한 지표가 지배하지 않을 것"을 최대 점유율로 재면, 신호를 가진 핵심지표가 둘뿐일 때
    최대 점유율의 하한이 0.5다. 예외가 필요한 상황이 정확히 그 상황이므로 이 통계는
    구조적으로 충족 불가능해진다. 그래서 leave-one-out으로 바꿨다.
    """

    development = evidence.loc[DEVELOPMENT[0] : DEVELOPMENT[1]]
    recession = development[development["usrec"] == 1]
    return pd.DataFrame(
        [
            {
                "statistic": column,
                "development_recession_max": round(float(recession[column].max()), 4),
                "development_recession_p95": round(float(recession[column].quantile(0.95)), 4),
                "development_recession_median": round(float(recession[column].median()), 4),
                "recession_weeks_above_one_half": int((recession[column] > 0.5).sum()),
                "recession_weeks": int(len(recession)),
            }
            for column in ("max_core_domain_share", "max_core_indicator_share")
        ]
    )


_EPISODE_COLUMNS = (
    "min_y",
    "max_radius",
    "min_core_level",
    "min_leave_one_indicator_level",
    "min_leave_one_domain_level",
    "max_claims_share",
    "max_domain_share",
)


def _episode_row(name: str, basis: str, frame: pd.DataFrame, gated: str) -> dict[str, Any]:
    if frame.empty:
        return {"episode": name, "basis": basis, "weeks": 0}
    return {
        "episode": name,
        "basis": basis,
        "weeks": int(len(frame)),
        "first_week": str(pd.Timestamp(str(frame.index[0])).date()),
        "last_week": str(pd.Timestamp(str(frame.index[-1])).date()),
        "usrec_weeks": int(frame["usrec"].sum()) if "usrec" in frame.columns else -1,
        "min_y": round(float(frame["y"].min()), 3),
        "max_radius": round(float(frame["radius"].max()), 3),
        "min_core_level": round(float(frame["core_level"].min()), 3),
        "min_leave_one_indicator_level": round(float(frame["leave_one_indicator_level"].min()), 3),
        "min_leave_one_domain_level": round(float(frame["leave_one_domain_level"].min()), 3),
        "max_claims_share": round(float(frame["claims_share"].max()), 3),
        "max_domain_share": round(float(frame["max_domain_share"].max()), 3),
        "min_negative_domains": int(frame["negative_domains"].min()),
        "max_negative_domains": int(frame["negative_domains"].max()),
        "max_ungated_contraction_probability": round(
            float(frame["ungated_contraction_probability"].max()), 3
        ),
        "max_gated_contraction_probability": round(float(frame[gated].max()), 3),
    }


def episode_comparison(evidence: pd.DataFrame, realtime: pd.DataFrame | None) -> pd.DataFrame:
    """지시된 사례를 같은 항목으로 나란히 놓는다."""

    column = "gated_contraction_probability"
    rows = [
        _episode_row(
            "2001 침체", "latest_revision", evidence.loc["2001-03-01":"2001-11-30"], column
        ),
        _episode_row(
            "금융위기", "latest_revision", evidence.loc["2007-12-01":"2009-06-30"], column
        ),
        _episode_row(
            "2000년 말 3영역 최고조",
            "latest_revision",
            evidence.loc["2000-11-24":"2001-02-09"],
            column,
        ),
        _episode_row(
            "2019년 말 오탐 구간",
            "latest_revision",
            evidence.loc["2019-10-01":"2020-01-31"],
            column,
        ),
        _episode_row(
            "2020년 침체", "latest_revision", evidence.loc[NBER_2020[0] : NBER_2020[1]], column
        ),
    ]
    if realtime is not None and not realtime.empty:
        indexed = realtime.set_index(pd.DatetimeIndex(realtime["as_of"]))
        for name, start, end in (
            ("2019년 말 오탐 구간", "2019-10-01", "2020-01-31"),
            ("2020년 침체", str(NBER_2020[0].date()), str(NBER_2020[1].date())),
            ("2020년 3~4월", "2020-03-01", "2020-04-30"),
        ):
            window = indexed.loc[start:end]
            if not window.empty:
                rows.append(_episode_row(name, "strict_alfred", window, "contraction_probability"))
    return pd.DataFrame(rows)
