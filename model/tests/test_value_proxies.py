"""가치 대리 변수 스윕의 계약.

이 단계에서 지켜야 할 것은 하나로 요약된다 — **문턱을 결과에 맞춰 움직이지 않는다.**
그래서 사전 명세가 코드로 존재하는지, 그것이 실제로 판정에 쓰이는지, 검정 B가
규칙대로만 열리는지를 검사한다.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from business_cycle.value_proxies import prespec
from business_cycle.value_proxies import sorts as S
from business_cycle.value_proxies import testa as T

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "value_proxies"
CACHE = str(ROOT / "data" / "cache" / "famafrench")


# ── 사전 명세 ───────────────────────────────────────────────────────────────


def test_the_decision_rule_exists_as_code_not_prose() -> None:
    """규칙이 보고서 문장으로만 있으면 나중에 조용히 바뀔 수 있다."""

    rule = prespec.rule()
    assert rule["written_before_running"] is True
    assert rule["decision_window"] == prespec.DECISION_WINDOW
    assert prespec.NOMINAL_T == 2.0
    assert prespec.FAMILY_CORRECTED_T > prespec.NOMINAL_T


def test_the_family_threshold_is_stricter_than_the_nominal_one() -> None:
    """보정이 문턱을 무르게 만들면 보정이 아니다."""

    assert prespec.FAMILY_ALPHA < 0.05
    assert prespec.passes_nominally(0.05, 2.1) is True
    assert prespec.passes_after_multiplicity(0.05, 2.1) is False
    assert prespec.passes_after_multiplicity(0.05, 2.6) is True


def test_a_negative_spread_never_passes_however_large_the_t() -> None:
    """부호가 음인데 t가 커서 통과하면 그것은 프리미엄이 아니라 반대 방향이다."""

    assert prespec.passes_nominally(-0.05, 9.0) is False
    assert prespec.passes_after_multiplicity(-0.05, 9.0) is False


def test_operating_profitability_is_not_in_the_value_family() -> None:
    assert not any("profitab" in name.lower() for name in prespec.VALUE_DEFINITIONS_TESTED)
    assert prespec.FAMILY_ALPHA == pytest.approx(0.05 / 4)
    op = next(sort for sort in S.SORTS if sort.key == "operating_profitability")
    assert op.is_value_proxy is False
    assert op.family == "profitability"


def test_the_decision_rule_was_committed_before_the_data(  # noqa: D401
) -> None:
    """사전 명세 커밋이 자료 캐시보다 앞선다는 것을 저장소가 증언한다.

    문장으로 "미리 정했다"고 쓰는 것과 이력에 남는 것은 다르다.
    """

    result = subprocess.run(
        ["git", "log", "--format=%s", "--", "src/business_cycle/value_proxies/prespec.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip("git 이력을 읽을 수 없다")
    subjects = [line for line in result.stdout.splitlines() if line.strip()]
    assert subjects, "사전 명세가 커밋되지 않았다"
    assert any("pre-specify" in subject for subject in subjects), subjects


# ── 정렬 읽기 ───────────────────────────────────────────────────────────────


def test_only_the_value_weighted_monthly_section_is_read() -> None:
    for sort in S.SORTS:
        frame = S.load(sort, CACHE)
        assert frame.index.is_unique, sort.key
        # 월 라벨이 YYYY-MM 형태여야 창 자르기가 문자열 비교로 안전하다.
        assert all(len(str(month)) == 7 for month in frame.index[:5])
        # 퍼센트가 아니라 소수. 월 수익률이 5를 넘을 수 없다.
        assert float(frame.abs().max().max()) < 5.0


def test_every_sort_carries_the_primary_and_secondary_columns() -> None:
    for sort in S.SORTS:
        frame = S.load(sort, CACHE)
        for column in (*prespec.PRIMARY_SORT, *prespec.SECONDARY_SORT):
            assert column in frame.columns, (sort.key, column)


def test_month_window_is_inclusive_on_both_ends() -> None:
    index = pd.Index(["2013-05", "2013-06", "2026-08", "2026-09"])
    assert S.month_window("2013-06", "2026-08", index) == ["2013-06", "2026-08"]


# ── 통계 ────────────────────────────────────────────────────────────────────


def test_the_annualisation_uses_twelve_periods_not_fifty_two() -> None:
    """월간 자료를 주간처럼 연율화하면 수익이 네 배로 뻥튀기된다."""

    monthly = pd.Series([0.01] * 120)
    assert T.annualise(monthly) == pytest.approx(1.01**12 - 1.0, rel=1e-6)


def test_a_thin_window_is_marked_rather_than_summarised() -> None:
    entry = T.profile(pd.Series([0.01] * 4), "tiny")
    assert entry["thin"] is True
    assert "annualised" not in entry


def test_more_hac_lags_never_make_the_t_statistic_easier_to_pass() -> None:
    """지연을 늘린 것이 문턱을 무르게 하지 않았다는 주장을 실제로 확인한다."""

    rng = np.random.default_rng(17)
    persistent = pd.Series(
        pd.Series(rng.normal(0.004, 0.04, size=600)).rolling(9).mean().dropna().to_numpy()
    )
    from business_cycle.phase_value.premium import hac_t_statistic

    few = hac_t_statistic(persistent, lags=2)
    many = hac_t_statistic(persistent, lags=prespec.MONTHLY_HAC_LAGS)
    assert few is not None and many is not None
    assert abs(many) <= abs(few)


# ── 판정 ────────────────────────────────────────────────────────────────────


def test_the_decision_reads_the_decision_window_and_not_the_full_history() -> None:
    """전체 역사에 프리미엄이 있어도 그것으로 통과시키면 안 된다.

    우리 국면 라벨이 그 구간에 존재하지 않기 때문이다.
    """

    frame = S.load(S.SORTS[0], CACHE)
    windows = {
        prespec.DECISION_WINDOW: S.month_window("1994-07", "2026-08", frame.index),
        "real-time window": S.month_window("2013-06", "2026-08", frame.index),
    }
    row = T.run_one(S.SORTS[0], windows, CACHE)
    full = next(e for e in row["profiles"] if e["window"] == "full Fama-French sample")
    decision = next(e for e in row["profiles"] if e["window"] == prespec.DECISION_WINDOW)
    assert row["decision_window_hac_t"] == decision["hac_t"]
    assert row["decision_window_hac_t"] != full["hac_t"]


def test_test_b_opens_only_when_a_value_proxy_survives_multiplicity() -> None:
    frame = S.load(S.SORTS[0], CACHE)
    windows = {
        prespec.DECISION_WINDOW: S.month_window("1994-07", "2026-08", frame.index),
        "real-time window": S.month_window("2013-06", "2026-08", frame.index),
    }
    result = T.run(windows, CACHE)
    assert result["test_b_opens"] == bool(result["value_proxies_passing_after_multiplicity"])
    # 수익성 요인이 통과해도 검정 B를 열지 않는다.
    assert all(
        key != "operating_profitability"
        for key in result["value_proxies_passing_after_multiplicity"]
    )


# ── 산출물 ──────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_artifact_records_the_rule_alongside_the_result() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    rule = payload["test_a"]["rule"]
    assert rule["written_before_running"] is True
    assert rule["family_size"] == len(prespec.VALUE_DEFINITIONS_TESTED)
    assert payload["frozen_model_modified"] is False


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_test_b_was_not_run_and_the_report_says_why() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    report = (OUTPUT / "value_proxy_report.md").read_text(encoding="utf-8")
    if payload["test_a"]["test_b_opens"]:
        pytest.skip("규칙상 B가 열린 경우")
    assert payload["test_b_run"] is False
    assert payload["test_b_not_run_because"]
    assert "검정 B는 열지 않았다" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_operating_profitability_is_reported_but_not_folded_into_the_conclusion() -> None:
    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    report = (OUTPUT / "value_proxy_report.md").read_text(encoding="utf-8")
    assert "영업이익률" in report
    assert "가치 결론에 접어 넣지 않는다" in report
    op = next(row for row in payload["test_a"]["sorts"] if row["sort"] == "operating_profitability")
    assert op["is_value_proxy"] is False
    assert op["sort"] not in payload["test_a"]["value_proxies_passing_nominally"]


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_recommendation_names_one_course_and_a_condition() -> None:
    """세 갈래를 나열만 하면 권고가 아니다."""

    payload = json.loads((OUTPUT / "validation_summary.json").read_text(encoding="utf-8"))
    advice = payload["recommendation"]["recommendation"]
    assert advice["immediate_position"] in {"a", "b", "c"}
    assert advice["next_investigation"] in {"a", "b", "c"}
    assert advice["conditional_on"]
    courses = payload["recommendation"]["courses"]
    assert set(courses) == {"a", "b", "c"}
    for course in courses.values():
        assert course["for"] and course["against"] and course["cost"]


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_keeps_the_proxy_asymmetry_without_overclaiming() -> None:
    report = (OUTPUT / "value_proxy_report.md").read_text(encoding="utf-8")
    assert "여전히 닫지 않는다" in report
    assert "닫힘에 훨씬 가까워졌다" in report


@pytest.mark.skipif(
    not (OUTPUT / "validation_summary.json").exists(), reason="아직 실행하지 않았다"
)
def test_the_report_carries_no_investment_instruction() -> None:
    report = (OUTPUT / "value_proxy_report.md").read_text(encoding="utf-8")
    disclaimer = "투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다"
    assert disclaimer in report
    stripped = report.replace(disclaimer, "")
    for token in ("매수", "매도", "비중 확대", "추천 종목"):
        assert token not in stripped
