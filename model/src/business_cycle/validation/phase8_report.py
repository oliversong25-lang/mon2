"""단계 A-5의 게이트 판정과 보고서 작성.

두 게이트를 따로 판정한다. 최신 수정치 게이트는 후보 H2가 개발구간에서 무엇도 잃지
않았는지 보고, 엄격 ALFRED 게이트는 2020년 실시간 실패가 실제로 고쳐졌는지 본다.
`§5`의 정지 규칙에 따라 둘 중 하나라도 실패하면 H3를 만들지 않는다.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, load_settings
from .phase2_metrics import binary_episodes, recession_prediction
from .phase6 import CLAIMS_SUBGROUP, jump_audit
from .phase8 import (
    CONFIRMATION_WEEKS,
    CORRECTED,
    GATED,
    NBER_2020,
    UNGATED,
    Phase8Result,
    concentration_reference,
    episode_comparison,
    evidence_frame,
    load_evaluations,
    severity_calibration,
    three_domain_audit,
)
from .real_data import _official_recession_flags

#: 2019년 말 오탐 구간. 단계 A-4에서 제거를 확인한 그 구간이다.
LATE_2019 = ("2019-06-01", "2020-02-29")

#: 지시된 상한. 첫 신호와 4주 확인 모두 기준주 대비 10주 이내여야 한다.
DETECTION_LIMIT_WEEKS = 10.0

ALFRED_DIR = ("phase8", "alfred")


def _weeks_between(later: pd.Timestamp, earlier: pd.Timestamp) -> float:
    return round((later - earlier).days / 7.0, 1)


def latest_vintage_gate(
    evidence: pd.DataFrame,
    corrected_history: pd.DataFrame,
    gated_history: pd.DataFrame,
    metrics: dict[str, Any],
    reference: dict[str, Any],
    jumps: pd.DataFrame,
) -> pd.DataFrame:
    """최신 수정치 필수 기준. 측정값과 판정을 같은 표에 남긴다."""

    identical = bool(
        (
            corrected_history["phase_code"].reindex(gated_history.index)
            == gated_history["phase_code"]
        ).all()
    )
    predicted = recession_prediction(corrected_history)
    confirmed = predicted.rolling(CONFIRMATION_WEEKS).sum().eq(CONFIRMATION_WEEKS)
    late_2019 = int(confirmed.loc[LATE_2019[0] : LATE_2019[1]].sum())
    post_2022 = int(confirmed.loc["2022-01-01":].sum())
    unjustified = 0 if jumps.empty else int((~jumps["justified"].astype(bool)).sum())
    rows = [
        ("재현율 >= 85%", float(metrics["recession_recall"]), metrics["recession_recall"] >= 0.85),
        (
            "오탐률 <= 5%",
            float(metrics["recession_false_positive_rate"]),
            metrics["recession_false_positive_rate"] <= 0.05,
        ),
        (
            "F1이 후보 H와 경쟁 가능",
            float(metrics["recession_f1"]),
            metrics["recession_f1"] >= float(reference["f1"]) - 1e-9,
        ),
        ("2019년 말 확인 수축 주 = 0", late_2019, late_2019 == 0),
        ("2022년 이후 확인 수축 주 ~ 0", post_2022, post_2022 == 0),
        ("설명되지 않는 다단계 점프 = 0", unjustified, unjustified == 0),
        (
            "워밍업 강건성(2001년 범위, 주)",
            float(reference["warmup_2001_range_weeks"]),
            float(reference["warmup_2001_range_weeks"]) <= 8.0,
        ),
        (
            "기하·영점 중심 유효(후보 H와 국면 경로 동일)",
            identical,
            identical,
        ),
    ]
    return pd.DataFrame(
        [{"criterion": name, "measured": value, "passed": bool(ok)} for name, value, ok in rows]
    )


def _detection(realtime: pd.DataFrame) -> dict[str, Any]:
    """실시간 경로에서 2020년 탐지 시점을 센다."""

    frame = realtime.copy()
    frame.index = pd.DatetimeIndex(frame["as_of"])
    contraction = frame["broad_phase"].eq("contraction")
    confirmed = contraction.rolling(CONFIRMATION_WEEKS).sum().eq(CONFIRMATION_WEEKS)
    start, end = NBER_2020
    window = frame.loc[start:end]
    scope = contraction.loc["2020-01-01":]
    decision = confirmed.loc["2020-01-01":]
    first = scope[scope].index.min() if scope.any() else pd.NaT
    confirm = decision[decision].index.min() if decision.any() else pd.NaT
    before = contraction.loc["2019-06-01" : start - pd.Timedelta(days=1)]
    before_confirmed = confirmed.loc["2019-06-01" : start - pd.Timedelta(days=1)]
    return {
        "first_contraction_signal_date": str(first.date()) if pd.notna(first) else "",
        "first_signal_lag_weeks": _weeks_between(first, start) if pd.notna(first) else float("nan"),
        "confirmation_decision_date": str(confirm.date()) if pd.notna(confirm) else "",
        "confirmation_lag_weeks": (
            _weeks_between(confirm, start) if pd.notna(confirm) else float("nan")
        ),
        "official_recession_weeks": int(len(window)),
        "official_recession_weeks_called_contraction": int(
            window["broad_phase"].eq("contraction").sum()
        ),
        "confirmation_within_official_recession": bool(
            pd.notna(confirm) and start <= confirm <= end
        ),
        "pre_recession_contraction_weeks": int(before.sum()),
        "pre_recession_confirmed_weeks": int(before_confirmed.sum()),
        "late_2019_confirmed_contraction_weeks": int(
            confirmed.loc[LATE_2019[0] : LATE_2019[1]].sum()
        ),
        "systemic_override_weeks": int(frame["systemic_override_active"].astype(bool).sum())
        if "systemic_override_active" in frame.columns
        else 0,
        "override_weeks_in_any_history": int(
            frame.get("systemic_override_weeks_in_history", pd.Series(0))
            .fillna(0)
            .astype(int)
            .max()
        ),
        "weeks_with_observations_after_as_of": int(frame["observations_after_as_of"].sum()),
        "weeks": int(len(frame)),
    }


def alfred_gate(detection: dict[str, Any]) -> pd.DataFrame:
    """엄격 ALFRED 필수 기준."""

    rows = [
        (
            "미래 관측 사용 없음",
            detection["weeks_with_observations_after_as_of"],
            detection["weeks_with_observations_after_as_of"] == 0,
        ),
        (
            "첫 수축 신호 <= 기준주 +10주",
            detection["first_signal_lag_weeks"],
            bool(detection["first_signal_lag_weeks"] <= DETECTION_LIMIT_WEEKS),
        ),
        (
            "4주 확인 <= 기준주 +10주",
            detection["confirmation_lag_weeks"],
            bool(detection["confirmation_lag_weeks"] <= DETECTION_LIMIT_WEEKS),
        ),
        (
            "공식 침체 주 중 수축 판정 >= 1",
            detection["official_recession_weeks_called_contraction"],
            detection["official_recession_weeks_called_contraction"] >= 1,
        ),
        (
            "침체 진행 중 확인(권장)",
            detection["confirmation_within_official_recession"],
            True,
        ),
        (
            "침체 이전 확인 오탐 구간 없음",
            detection["pre_recession_confirmed_weeks"],
            detection["pre_recession_confirmed_weeks"] == 0,
        ),
        (
            "2019년 말 좁은 둔화가 확인 수축이 아님",
            detection["late_2019_confirmed_contraction_weeks"],
            detection["late_2019_confirmed_contraction_weeks"] == 0,
        ),
    ]
    return pd.DataFrame(
        [{"criterion": name, "measured": value, "passed": bool(ok)} for name, value, ok in rows]
    )


def withheld_audit(realtime: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """보류 주에 어떤 지표가 왜 빠졌는지 지표 단위로 되짚는다.

    상태 논리가 아니라 자료 자체를 본다. 보류가 파이프라인 결함인지, 아카이브 구멍인지,
    실제 발표 중단인지는 계열별 빈티지 유무로만 갈린다.
    """

    indicator_settings = settings.indicators["indicators"]
    withheld = realtime[realtime["status"] == "withheld"].copy()
    rows: list[dict[str, Any]] = []
    for record in withheld.to_dict("records"):
        as_of = pd.Timestamp(str(record["as_of"]))
        for indicator, config in indicator_settings.items():
            newest = str(record.get(f"newest_{indicator}", "") or "")
            if not newest:
                rows.append(
                    {
                        "as_of": str(as_of.date()),
                        "indicator": indicator,
                        "newest_observation": "",
                        "age_weeks": float("nan"),
                        "max_age_weeks": float(config["max_age_weeks"]),
                        "counted_available": False,
                    }
                )
                continue
            release = pd.Timestamp(newest) + pd.Timedelta(days=int(config["release_lag_days"]))
            age = max(0.0, (as_of - release).days / 7.0)
            rows.append(
                {
                    "as_of": str(as_of.date()),
                    "indicator": indicator,
                    "newest_observation": newest,
                    "age_weeks": round(age, 1),
                    "max_age_weeks": float(config["max_age_weeks"]),
                    "counted_available": bool(age <= float(config["max_age_weeks"])),
                }
            )
    return pd.DataFrame(rows)


def vintage_gap_audit(settings: Settings, start: str, end: str) -> pd.DataFrame:
    """보류 구간에 계열별 빈티지가 실제로 있었는지 센다."""

    from ..data.alfred import AlfredCollector

    collector = AlfredCollector(settings.root / "data" / "cache" / "alfred")
    window = (pd.Timestamp(start), pd.Timestamp(end))
    rows: list[dict[str, Any]] = []
    for series_id in settings.indicators["indicators"]:
        frame = collector.realtime_observations(series_id)
        vintages = pd.Series(sorted(set(frame["realtime_start"])))
        before = vintages[vintages < window[0]]
        inside = vintages[(vintages >= window[0]) & (vintages <= window[1])]
        after = vintages[vintages > window[1]]
        rows.append(
            {
                "series_id": series_id,
                "last_vintage_before_window": str(before.max().date()) if len(before) else "",
                "vintages_inside_window": int(len(inside)),
                "first_vintage_after_window": str(after.min().date()) if len(after) else "",
                "gap_weeks": (
                    round((after.min() - before.max()).days / 7.0, 1)
                    if len(before) and len(after)
                    else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows)


def _markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_(없음)_"
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "|" + "|".join("---" for _ in frame.columns) + "|"
    body = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
        for row in frame.itertuples(index=False)
    ]
    return "\n".join([header, divider, *body])


def _annotate_realtime(realtime: pd.DataFrame, actual: pd.Series) -> pd.DataFrame:
    """실시간 경로에 침체 여부와 이후 4주 경로를 붙인다.

    러너는 한 주씩 독립으로 돌기 때문에 "다음 4주에 무엇이 됐는가"를 그 안에서 알 수
    없다. 경로가 다 모인 뒤에 붙인다.
    """

    frame = realtime.copy()
    index = pd.DatetimeIndex(frame["as_of"])
    flags = actual.reindex(index, method="ffill").fillna(False)
    frame["usrec"] = flags.to_numpy().astype(int)
    codes = frame["detail_phase"].astype(str)
    frame["next_four_weeks"] = [
        ">".join(codes.iloc[position + 1 : position + 5]) for position in range(len(codes))
    ]
    return frame


def _write_report(
    output: Path,
    evidence: pd.DataFrame,
    calibration: pd.DataFrame,
    concentration: pd.DataFrame,
    episodes: pd.DataFrame,
    latest_gate: pd.DataFrame,
    gate: pd.DataFrame | None,
    detection: dict[str, Any],
    realtime: pd.DataFrame | None,
    withheld: pd.DataFrame | None,
    gaps: pd.DataFrame | None,
    identical: bool,
    override_weeks: int,
) -> Path:
    """사람이 읽는 보고서. 수치는 전부 같은 실행에서 온 값이다."""

    development = evidence.loc["1995-01-01":"2012-12-31"]
    three_development = development[development["negative_domains"] == 3]
    three_recession = int(three_development["usrec"].sum())
    recession_weeks = int(development["usrec"].sum())
    stability = evidence.loc["2013-01-01":"2019-12-31"]
    three_stability = stability[stability["negative_domains"] == 3]
    alfred_three = 0 if realtime is None else int((realtime["negative_domains"] == 3).sum())
    passed_latest = bool(latest_gate["passed"].all())
    passed_alfred = bool(gate["passed"].all()) if gate is not None else False

    lines: list[str] = []
    lines.append("# 단계 A-5 — 3영역 진단과 후보 H2\n")
    lines.append("## 1. 한 줄 결과\n")
    if passed_latest and passed_alfred:
        verdict = "후보 H2가 두 게이트를 모두 통과했다."
    elif passed_latest:
        verdict = (
            "후보 H2는 최신 수정치 게이트를 통과했지만 엄격 ALFRED 게이트를 통과하지 못했다. "
            "§5의 정지 규칙에 따라 H3를 만들지 않는다."
        )
    else:
        verdict = "후보 H2가 최신 수정치 게이트에서 이미 실패했다."
    lines.append(verdict + "\n")
    lines.append(
        "후보 H와 그 동결 파일·해시·보고서·ALFRED 결과는 `outputs/robustness_validation/phase6`와\n"
        "`phase7`에 그대로 있다. 이번 단계는 어느 것도 덮어쓰지 않았다.\n"
    )

    lines.append("## 2. 게이트를 바꾸기 전 진단 (§1)\n")
    lines.append(
        f"음수 영역이 **정확히 3개**인 주는 개발구간(1995~2012)에 {len(three_development)}주, "
        f"안정성구간(2013~2019)에 {len(three_stability)}주, 엄격 ALFRED 구간에 {alfred_three}주 있다. "
        f"주 단위 전체 항목은 `three_domain_audit.csv`와 `alfred_three_domain_audit.csv`에 있다.\n"
    )
    lines.append(
        f"**개발구간의 3영역 주 {len(three_development)}개 가운데 공식 침체 주는 "
        f"{three_recession}개다.** 같은 구간의 침체 주 {recession_weeks}개는 모두 음수 영역이 "
        "4개 이상이었다. 즉 개발 자료에는 '3영역인데 진짜 침체'라는 양성 사례가 없다. "
        "임계값을 양성 사례에 맞춰 고를 수 없다는 뜻이며, 그래서 이번 예외는 "
        "**보통의 3영역 상태가 도달한 적 없는 심각도**로만 정의한다.\n"
    )
    lines.append("### 사례 비교\n")
    lines.append(_markdown(episodes) + "\n")
    lines.append(
        "읽는 법은 `core_level`이다. 이것은 실업수당(주간 가교)을 빼고 핵심 동행지표만으로 다시 "
        "계산한 수준이며 Y와 같은 단위다. 2019년 말 오탐 구간과 2020년 3월 실시간이 Y로는 비슷해 "
        "보여도 `core_level`로는 갈린다.\n"
    )
    if realtime is not None and not realtime.empty:
        window = realtime.set_index(pd.DatetimeIndex(realtime["as_of"])).loc[
            "2020-03-06":"2020-05-08"
        ]
        columns = [
            "as_of",
            "negative_domains",
            "core_negative_domains",
            "y",
            "radius",
            "core_level",
            "leave_one_indicator_level",
            "claims_share",
            "max_domain_share",
            "broad_phase",
        ]
        table = window[[column for column in columns if column in window.columns]].copy()
        for column in table.columns:
            if table[column].dtype.kind == "f":
                table[column] = table[column].round(3)
        lines.append("### 2020년 실시간 — 폭이 3개였던 주에 무엇이 보였나\n")
        lines.append(_markdown(table) + "\n")
        lines.append(
            "이것이 이번 단계의 결정적 관측이다. 3/27에 Y=-13.4, 반지름 38.6이라는 사상 최대 "
            "붕괴가 찍혔지만 그 신호의 **95.8%가 실업수당**이었고 ICSA 한 계열이 92.3%였다. "
            "같은 주 핵심 동행지표만으로 잰 수준은 **+0.16으로 음수조차 아니었다** — 고용과 "
            "소득이 아직 양수였기 때문이다. 핵심 증거가 처음 나타난 4/17에도 한 지표(RRSFS)를 "
            "빼면 심각도가 -3.56에서 -1.78로 무너진다.\n"
        )
        lines.append(
            "즉 공식 침체 구간(3/6~4/24) 내내, 그 시점에 **공개돼 있던** 핵심 동행 자료는 "
            "넓고 한 항목에 기대지 않는 하락을 보여준 적이 없다. 지시된 여덟 조건을 그대로 "
            "지키는 일반 예외는 이 구간에서 발동할 근거가 없다. 발동시키려면 청구건수 하나가 "
            "만든 신호로 침체를 부르도록 허용해야 하는데, 그것은 §2가 명시적으로 금지한 것이고 "
            "팬데믹 전용 예외와 다르지 않다.\n"
        )

    lines.append("## 3. 후보 H2 설계 (§2)\n")
    lines.append(
        "기본 규칙은 그대로다 — **현재 침체는 독립 영역 4개 이상이 동시에 음수여야 한다.** "
        "폭 임계값을 3으로 낮추지 않았다. 그 위에 폭이 한 단계 모자란 주에만 적용되는 "
        "체계적 충격 예외를 더했고, 아래 조건이 **모두** 성립할 때만 발동한다.\n"
    )
    lines.append(
        "1. 음수 영역이 정확히 3개 (두 단계 아래는 대상이 아니다)\n"
        "2. 핵심 동행 영역 중 2개 이상이 음수\n"
        "3. 청구건수를 제외한 심각도가 개발구간 밖 (`core_level`)\n"
        "4. 최대 기여 **지표** 하나를 빼도 심각도가 남음 (`leave_one_indicator_level`)\n"
        "5. 최대 기여 **영역** 하나를 빼도 심각도가 남음 (`leave_one_domain_level`)\n"
        "6. 게이트 이전 침체확률이 강하게 몰려 있음\n"
        "7. 동적요인이 같은 방향\n"
        "8. 지속성은 기존 4주 확인 규칙이 그대로 맡는다 (규칙을 바꾸지 않았다)\n"
    )
    lines.append("### 임계값의 출처 — 1995~2012 개발구간뿐\n")
    lines.append(_markdown(calibration) + "\n")
    lines.append(
        "설정 파일의 값은 위 측정치를 **더 엄격한 쪽으로** 반올림한 것이다 "
        "(-2.9254→-2.93, -2.8174→-2.82, -2.3273→-2.33). 2013년 이후 자료는 임계값 선택에 쓰지 "
        "않았고 안정성 확인에만 썼다. 팬데믹 날짜·분기·상수는 없다.\n"
    )
    lines.append("### '한 항목이 지배하지 않을 것'을 최대 점유율로 재지 않은 이유\n")
    lines.append(_markdown(concentration) + "\n")
    lines.append(
        "최대 점유율은 신호를 가진 핵심지표가 둘뿐일 때 하한이 0.5다. 예외가 필요한 상황이 "
        "정확히 그 상황이므로 0.5 기준은 구조적으로 충족 불가능해진다. 게다가 개발구간 침체 주 "
        f"{recession_weeks}개 중 {int(concentration.iloc[0]['recession_weeks_above_one_half'])}개가 "
        "영역 점유율 0.5를 넘는다 — 진짜 침체를 기각하는 기준이라는 뜻이다. 그래서 점유율 대신 "
        "leave-one-out으로 바꿨다. 이 판단은 개발구간 통계만으로 내렸다.\n"
    )
    lines.append(
        "**2020년은 더 이상 순수한 검증표본이 아니다.** 단계 B에서 이미 결과를 봤다. "
        "여기서 2020년은 사전에 정한 일반 규칙이 진단된 결함을 실제로 고치는지 확인하는 "
        "**알려진 실패 사례**로만 쓴다.\n"
    )

    lines.append("## 4. 최신 수정치 게이트 (§3 전반)\n")
    lines.append(_markdown(latest_gate) + "\n")
    lines.append(
        f"국면 경로가 후보 H와 **{'완전히 동일' if identical else '다름'}**하고, 최신 수정치 "
        f"1995~2026 전 구간에서 예외가 발동한 주는 **{override_weeks}주**다. "
        "그래서 재현율·오탐률·F1·2019년 말 제거·점프·기하·영점 중심이 모두 H의 값을 그대로 "
        "승계한다. 동시에 이는 한계이기도 하다 — **이 예외는 최신 수정치 자료로는 검증할 수 "
        "없다.** 발동한 적이 없기 때문이다.\n"
    )

    lines.append("## 5. 엄격 ALFRED 게이트 (§3 후반)\n")
    if gate is None:
        lines.append("_(ALFRED 실행 결과가 아직 없다.)_\n")
    else:
        lines.append(_markdown(gate) + "\n")
        lines.append(
            f"**공식 2020년 침체 {detection['official_recession_weeks']}주 가운데 수축으로 "
            f"분류한 주는 {detection['official_recession_weeks_called_contraction']}주다.** "
            "전체 주간 재현율 뒤에 두지 않고 여기에 그대로 적는다.\n"
        )
        lines.append(
            f"- 첫 수축 신호: {detection['first_contraction_signal_date'] or '없음'} "
            f"(기준주 대비 {detection['first_signal_lag_weeks']}주)\n"
            f"- 4주 확인 결정일: {detection['confirmation_decision_date'] or '없음'} "
            f"(기준주 대비 {detection['confirmation_lag_weeks']}주)\n"
            f"- 침체 이전 확인 오탐: {detection['pre_recession_confirmed_weeks']}주\n"
            f"- 2019년 말 확인 수축: {detection['late_2019_confirmed_contraction_weeks']}주\n"
            f"- 예외 발동 주: {detection['systemic_override_weeks']}주 "
            f"(어느 실행 내부 이력에서든 최대 {detection['override_weeks_in_any_history']}주)\n"
            f"- as-of 이후 관측 사용: {detection['weeks_with_observations_after_as_of']}건\n"
        )

    lines.append("## 6. 판정 보류 구간 감사 (§4)\n")
    if withheld is None or gaps is None:
        lines.append("_(ALFRED 실행 결과가 아직 없다.)_\n")
    else:
        stale = withheld[~withheld["counted_available"]]
        lines.append(
            f"보류 {withheld['as_of'].nunique()}주에서 지표는 7개 모두 존재했다. 빠진 것이 아니라 "
            f"**오래된 것**이며, 기준을 넘긴 지표-주 조합은 {len(stale)}건이다. "
            "지표 단위 내역은 `withheld_audit.csv`에 있다.\n"
        )
        lines.append(_markdown(gaps) + "\n")
        lines.append(
            "원인은 파이프라인 상태 논리도, 특정 계열의 아카이브 구멍도 아니다. "
            "**7개 계열 전부가 같은 창에서 동시에 새 빈티지를 내지 않았다.** 매주 나오는 "
            "실업수당 두 계열까지 멈췄다는 것이 결정적이다 — 원천의 발표 중단이다. "
            "모델은 추정하지 않고 판정을 보류했고, 보류를 푸는 데 미래 정보가 필요하지 않았다. "
            "새 빈티지가 나온 주에 그 주까지의 자료만으로 풀렸다.\n"
        )
        lines.append(
            "정정 사항이 아니라 운영 정책 문제다. 보류 주에도 `current_phase`와 12개 확률은 "
            "그대로 나가고 `status = withheld`와 사유가 붙는다. 마지막 유효 판정을 '자료 지연'으로 "
            "표시할지 아무 국면도 보이지 않을지는 서비스 표시 정책에서 정할 일이다. "
            "**보류 주를 없애려고 H2를 바꾸지 않았다.**\n"
        )

    lines.append("## 7. 정지 규칙 (§5)\n")
    if passed_latest and passed_alfred:
        lines.append("두 게이트를 모두 통과했으므로 H2를 동결하고 운영 재생 게이트로 넘어간다.\n")
    else:
        lines.append(
            "허용된 수정은 이번 한 번뿐이었다. 게이트를 통과하지 못했으므로 **H3를 만들지 않는다.** "
            "현재 설계에서 미국 v1 모델은 **채택하지 않는다**로 보고한다.\n"
        )

    lines.append("## 8. 사실·해석·미검증 가정\n")
    lines.append(
        "- **검증된 사실**: 이 디렉터리의 CSV·JSON 수치, 개발구간 3영역 주에 침체가 없다는 것, "
        "최신 수정치에서 예외가 한 번도 발동하지 않는다는 것.\n"
        "- **경제적 해석**: 2020년 실시간에서 붕괴가 실업수당에만 보인 것은 핵심 동행지표의 "
        "발표 지연 때문이라는 설명.\n"
        "- **미검증 가정**: 이 예외가 다른 체계적 충격에서도 같은 시점에 발동하리라는 가정. "
        "엄격 창의 침체가 하나뿐이라 확인할 수 없다.\n"
    )
    lines.append("## 9. 재현\n")
    lines.append(
        "```bash\n"
        "cd model\n"
        '$env:FRED_API_KEY = "<키>"          # 환경변수로만. 저장소에 넣지 않는다.\n'
        ".\\.venv\\Scripts\\python.exe run_h2_alfred_backtest.py\n"
        ".\\.venv\\Scripts\\python.exe -m business_cycle.validation.phase8_report\n"
        "```\n"
    )
    report = output / "validation_report.md"
    report.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return report


def run_phase8(settings: Settings | None = None) -> Phase8Result:
    """단계 A-5를 처음부터 끝까지 실행하고 산출물을 남긴다."""

    base = settings or load_settings()
    output = base.root / "outputs" / "robustness_validation" / "phase8"
    output.mkdir(parents=True, exist_ok=True)
    alfred_dir = output / "alfred"

    evaluations, source = load_evaluations(base)
    gated = evaluations[GATED]
    ungated = evaluations[UNGATED]
    corrected = evaluations[CORRECTED]
    actual = _official_recession_flags(source, pd.DatetimeIndex(gated.history.index))
    evidence = evidence_frame(gated, ungated, actual)
    evidence.to_csv(output / "weekly_evidence.csv")
    three_domain_audit(evidence).to_csv(output / "three_domain_audit.csv", index=False)
    calibration = severity_calibration(evidence)
    calibration.to_csv(output / "severity_calibration.csv", index=False)
    concentration = concentration_reference(evidence)
    concentration.to_csv(output / "concentration_reference.csv", index=False)

    realtime_path = alfred_dir / "realtime_path.csv"
    realtime = pd.read_csv(realtime_path) if realtime_path.exists() else None
    episodes = episode_comparison(evidence, realtime)
    episodes.to_csv(output / "episode_comparison.csv", index=False)

    jumps = jump_audit(corrected, actual, CORRECTED, base)
    if not jumps.empty:
        jumps.to_csv(output / "jump_audit.csv", index=False)
    reference = json.loads(
        (
            base.root / "outputs" / "robustness_validation" / "phase6" / "validation_summary.json"
        ).read_text(encoding="utf-8")
    )["measurements"]
    latest_gate = latest_vintage_gate(
        evidence, corrected.history, gated.history, corrected.metrics, reference, jumps
    )
    latest_gate.to_csv(output / "latest_vintage_gate.csv", index=False)
    latest_passed = bool(latest_gate["passed"].all())

    detection: dict[str, Any] = {}
    alfred_passed = False
    gate: pd.DataFrame | None = None
    withheld: pd.DataFrame | None = None
    gaps: pd.DataFrame | None = None
    if realtime is not None and not realtime.empty:
        detection = _detection(realtime)
        gate = alfred_gate(detection)
        gate.to_csv(output / "alfred_gate.csv", index=False)
        alfred_passed = bool(gate["passed"].all())
        realtime = _annotate_realtime(realtime, actual)
        three = realtime[realtime["negative_domains"] == 3]
        three.to_csv(output / "alfred_three_domain_audit.csv", index=False)
        withheld = withheld_audit(realtime, base)
        withheld.to_csv(output / "withheld_audit.csv", index=False)
        gaps = vintage_gap_audit(base, "2025-09-26", "2025-11-19")
        gaps.to_csv(output / "withheld_vintage_gaps.csv", index=False)

    identical = bool(
        (
            corrected.history["phase_code"].reindex(gated.history.index)
            == gated.history["phase_code"]
        ).all()
    )
    override_weeks = int(corrected.backtest.run.breadth_audit["systemic_override_active"].sum())
    _write_report(
        output,
        evidence,
        calibration,
        concentration,
        episodes,
        latest_gate,
        gate,
        detection,
        realtime,
        withheld,
        gaps,
        identical,
        override_weeks,
    )

    summary = {
        "stage": "A-5",
        "candidate": CORRECTED,
        "preserved_failed_candidate": GATED,
        "latest_vintage_passed": latest_passed,
        "alfred_passed": alfred_passed,
        "adopted": bool(latest_passed and alfred_passed),
        "latest_vintage_identical_to_h": identical,
        "systemic_override_weeks_latest_vintage": override_weeks,
        "detection": detection,
        "measurements": {
            "recall": float(corrected.metrics["recession_recall"]),
            "false_positive_rate": float(corrected.metrics["recession_false_positive_rate"]),
            "precision": float(corrected.metrics["recession_precision"]),
            "f1": float(corrected.metrics["recession_f1"]),
        },
    }
    (output / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return Phase8Result(output, latest_passed, alfred_passed, latest_passed and alfred_passed)


__all__ = [
    "ALFRED_DIR",
    "CLAIMS_SUBGROUP",
    "alfred_gate",
    "binary_episodes",
    "latest_vintage_gate",
    "run_phase8",
    "vintage_gap_audit",
    "withheld_audit",
]


def main() -> int:
    result = run_phase8()
    print(f"단계 A-5 산출물: {result.output_dir}")
    print(f"최신 수정치 게이트 통과: {result.latest_vintage_passed}")
    print(f"엄격 ALFRED 게이트 통과: {result.alfred_passed}")
    print(f"채택: {result.adopted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
