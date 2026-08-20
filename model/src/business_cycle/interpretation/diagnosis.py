"""한 나라·한 시점의 경기 진단을 조립한다.

이 모듈은 국면을 고르지 않는다. `run_pipeline`이 이미 고른 공식 국면을 받아, 경계·
전환·확신도·영역·산업 진단을 붙여 사람이 읽을 형태로 만든다. 어떤 필드도 모델로
되돌아가지 않는다.

출력에는 투자 판단이 없다. 종목·섹터·비중·목표가·매매 문구를 만들지 않으며,
`contract.validate_output`이 그것을 강제한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..pipeline import PipelineRun
from . import contract
from .boundary import boundary_view
from .confidence import confidence_view
from .countries import registration
from .domains import (
    domain_breadth,
    domain_changes,
    domain_readings,
    explain,
)
from .industry import availability_audit, industry_view
from .transition import transition_view

#: 사용자에게 항상 함께 나가야 하는 한계. 검증에서 확인된 사실만 적는다.
STANDING_LIMITATIONS: tuple[str, ...] = (
    "이 모델은 갑작스러운 외생 침체를 광범위한 동행지표가 발표된 뒤에야 인식할 수 있다. "
    "엄격 ALFRED 검증에서 2020년 공식 침체 8주 중 수축으로 부른 주가 0주였다.",
    "실시간 검증이 가능한 침체가 2020년 하나뿐이라 실시간 탐지 성능을 일반화할 수 없다.",
    "확신도는 보정된 확률이 아니다. 보정을 시연한 적이 없다.",
    "이 결과는 경기국면 진단이며 투자 판단이 아니다. 종목·섹터·비중 판단은 사용자 몫이다.",
)


def _agreement(composite: pd.Series, dynamic: pd.Series, as_of: pd.Timestamp) -> float:
    left = float(dynamic.reindex([as_of], method="ffill").iloc[0])
    right = float(composite.reindex([as_of], method="ffill").iloc[0])
    if not (np.isfinite(left) and np.isfinite(right)):
        return float("nan")
    return float(np.exp(-abs(left - right)))


def _stale_domains(events: pd.DataFrame, settings: Settings, as_of: pd.Timestamp) -> list[str]:
    """설정된 `max_age_weeks`를 넘긴 영역. 판단 기준을 새로 만들지 않는다."""

    indicator_settings = settings.indicators["indicators"]
    stale: set[str] = set()
    window = events.loc[:as_of]
    for indicator, config in indicator_settings.items():
        if indicator not in window.columns:
            stale.add(contract.label_domain(str(config["domain"])))
            continue
        observed = window[indicator].dropna()
        if observed.empty:
            stale.add(contract.label_domain(str(config["domain"])))
            continue
        age = float((as_of - pd.Timestamp(str(observed.index[-1]))).days) / 7.0
        if age > float(config["max_age_weeks"]):
            stale.add(contract.label_domain(str(config["domain"])))
    return sorted(stale)


def diagnose(
    run: PipelineRun,
    settings: Settings,
    country: str = "US",
    as_of: pd.Timestamp | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """공식 국면 하나와 그 국면에 대한 진단을 낸다."""

    entry = registration(country)
    if entry.status != "implemented":
        raise ValueError(f"{country}에는 인과 모델이 없습니다. 진단을 만들지 않습니다")

    history = run.history
    timestamp = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp(str(history.index[-1]))
    row = history.loc[timestamp]

    # 공식 국면은 모델이 고른 것을 그대로 쓴다. 여기서 다시 고르지 않는다.
    official_code = str(row["phase_code"])
    official_broad = str(row["broad_phase"])
    official_detailed = contract.label_detailed_phase(official_code)

    boundary = boundary_view(history, timestamp)
    momentum_weeks = int(settings.model["momentum_weeks"])
    readings = domain_readings(
        run.contributions,
        run.events,
        settings,
        history,
        timestamp,
        official_broad,
        momentum_weeks,
    )
    transition = transition_view(history, timestamp, official_code, domain_changes(readings))

    status = str(run.result.status)
    stale = _stale_domains(run.events, settings, timestamp)
    confidence = confidence_view(
        boundary,
        readings,
        status,
        _agreement(run.composite, run.dynamic, timestamp),
        stale,
    )

    audit = availability_audit(
        cache_dir if cache_dir is not None else settings.root / "data" / "cache",
        list(settings.indicators["indicators"]),
    )
    industry = industry_view(audit)

    recession = (
        "withheld" if status == "withheld" else ("yes" if official_broad == "contraction" else "no")
    )
    payload: dict[str, Any] = {
        "country": entry.country,
        "as_of_date": str(timestamp.date()),
        "official_broad_phase": official_broad,
        "official_detailed_phase": official_detailed,
        "recession_status": recession,
        "official_phase_probability": round(float(str(row[f"p_{official_code}"])), 6),
        "runner_up_phase": contract.label_detailed_phase(boundary.runner_up_phase),
        "runner_up_probability": round(boundary.runner_up_probability, 6),
        "winner_runner_up_gap": round(boundary.gap, 6),
        "confidence_level": confidence.confidence_level,
        "boundary_flag": boundary.boundary_flag,
        "boundary_reason": boundary.boundary_reason,
        "transition_watch": transition.transition_watch,
        "transition_direction": contract.label_detailed_phase(transition.transition_direction),
        "data_status": status,
        "supporting_domains": [
            reading.domain for reading in readings if reading.supports_official_phase
        ],
        "opposing_domains": [
            reading.domain for reading in readings if reading.opposes_official_phase
        ],
        "domain_breadth": domain_breadth(readings),
        "industry_breadth_status": industry.industry_breadth_status,
        "industry_concentration_status": industry.industry_concentration_status,
        "short_explanation": explain(readings, official_broad),
        "limitations": list(STANDING_LIMITATIONS),
        # ── 부가 진단. 필수 필드가 아니라 근거를 되짚기 위한 것이다. ──
        "model_detailed_phase_code": official_code,
        "phase_entropy": round(boundary.entropy, 6),
        "confidence_reasons": confidence.confidence_reasons,
        "confidence_is_calibrated": confidence.calibrated,
        "transition_detail": {
            "transition_probability": round(transition.transition_probability, 6),
            "probability_change_1w": (
                round(transition.probability_change_1w, 6)
                if np.isfinite(transition.probability_change_1w)
                else None
            ),
            "probability_change_4w": (
                round(transition.probability_change_4w, 6)
                if np.isfinite(transition.probability_change_4w)
                else None
            ),
            "rising_weeks_of_four": transition.rising_weeks_of_four,
            "trigger": transition.trigger,
            "supporting_domain_changes": {
                key: round(value, 6) for key, value in transition.supporting_domain_changes.items()
            },
        },
        "domain_detail": [
            {
                "domain": reading.domain,
                "direction": reading.direction,
                "standardized_contribution": reading.standardized_contribution,
                "contribution_share": reading.contribution_share,
                "recent_change": reading.recent_change,
                "stance": reading.stance,
                "data_freshness_weeks": reading.data_freshness_weeks,
                "missing": reading.missing,
            }
            for reading in readings
        ],
        "industry_basis": industry.basis,
        "status_reason": str(run.result.metadata.get("status_reason", "")),
        "model_baseline": entry.model_baseline,
        "coordinates": {
            "x_momentum": round(float(str(row["x"])), 6),
            "y_level": round(float(str(row["y"])), 6),
            "radius": round(float(str(row["radius"])), 6),
        },
    }
    contract.validate_output(payload)
    return payload


def render_markdown(payload: dict[str, Any]) -> str:
    """§10의 순서대로 읽히는 사람용 출력."""

    country_names = {"US": "United States", "KR": "대한민국", "CN": "중국"}
    lines = [
        f"{country_names.get(payload['country'], payload['country'])} — {payload['as_of_date']}",
        "",
        f"공식 국면: {payload['official_detailed_phase']}",
        f"침체 여부: {payload['recession_status']}",
        f"확신도: {payload['confidence_level']}",
    ]
    if payload["confidence_reasons"]:
        for reason in payload["confidence_reasons"]:
            lines.append(f"  - {reason}")
    lines += [
        "",
        f"2순위: {payload['runner_up_phase']} ({payload['runner_up_probability']:.3f})",
        f"경계 상태: {'경계에 있음' if payload['boundary_flag'] else '경계 아님'}"
        f" (1·2순위 차이 {payload['winner_runner_up_gap']:.3f})",
        f"전환 감시: {'있음' if payload['transition_watch'] else '없음'}"
        f" — 방향 {payload['transition_direction']},"
        f" 4주 변화 {payload['transition_detail']['probability_change_4w']}",
        "",
        "뒷받침하는 증거:",
    ]
    detail = {item["domain"]: item for item in payload["domain_detail"]}
    for domain in payload["supporting_domains"] or ["(없음)"]:
        item = detail.get(domain)
        lines.append(
            f"  - {domain}: 기여 {item['standardized_contribution']:+.3f},"
            f" 8주 변화 {item['recent_change']:+.3f}"
            if item
            else f"  - {domain}"
        )
    lines.append("")
    lines.append("반대하는 증거:")
    for domain in payload["opposing_domains"] or ["(없음)"]:
        item = detail.get(domain)
        lines.append(
            f"  - {domain}: 기여 {item['standardized_contribution']:+.3f},"
            f" 8주 변화 {item['recent_change']:+.3f}"
            if item
            else f"  - {domain}"
        )
    breadth = payload["domain_breadth"]
    lines += [
        "",
        f"경제영역 폭: 찬성 {breadth['supporting']} · 반대 {breadth['opposing']}"
        f" · 혼재 {breadth['mixed']} (총 {breadth['total_domains']})",
        f"산업 폭: {payload['industry_breadth_status']}"
        f" · 집중도: {payload['industry_concentration_status']}",
        f"  {payload['industry_basis']}",
        "",
        "한계:",
    ]
    lines += [f"  - {item}" for item in payload["limitations"]]
    return "\n".join(lines) + "\n"
