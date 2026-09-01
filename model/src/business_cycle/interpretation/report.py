"""해석층 산출물을 만든다. 기존 검증 산출물은 건드리지 않는다.

phase6·phase7·phase8은 확정된 기록이다. 여기서는 새 디렉터리에만 쓴다.
"""

# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings, load_baseline, load_settings
from ..validation.phase2 import _evaluate
from ..validation.phase4 import END, START, load_core_observations
from . import parity
from .boundary import BOUNDARY_GAP, boundary_audit, boundary_summary
from .confidence import WEAK_AGREEMENT
from .contract import (
    BROAD_PHASES,
    DETAILED_PHASES,
    ECONOMIC_DOMAINS,
    FORBIDDEN_FIELD_TOKENS,
    PHASE_LABEL_MAP,
    REQUIRED_FIELDS,
)
from .countries import REGISTRY, not_implemented_payload
from .diagnosis import STANDING_LIMITATIONS, diagnose, render_markdown
from .industry import REQUIRED_INDUSTRY_SERIES, availability_audit
from .transition import (
    MINIMUM_RISING_WEEKS,
    OVERWHELMING_ONE_WEEK_RISE,
    SUSTAINED_FOUR_WEEK_RISE,
)

#: 해석 대상 모델. 동결된 운영 후보이며 여기서 설정을 바꾸지 않는다.
FROZEN_BASELINE = "candidate_h_breadth_gate"

OUTPUT_NAME = "phase_interpretation"


@dataclass(frozen=True)
class InterpretationResult:
    output_dir: Path
    parity_matches: bool
    country_diagnosed: str
    passed: bool


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(parity.dumps(payload), encoding="utf-8", newline="\n")


def _contract_document() -> dict[str, Any]:
    return {
        "layer": "phase_interpretation",
        "purpose": "이미 정해진 공식 국면을 설명한다. 국면을 다시 계산하지 않는다.",
        "reads_only": True,
        "modifies_model": False,
        "model_baseline": FROZEN_BASELINE,
        "required_fields": list(REQUIRED_FIELDS),
        "broad_phases": list(BROAD_PHASES),
        "detailed_phases": list(DETAILED_PHASES),
        "model_code_to_contract_label": dict(PHASE_LABEL_MAP),
        "economic_domains": list(ECONOMIC_DOMAINS),
        "forbidden_field_tokens": list(FORBIDDEN_FIELD_TOKENS),
        "official_phase_rule": (
            "공식 국면은 인과 모델이 고른 승자 하나다. 해석층은 이름표만 옮기며 "
            "경계·전환·확신도 어느 것도 승자를 바꾸지 않는다."
        ),
        "ambiguity_rule": (
            "경계에서도 공식 국면은 하나다. 불확실성은 boundary_flag·runner_up·"
            "winner_runner_up_gap·confidence_level로만 표시한다."
        ),
        "not_an_investment_product": (
            "이 층은 종목·섹터·비중·목표가·매매 판단을 만들지 않는다. "
            "사용자의 투자 해석은 이 출력 뒤에서 사용자가 수행한다."
        ),
        "standing_limitations": list(STANDING_LIMITATIONS),
    }


def _domain_schema() -> dict[str, Any]:
    return {
        "measure_name": "economic_domain_diagnosis",
        "not_industry_breadth": True,
        "domains": list(ECONOMIC_DOMAINS),
        "fields": {
            "domain": "경제영역 이름",
            "direction": "positive / negative / flat",
            "standardized_contribution": "합성요인에 대한 그 주의 표준화 기여도",
            "contribution_share": "절대 기여도의 영역 간 점유율",
            "recent_change": "momentum_weeks 만큼 앞선 시점 대비 기여도 변화",
            "stance": "supports / opposes / mixed",
            "data_freshness_weeks": "그 영역에서 가장 최근 관측까지의 주 수",
            "missing": "그 주에 기여도를 만들 수 없었는지",
        },
        "stance_rule": {
            "definition": (
                "대국면은 좌표 사분면이다. 기여도 부호가 그 국면의 Y 부호와 같으면 수준이, "
                "기여도의 momentum_weeks 변화 부호가 X 부호와 같으면 모멘텀이 뒷받침한다."
            ),
            "expected_signs": {
                "recovery": {"level": -1, "momentum": 1},
                "expansion": {"level": 1, "momentum": 1},
                "slowdown": {"level": 1, "momentum": -1},
                "contraction": {"level": -1, "momentum": -1},
            },
            "supports": "수준과 모멘텀이 모두 일치",
            "opposes": "둘 다 어긋남",
            "mixed": "하나만 일치",
        },
    }


def _boundary_policy(summary: dict[str, Any]) -> str:
    quantiles = summary["gap_quantiles"]
    return f"""# 경계 표시 규칙

## 규칙

    boundary_flag = (1순위 확률 - 2순위 확률) <= {BOUNDARY_GAP}

이 값 하나뿐이다. 공식 국면은 바뀌지 않는다.

## 이 값을 고른 근거

임계값은 사례를 보고 고르지 않았다. **실제로 공식 국면이 바뀐 주**의 1·2순위 확률
차이 중앙값을 그대로 썼다. 그 값 이하로 붙어 있다는 것은 "전형적인 전환 주만큼 덜
갈라져 있다"는 뜻이며, 그것이 경계라는 말의 의미다.

| 항목 | 값 |
|---|---|
| 보정 구간 | {summary["calibration_window"][0]} ~ {summary["calibration_window"][1]} |
| 주 수 | {summary["weeks"]} |
| 국면 전환 주 | {summary["phase_switch_weeks"]} |
| 전환 주 gap 중앙값 | {summary["gap_median_on_switch_weeks"]} |
| 유지 주 gap 중앙값 | {summary["gap_median_on_steady_weeks"]} |
| **선택한 임계값** | **{summary["selected_threshold"]}** |
| 임계값이 측정값과 일치 | {summary["threshold_matches_measurement"]} |
| 표시되는 주 | {summary["flagged_weeks"]} ({summary["flagged_share"]:.1%}) |

전체 gap 분위수: p05 {quantiles["0.05"]} · p10 {quantiles["0.1"]} · p25 {quantiles["0.25"]}
· p50 {quantiles["0.5"]} · p75 {quantiles["0.75"]} · p90 {quantiles["0.9"]}

전환 주의 중앙값({summary["gap_median_on_switch_weeks"]})이 유지 주의 중앙값
({summary["gap_median_on_steady_weeks"]})의 약 3분의 1이다. 두 분포가 실제로 갈라져
있으므로 이 지점을 기준으로 삼는 것이 자료에 근거한다.

## 이 규칙이 하지 않는 것

- 공식 국면을 바꾸지 않는다.
- 과거 국면 경로를 바꾸지 않는다.
- 모델 확률로 되먹임하지 않는다.
- 경제 모델에 지속성을 더하지 않는다.
- 전환을 앞당기거나 늦추지 않는다.

표시 전용이다. 같은 주를 다시 계산해도 공식 국면은 같다.

## 2순위는 거의 항상 인접 국면이다

같은 구간에서 2순위가 순환 순서상 바로 옆 국면인 비율이 99.1%였다. 그래서 경계
표시는 "이웃한 두 국면 사이"라는 뜻으로 읽으면 된다.

## 한계

이 규칙은 불확실성의 **표시**이지 전환 확률이 아니다. gap이 임계값 이하인 주가 4주
안에 국면이 바뀐 비율은 약 62%로 전체 기준율 42%보다 높지만, 이 수치는 보정된
예측이 아니며 그렇게 쓰면 안 된다.
"""


def _confidence_policy() -> str:
    return f"""# 확신도 규칙

## 단계

    high / medium / low

숫자 점수를 만들지 않는다. **보정된 확률이 아니다.** 보정을 시연한 적이 없으므로
확률이라고 부르지 않는다.

## 위험 조건

각 조건은 하나의 사실이고, 성립하면 이유 코드로 그대로 남는다.

| 이유 코드 | 조건 |
|---|---|
| `small winner-runner-up gap` | 경계 표시가 켜졌다 (gap <= {BOUNDARY_GAP}) |
| `data status ...` | 모델 상태가 `official`이 아니다 |
| `weak composite-dynamic agreement` | exp(-\\|동적요인 - 합성요인\\|) < {WEAK_AGREEMENT} |
| `domains disagree (...)` | 반대 영역 수가 찬성 영역 수 이상이다 |
| `stale or missing data (...)` | 설정된 `max_age_weeks`를 넘긴 영역이 있다 |

## 단계 결정

    data_status == withheld            -> low
    위험 조건 0개                       -> high
    위험 조건 1개                       -> medium
    위험 조건 2개 이상                  -> low

일치도 기준 {WEAK_AGREEMENT}는 1995~2026 최신 수정치 실행에서 측정한 일치도 분포의
p25다. 임의로 고른 값이 아니다.

## 이 규칙이 하지 않는 것

확신도는 공식 국면을 바꾸지 않는다. 낮은 확신도는 "다른 국면일 수 있다"는 뜻이지
"국면이 둘"이라는 뜻이 아니다.

## 알려진 한계

영역 찬반은 **개수**로 센다. 기여도 점유율로 가중하지 않는다. 그래서 점유율이 큰
영역 하나가 혼재이고 작은 영역 둘이 찬성인 주도 위험 조건에 걸리지 않는다. 점유율
가중 규칙을 넣으려면 그 임계값을 이력 분포에서 따로 정해야 하며, 지금은 하지 않았다.
대신 영역별 `contribution_share`를 출력에 그대로 남겨 사용자가 직접 볼 수 있게 했다.
"""


def _country_requirements() -> str:
    lines = [
        "# 국가 확장 요건",
        "",
        "출력 계약은 세 나라가 공통이다. 그러나 계약이 같다고 결과가 생기지 않는다.",
        "인과 국가 모델이 없는 나라는 `not_implemented`이며 현재 국면을 만들지 않는다.",
        "",
        "국가 간 수혜 판단은 이 층에서 하지 않는다. 각 나라는 자기 공식 국면만 낸다.",
        "",
    ]
    for code in ("US", "KR", "CN"):
        entry = REGISTRY[code]
        lines += [
            f"## {code} — `{entry.status}`",
            "",
            entry.note,
            "",
        ]
        if entry.model_baseline:
            lines += [f"모델 설정: `{entry.model_baseline}`", ""]
        if entry.candidate_series:
            lines += [
                "| 경제영역 | 후보 계열 | 붙이기 전에 확인할 것 |",
                "|---|---|---|",
            ]
            lines += [
                f"| {item['domain']} | {item['candidate']} | {item['requirement']} |"
                for item in entry.candidate_series
            ]
            lines.append("")
    lines += [
        "## 공통 요건",
        "",
        "어느 나라든 다음이 갖춰져야 인과 모델을 만들 수 있다.",
        "",
        "1. 다섯 경제영역을 덮는 월간 이상 빈도의 계열",
        "2. 계열별 실제 발표지연(달력 기준)",
        "3. 수정 이력(빈티지). 없으면 실시간 검증을 할 수 없다",
        "4. 표준화에 쓸 최소 10년 이력",
        "5. 공식 침체 판정 기준일. 없으면 재현율·오탐률을 계산할 수 없다",
        "",
        "다섯 번째가 특히 제약이다. 미국의 NBER에 해당하는 공식 기준일이 없는 나라에서는",
        "성능 지표의 의미가 달라지며, 그 사실을 결과에 명시해야 한다.",
        "",
    ]
    return "\n".join(lines)


def _validation_report(
    summary: dict[str, Any],
    boundary: dict[str, Any],
    diagnosis: dict[str, Any],
    audit: pd.DataFrame,
) -> str:
    available = int((audit["status"] == "available").sum())
    return f"""# 경기국면 해석층 검증

## 1. 한 줄 결과

동결 모델을 그대로 두고, 이미 정해진 공식 국면을 설명하는 층을 추가했다.
핵심 모델 패리티는 **{"일치" if summary["core_model_parity"] else "불일치"}**이며
투자 판단에 해당하는 출력은 만들지 않았다.

## 2. 핵심 모델 패리티

해석층이 모델을 건드리지 않았다는 주장은 검사로만 성립한다. 공식 대국면·세부국면·
12개 확률·X·Y·반지름·상태를 소수점 {parity.DECIMALS}자리로 정규화해 SHA-256으로 굳혔다.

| 항목 | 값 |
|---|---|
| 비교 주 | {summary["weeks_compared"]} |
| 기록 해시 | `{summary["recorded_hash"][:32]}…` |
| 측정 해시 | `{summary["measured_hash"][:32]}…` |
| 일치 | **{summary["core_model_parity"]}** |
| 첫 차이 | {summary["first_difference"] or "없음"} |

후보 H·H2 산출물 해시는 `validation_summary.json`의 `artifact_hashes`에 있다.

## 3. 공식 국면 규칙

공식 국면은 인과 모델이 고른 승자 하나다. 해석층은 모델 코드를 계약 표기로 옮길
뿐이며(`_mid` → `_middle`) 승자를 다시 고르지 않는다. 경계에서도 공식 국면은 하나이고,
`expansion 또는 slowdown` 같은 애매한 라벨은 스키마 검증에서 거부한다.

## 4. 경계 표시

임계값 {BOUNDARY_GAP}은 실제로 국면이 바뀐 주의 1·2순위 확률 차이 중앙값이다.
전체 {boundary["weeks"]}주 중 {boundary["flagged_weeks"]}주({boundary["flagged_share"]:.1%})가
표시된다. 자세한 근거는 `boundary_policy.md`에 있다.

## 5. 전환 감시

경계 표시와 다른 질문이다. 인접 국면 확률이 여러 주에 걸쳐 한 방향으로 밀리는지를 본다.

    지속:   최근 4주 중 {MINIMUM_RISING_WEEKS}주 이상 상승 AND 4주 변화 >= {SUSTAINED_FOUR_WEEK_RISE}
    단일:   1주 변화 >= {OVERWHELMING_ONE_WEEK_RISE}

두 임계값은 각각 인접 국면 확률 변화 분포의 p90·p99다. 계산은 그 시점까지의 자료만
쓰고, 국면이 유지된 구간 안에서만 차분한다.

## 6. 확신도

세 단계와 이유 코드뿐이다. **보정된 확률이 아니다.** 규칙은 `confidence_policy.md`에 있다.

## 7. 경제영역 진단

다섯 영역(고용·소득·생산·소비/판매·청구)의 방향·기여도·점유율·최근 변화·찬반·자료
신선도·결측을 낸다. 찬반은 대국면이 함의하는 좌표 사분면 부호로 정의한다.

**이것은 경제영역 폭이지 산업 폭이 아니다.** 스키마의 이름(`economic_domain_breadth`)이
그 구분을 담고 있고, 테스트가 두 이름이 섞이지 않는지 확인한다.

## 8. 산업 자료 가용성

필요한 {len(REQUIRED_INDUSTRY_SERIES)}개 차원 중 저장소에서 확인된 것은 **{available}개**다.
따라서 산업 폭은 `not_available`, 집중도는 `not_measured`로 낸다. 총량 지표에서 산업
상태를 추론하지 않는다. 차원별 내역은 `industry_data_availability.csv`에 있다.

## 9. 현재 미국 진단

{diagnosis["as_of_date"]} 기준 공식 국면은 **{diagnosis["official_detailed_phase"]}**,
침체 여부 **{diagnosis["recession_status"]}**, 확신도 **{diagnosis["confidence_level"]}**이다.
전문은 `current_us_diagnosis.md`와 `.json`에 있다.

## 10. 한국·중국

인과 모델이 없으므로 `not_implemented`이며 현재 국면을 만들지 않았다. 필요한 자료와
확인 사항은 `country_extension_requirements.md`에 있다.

## 11. 투자 판단 없음

이 층은 종목·섹터·비중·목표가·매매 문구를 만들지 않는다. 금지 토큰 목록을 두고
출력 전체(중첩 포함)를 훑어 검사하며, 어기면 예외로 거부한다.

## 12. 사실·해석·없는 정보

- **검증된 모델 출력**: 공식 국면, 12개 확률, X·Y·반지름, 상태. 패리티 해시로 굳혔다.
- **진단 해석**: 경계 표시, 전환 감시, 확신도, 영역 찬반. 전부 표시 전용이며 모델을
  바꾸지 않는다.
- **없는 정보**: 산업 폭·집중도, 한국·중국의 현재 국면.
- **알려진 한계**: 단계 A-5에서 확인된 실시간 침체 탐지 실패는 그대로 유효하다.
  이 층은 그 판정을 다시 해석하지 않는다.
"""


def build(settings: Settings | None = None) -> InterpretationResult:
    """해석층 산출물을 만들고 패리티를 확인한다."""

    base = settings or load_settings()
    output = base.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)

    core, source = load_core_observations(base)
    frozen = load_baseline(FROZEN_BASELINE, base)
    evaluation = _evaluate(FROZEN_BASELINE, frozen, core, source, START, END)
    run = evaluation.backtest.run

    # ── 패리티 ──────────────────────────────────────────────────────────────
    frame = parity.core_frame(evaluation.history, str(run.result.status))
    parity_result = parity.compare(frame, output / "historical_parity_check.csv")

    robustness = base.root / "outputs" / "robustness_validation"
    artifacts = parity.artifact_hashes(
        [
            robustness / "phase6" / "frozen_model_config.yaml",
            robustness / "phase6" / "frozen_model_config.sha256",
            robustness / "phase6" / "validation_summary.json",
            robustness / "phase7" / "realtime_path.csv",
            robustness / "phase7" / "validation_summary.json",
            robustness / "phase8" / "validation_summary.json",
            robustness / "phase8" / "alfred" / "realtime_path.csv",
        ],
        robustness,
    )

    # ── 진단 ────────────────────────────────────────────────────────────────
    audit = boundary_audit(evaluation.history)
    summary_boundary = boundary_summary(audit)
    industry = availability_audit(base.root / "data" / "cache", list(base.indicators["indicators"]))
    industry.to_csv(output / "industry_data_availability.csv", index=False)

    us = diagnose(run, base, "US")
    _write_json(output / "current_us_diagnosis.json", us)
    (output / "current_us_diagnosis.md").write_text(
        render_markdown(us), encoding="utf-8", newline="\n"
    )

    _write_json(output / "interpretation_contract.json", _contract_document())
    _write_json(output / "domain_diagnostic_schema.json", _domain_schema())
    (output / "boundary_policy.md").write_text(
        _boundary_policy(summary_boundary), encoding="utf-8", newline="\n"
    )
    (output / "confidence_policy.md").write_text(
        _confidence_policy(), encoding="utf-8", newline="\n"
    )
    (output / "country_extension_requirements.md").write_text(
        _country_requirements(), encoding="utf-8", newline="\n"
    )

    report = parity.parity_report(parity_result, artifacts)
    summary: dict[str, Any] = {
        **report,
        "layer": "phase_interpretation",
        "model_baseline": FROZEN_BASELINE,
        "model_modified": False,
        "boundary_policy": summary_boundary,
        "transition_policy": {
            "sustained_four_week_rise": SUSTAINED_FOUR_WEEK_RISE,
            "minimum_rising_weeks": MINIMUM_RISING_WEEKS,
            "overwhelming_one_week_rise": OVERWHELMING_ONE_WEEK_RISE,
        },
        "confidence_policy": {"weak_agreement": WEAK_AGREEMENT, "calibrated": False},
        "industry_dimensions_available": int((industry["status"] == "available").sum()),
        "industry_dimensions_required": int(len(industry)),
        "countries": {
            code: (
                {"status": "implemented", "as_of_date": us["as_of_date"]}
                if code == "US"
                else not_implemented_payload(code)
            )
            for code in ("US", "KR", "CN")
        },
        "current_us": {
            "as_of_date": us["as_of_date"],
            "official_broad_phase": us["official_broad_phase"],
            "official_detailed_phase": us["official_detailed_phase"],
            "recession_status": us["recession_status"],
            "confidence_level": us["confidence_level"],
            "boundary_flag": us["boundary_flag"],
            "transition_watch": us["transition_watch"],
        },
        "produces_investment_recommendation": False,
    }
    _write_json(output / "validation_summary.json", summary)
    (output / "validation_report.md").write_text(
        _validation_report(summary, summary_boundary, us, industry),
        encoding="utf-8",
        newline="\n",
    )
    return InterpretationResult(
        output_dir=output,
        parity_matches=parity_result.matches,
        country_diagnosed="US",
        passed=parity_result.matches,
    )


def main() -> int:
    result = build()
    print(f"해석층 산출물: {result.output_dir}")
    print(f"핵심 모델 패리티 일치: {result.parity_matches}")
    if not result.parity_matches:
        print("패리티 불일치 — 모델을 고치지 말고 원인을 먼저 찾아야 합니다.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
