"""단계 A-5: 청구건수 제외 심각도, 체계적 충격 예외, 게이트 판정."""

from __future__ import annotations

import pandas as pd
import pytest

from business_cycle.config import load_baseline, load_settings
from business_cycle.models.severity import severity_details, systemic_override
from business_cycle.validation.phase8 import CORE_DOMAINS, DEVELOPMENT
from business_cycle.validation.phase8_report import (
    alfred_gate,
    latest_vintage_gate,
    withheld_audit,
)

DOMAIN_OF = {
    "PAYEMS": "employment",
    "W875RX1": "income",
    "INDPRO": "production",
    "CMRMTSPL": "consumption",
    "RRSFS": "consumption",
    "ICSA": "weekly_bridge",
    "CCSA": "weekly_bridge",
}


def _week(values: dict[str, float], weights: dict[str, float], scale: float = 0.5) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp("2020-04-17")])
    contributions = pd.DataFrame([values], index=index)
    return severity_details(
        contributions,
        {index[0]: weights},
        DOMAIN_OF,
        pd.Series([scale], index=index),
        index,
    )


# ── 청구건수 제외 심각도 ─────────────────────────────────────────────────────


def test_core_level_excludes_claims_and_renormalises_weights() -> None:
    """실업수당이 아무리 크게 무너져도 핵심 심각도에는 들어가지 않는다.

    2020년 3월 실시간이 정확히 이 모양이었다. 청구건수만 붕괴하고 핵심 동행지표는
    아직 아무 말도 하지 않았다. 그 주를 '사상 최대 충격'으로 읽으면 안 된다.
    """

    frame = _week(
        {"INDPRO": -0.04, "PAYEMS": 0.09, "W875RX1": 0.04, "CMRMTSPL": -0.05, "ICSA": -4.86},
        {"INDPRO": 0.2, "PAYEMS": 0.18, "W875RX1": 0.18, "CMRMTSPL": 0.09, "ICSA": 0.15},
    )
    # 핵심 기여 합 0.04, 핵심 가중치 합 0.65, 척도 0.5 → 0.04/0.65/0.5
    assert frame["core_level"].iloc[0] == pytest.approx(0.04 / 0.65 / 0.5, rel=1e-6)
    assert frame["core_negative_domains"].iloc[0] == 2
    assert frame["core_indicator_count"].iloc[0] == 4


def test_core_level_is_unchanged_by_the_size_of_the_claims_collapse() -> None:
    """청구건수 값을 100배로 키워도 핵심 심각도는 그대로여야 한다."""

    weights = {"INDPRO": 0.2, "PAYEMS": 0.18, "CMRMTSPL": 0.09, "ICSA": 0.15}
    small = _week({"INDPRO": -0.9, "PAYEMS": 0.01, "CMRMTSPL": -0.8, "ICSA": -0.05}, weights)
    large = _week({"INDPRO": -0.9, "PAYEMS": 0.01, "CMRMTSPL": -0.8, "ICSA": -5.0}, weights)
    assert small["core_level"].iloc[0] == pytest.approx(large["core_level"].iloc[0])


def test_leave_one_out_removes_the_largest_contributor_not_the_first() -> None:
    """가장 큰 기여를 뺀다. 열 순서나 이름 순서가 아니다."""

    frame = _week(
        {"INDPRO": -0.7, "RRSFS": -0.9, "PAYEMS": 0.01, "ICSA": -2.0},
        {"INDPRO": 0.2, "RRSFS": 0.1, "PAYEMS": 0.18, "ICSA": 0.15},
    )
    remaining = (-0.7 + 0.01) / (0.48 - 0.1) / 0.5
    assert frame["leave_one_indicator_level"].iloc[0] == pytest.approx(remaining, rel=1e-6)
    # 소비 영역은 RRSFS 하나뿐이라 지표 제거와 영역 제거가 같은 값을 준다.
    assert frame["leave_one_domain_level"].iloc[0] == pytest.approx(remaining, rel=1e-6)


def test_missing_scale_yields_no_severity_instead_of_a_wrong_number() -> None:
    """척도가 없으면 값을 지어내지 않는다."""

    frame = _week({"INDPRO": -0.7, "ICSA": -2.0}, {"INDPRO": 0.2, "ICSA": 0.15}, scale=float("nan"))
    assert pd.isna(frame["core_level"].iloc[0])


# ── 예외 규칙 ────────────────────────────────────────────────────────────────


BASE_CONFIG = {
    "enabled": True,
    "minimum_core_negative_domains": 2,
    "core_level": -2.93,
    "leave_one_indicator_level": -2.82,
    "leave_one_domain_level": -2.33,
    "minimum_ungated_contraction_probability": 0.90,
    "require_dynamic_agreement": True,
}


def _passing_inputs() -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    index = pd.DatetimeIndex(pd.date_range("2009-01-02", periods=1, freq="W-FRI"))
    severity = pd.DataFrame(
        {
            "core_level": [-3.4],
            "core_negative_domains": [3],
            "leave_one_indicator_level": [-3.1],
            "leave_one_domain_level": [-3.0],
        },
        index=index,
    )
    return (
        severity,
        pd.Series([3], index=index),
        pd.Series([0.97], index=index),
        pd.Series([-3.2], index=index),
    )


def test_override_fires_when_every_condition_holds() -> None:
    severity, breadth, ungated, dynamic = _passing_inputs()
    assert bool(systemic_override(severity, breadth, ungated, dynamic, 4, BASE_CONFIG).iloc[0])


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("core_level", -2.5),
        ("core_negative_domains", 1),
        ("leave_one_indicator_level", -2.0),
        ("leave_one_domain_level", -1.0),
    ],
)
def test_every_severity_condition_can_block_the_override(column: str, value: float) -> None:
    """조건은 AND다. 하나라도 무너지면 예외는 발동하지 않는다."""

    severity, breadth, ungated, dynamic = _passing_inputs()
    severity[column] = value
    assert not bool(systemic_override(severity, breadth, ungated, dynamic, 4, BASE_CONFIG).iloc[0])


def test_weak_ungated_probability_blocks_the_override() -> None:
    severity, breadth, ungated, dynamic = _passing_inputs()
    assert not bool(
        systemic_override(
            severity, breadth, pd.Series([0.4], index=ungated.index), dynamic, 4, BASE_CONFIG
        ).iloc[0]
    )


def test_disagreeing_dynamic_model_blocks_the_override() -> None:
    """동적요인이 반대 방향이면 한 모형의 주장일 뿐이다."""

    severity, breadth, ungated, dynamic = _passing_inputs()
    assert not bool(
        systemic_override(
            severity, breadth, ungated, pd.Series([0.5], index=dynamic.index), 4, BASE_CONFIG
        ).iloc[0]
    )


def test_override_does_not_apply_when_breadth_already_satisfies_the_gate() -> None:
    """폭이 이미 충분한 주는 예외의 대상이 아니다. 정상 경로로 통과한다."""

    severity, _, ungated, dynamic = _passing_inputs()
    breadth = pd.Series([4], index=severity.index)
    assert not bool(systemic_override(severity, breadth, ungated, dynamic, 4, BASE_CONFIG).iloc[0])


def test_override_does_not_apply_two_domains_below_the_minimum() -> None:
    """한 단계만 봐준다. 두 단계 아래는 넓이라고 부를 수 없다."""

    severity, _, ungated, dynamic = _passing_inputs()
    breadth = pd.Series([2], index=severity.index)
    assert not bool(systemic_override(severity, breadth, ungated, dynamic, 4, BASE_CONFIG).iloc[0])


# ── 설정 ─────────────────────────────────────────────────────────────────────


def test_candidate_h_has_no_override_and_stays_untouched() -> None:
    """후보 H는 실패한 검증 기록이다. 예외 규칙이 섞여 들어가면 안 된다."""

    settings = load_settings()
    frozen = load_baseline("candidate_h_breadth_gate", settings)
    assert "systemic_shock_override" not in frozen.model
    assert frozen.model["contraction_breadth_gate"] == {"enabled": True, "minimum_domains": 4.0}


def test_candidate_h2_keeps_the_four_domain_rule() -> None:
    """예외를 더했을 뿐, 기본 규칙은 여전히 영역 4개다."""

    settings = load_settings()
    corrected = load_baseline("candidate_h2_systemic_override", settings)
    assert corrected.model["contraction_breadth_gate"] == {"enabled": True, "minimum_domains": 4.0}
    override = corrected.model["systemic_shock_override"]
    assert override["enabled"] is True
    # 상수는 모두 개발구간에서 나온 값을 더 엄격한 쪽으로 반올림한 것이다.
    assert override["core_level"] <= -2.9254
    assert override["leave_one_indicator_level"] <= -2.8174
    assert override["leave_one_domain_level"] <= -2.3273


def test_override_without_a_breadth_gate_is_rejected() -> None:
    """게이트 없이 예외만 두면 무엇을 봐주는지 정의되지 않는다."""

    import yaml

    from business_cycle.config import _baseline_model

    document = yaml.safe_load(
        (load_settings().root / "configs" / "baselines.yaml").read_text(encoding="utf-8")
    )["candidate_h2_systemic_override"]
    document.pop("contraction_breadth_gate")
    with pytest.raises(ValueError, match="폭 게이트"):
        _baseline_model("broken", document, load_settings().model)


# ── 게이트 판정 ──────────────────────────────────────────────────────────────


def _history(codes: list[str]) -> pd.DataFrame:
    index = pd.date_range("2019-06-07", periods=len(codes), freq="W-FRI")
    frame = pd.DataFrame(
        {"phase_code": codes, "broad_phase": [code.split("_")[0] for code in codes]}, index=index
    )
    for code in {
        "contraction_early",
        "contraction_mid",
        "contraction_late",
        "slowdown_late",
    }:
        frame[f"p_{code}"] = [1.0 if value == code else 0.0 for value in codes]
    return frame


def test_latest_vintage_gate_fails_when_recall_drops() -> None:
    history = _history(["slowdown_late"] * 10)
    metrics = {
        "recession_recall": 0.70,
        "recession_false_positive_rate": 0.01,
        "recession_f1": 0.80,
    }
    reference = {"f1": 0.80, "warmup_2001_range_weeks": 4.0}
    gate = latest_vintage_gate(pd.DataFrame(), history, history, metrics, reference, pd.DataFrame())
    assert not bool(gate["passed"].all())
    assert not bool(gate.loc[gate["criterion"].str.startswith("재현율"), "passed"].iloc[0])


def test_alfred_gate_fails_when_no_official_recession_week_is_called() -> None:
    """전체 재현율 뒤에 숨기지 않는다. 공식 구간 안의 주 수를 직접 본다."""

    detection = {
        "weeks_with_observations_after_as_of": 0,
        "first_signal_lag_weeks": 8.0,
        "confirmation_lag_weeks": 9.0,
        "official_recession_weeks_called_contraction": 0,
        "confirmation_within_official_recession": False,
        "pre_recession_confirmed_weeks": 0,
        "late_2019_confirmed_contraction_weeks": 0,
    }
    gate = alfred_gate(detection)
    assert not bool(gate["passed"].all())
    failing = gate[~gate["passed"]]["criterion"].tolist()
    assert failing == ["공식 침체 주 중 수축 판정 >= 1"]


def test_alfred_gate_flags_a_late_first_signal() -> None:
    detection = {
        "weeks_with_observations_after_as_of": 0,
        "first_signal_lag_weeks": 12.0,
        "confirmation_lag_weeks": 15.0,
        "official_recession_weeks_called_contraction": 1,
        "confirmation_within_official_recession": False,
        "pre_recession_confirmed_weeks": 0,
        "late_2019_confirmed_contraction_weeks": 0,
    }
    gate = alfred_gate(detection)
    assert not bool(gate["passed"].all())


# ── 보류 감사 ────────────────────────────────────────────────────────────────


def test_withheld_audit_marks_stale_indicators_by_age_not_by_absence() -> None:
    """보류 원인은 지표가 없어서가 아니라 오래돼서일 수 있다. 둘을 구분한다."""

    settings = load_settings()
    realtime = pd.DataFrame(
        [
            {
                "as_of": "2025-11-14",
                "status": "withheld",
                "newest_PAYEMS": "2025-08-01",
                "newest_W875RX1": "2025-08-01",
                "newest_INDPRO": "2025-08-01",
                "newest_CMRMTSPL": "2025-07-01",
                "newest_RRSFS": "2025-08-01",
                "newest_ICSA": "2025-09-20",
                "newest_CCSA": "2025-09-13",
            }
        ]
    )
    audit = withheld_audit(realtime, settings)
    assert len(audit) == 7
    assert audit["newest_observation"].ne("").all()
    stale = audit[~audit["counted_available"]]["indicator"].tolist()
    assert "PAYEMS" in stale
    assert audit.loc[audit["indicator"] == "PAYEMS", "age_weeks"].iloc[0] > 8.0


def test_development_window_is_the_only_calibration_window() -> None:
    """임계값은 1995~2012에서만 온다. 상수로 박아두지 않고 검사한다."""

    assert DEVELOPMENT == ("1995-01-01", "2012-12-31")
    assert "weekly_bridge" not in CORE_DOMAINS
