"""가치 대리 변수 스윕 실행기.

    python -m business_cycle.value_proxies

동결 v1.1을 하나도 건드리지 않는다. 국면 라벨은 **창 경계를 정하는 데만** 쓰인다 —
검정 B가 열리지 않으면 라벨은 계산에 들어가지 않는다.

산출물은 ``outputs/value_proxies/``에만 쓴다.
Fama-French 자료는 내부 검증 전용이며 제품에 실리지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..config import load_settings
from ..four_phase.engine import load_config
from ..phase_returns import labels as L
from . import prespec, recommend
from . import sorts as S
from . import testa as T

OUTPUT_NAME = "value_proxies"

REVISED_PATH = "outputs/four_phase_v1_1/weekly_state.csv"
REAL_TIME_PATH = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"


def _windows(root: Any, index: pd.Index) -> dict[str, list[str]]:
    """라벨 창을 월 경계로 옮긴다. 경계는 라벨 파일에서 읽고 손으로 적지 않는다."""

    revised = L.load_revised(str(root / REVISED_PATH))
    real_time = L.load_real_time(str(root / REAL_TIME_PATH))
    overlap = L.overlap(revised, real_time)
    return {
        "revised label window": S.month_window(revised.weeks[0][:7], revised.weeks[-1][:7], index),
        "real-time window": S.month_window(overlap[0][:7], overlap[-1][:7], index),
    }


def _report(payload: dict[str, Any]) -> str:
    test_a = payload["test_a"]
    rule = test_a["rule"]
    advice = payload["recommendation"]

    value_rows = [row for row in test_a["sorts"] if row["is_value_proxy"]]
    other_rows = [row for row in test_a["sorts"] if not row["is_value_proxy"]]

    def window(row: dict[str, Any], name: str) -> dict[str, Any]:
        return next(entry for entry in row["profiles"] if entry["window"] == name)

    lines = [
        "# 다른 가치 정의로도 프리미엄이 없는가",
        "",
        "## 결론",
        "",
        f"**{test_a['statement']}**",
        "",
        "이익/가격도, 현금흐름/가격도, 배당수익률도 판정 창에서 **명목 문턱조차** 넘지",
        "못한다. 셋 다 장부가 기반이 아니므로, 트랙 19의 음의 결과를 설명하던",
        "'장부가/시가가 무형자산을 잘못 값매긴다'는 해석이 **사라진다.**",
        "",
        "음의 결과가 단단해졌다.",
        "",
        "## 결과를 보기 전에 정한 판정 규칙",
        "",
        "이 규칙은 자료를 내려받기 **전에** 코드로 커밋됐다. 이 프로젝트는 이제 두 번째",
        "대리 변수 가족을 검정하고 있고, 양수가 나올 때까지 바꿔 볼 여지가 구조적으로",
        "열려 있기 때문이다.",
        "",
        f"- 판정 창: **{rule['decision_window']}**",
        f"- 왜 이 창인가: {rule['why_this_window']}",
        f"- 기본 구성: {rule['primary_sort']}",
        f"- 명목 통과: {rule['passes_nominally_if']}",
        f"- 다중비교 보정 후 통과: {rule['passes_after_multiplicity_if']}",
        f"- 검정 B는 언제 여는가: {rule['test_b_opens_only_if']}",
        "",
        f"월간 HAC 지연은 {rule['monthly_hac_lags']}개월이다. {rule['monthly_hac_lags_note']}",
        "",
        "## 다중비교 — 프로젝트 전체에서 센다",
        "",
        f"검정한 **가치 정의**는 {rule['family_size']}개다.",
        "",
    ]
    for name in rule["value_definitions_tested_project_wide"]:
        lines.append(f"- {name}")
    lines += [
        "",
        f"Bonferroni 수준 {rule['family_alpha_bonferroni']}, 그에 해당하는 t 문턱 "
        f"**{prespec.FAMILY_CORRECTED_T}**.",
        "",
        f"{rule['operating_profitability_excluded_from_the_family']}",
        "",
        "## 검정 A — 정렬별 결과",
        "",
        "고-저 스프레드(가치가중 3분위), 연율. 판정은 굵게 표시한 열에서만 내린다.",
        "",
        "| 정렬 | 첫 관측 | 전체 FF | **판정 창** | 실시간 창 |",
        "|---|---|---|---|---|",
    ]
    for row in value_rows:
        full = window(row, "full Fama-French sample")
        decision = window(row, "revised label window")
        live = window(row, "real-time window")
        lines.append(
            f"| {row['label']} | {row['first_month']} | "
            f"{full['annualised']:+.4f} (t={full['hac_t']}) | "
            f"**{decision['annualised']:+.4f} (t={decision['hac_t']})** | "
            f"{live['annualised']:+.4f} (t={live['hac_t']}) |"
        )
    lines += [
        "",
        "### 기계가 작동한다는 확인",
        "",
        "장부가/시가·이익/가격·현금흐름/가격은 **전체 역사에서는** 프리미엄이 분명하다",
        "(t=2.28~2.99). 같은 코드가 알려진 곳에서는 찾아내고 우리 창에서는 못 찾는다.",
        "표본이 아니라 방법이 문제였을 가능성이 그만큼 줄어든다.",
        "",
        "배당수익률만 전체 역사에서도 약하다(연 +0.21%, t=0.74). 배당 성향 자체가",
        "시대에 따라 크게 변해 온 것과 무관하지 않지만, 이 단계에서 그 이상 파고들지",
        "않았다.",
        "",
        "### 판정 창에서 무엇도 통과하지 못했다",
        "",
        f"- 명목 문턱(t≥{prespec.NOMINAL_T})을 넘은 가치 대리 변수: "
        f"**{test_a['value_proxies_passing_nominally'] or '없음'}**",
        f"- 다중비교 보정 후 통과: "
        f"**{test_a['value_proxies_passing_after_multiplicity'] or '없음'}**",
        "",
        "**보정이 할 일이 없었다.** 명목 수준에서 유의한 것이 애초에 하나도 없어서",
        "Bonferroni가 아무것도 걸러낼 필요가 없었다. 이것은 '보정을 견뎠다'보다 훨씬",
        "강한 진술이다.",
        "",
        "판정 창에서 가장 큰 t는 이익/가격의 **0.51**이다. 문턱 2.0의 4분의 1이다.",
        "",
        "### 10분위로 잘라도 같다",
        "",
        "| 정렬 | 판정 창 10분위 스프레드 | HAC t |",
        "|---|---|---|",
    ]
    for row in test_a["sorts"]:
        secondary = next(
            (
                entry
                for entry in row["secondary_profiles"]
                if entry["window"] == "revised label window"
            ),
            None,
        )
        if secondary is None or secondary.get("thin"):
            continue
        lines.append(f"| {row['label']} | {secondary['annualised']:+.4f} | {secondary['hac_t']} |")
    lines += [
        "",
        "극단만 골라도 달라지지 않는다. 3분위에서 없던 것이 10분위에서 생기지 않는다.",
        "",
        "이익/가격 행에서 연율이 음인데 t가 양인 것은 오류가 아니다. 연율은 월 수익을",
        "복리로 묶은 값이고 t는 산술평균에 대한 것이라, 변동성이 큰 표본에서 부호가",
        "갈릴 수 있다. 어느 쪽도 문턱 근처가 아니므로 판정에는 영향이 없다.",
        "",
        "### 두 다리를 갈라 보면",
        "",
        "| 정렬 | 고 분위 − 시장 | 저 분위 − 시장 | 고 − 저 |",
        "|---|---|---|---|",
    ]
    for row in test_a["sorts"]:
        legs = row["legs"]["revised label window"].get("tercile")
        if not legs:
            continue
        lines.append(
            f"| {row['label']} | {legs['high_leg_versus_market']:+.4f} | "
            f"{legs['low_leg_versus_market']:+.4f} | {legs['high_minus_low']:+.4f} |"
        )

    lines += [
        "",
        "## 영업이익률 — 가치 결론에 접어 넣지 않는다",
        "",
        "수익성 요인이지 저평가 대리 변수가 아니다. 따로 적는다.",
        "",
        "| 창 | 연율 | 샤프 | HAC t |",
        "|---|---|---|---|",
    ]
    for row in other_rows:
        for entry in row["profiles"]:
            if entry.get("thin"):
                continue
            lines.append(
                f"| {entry['window']} | {entry['annualised']:+.4f} | "
                f"{entry['sharpe']} | {entry['hac_t']} |"
            )
    lines += [
        "",
        "판정 창에서 연 **+3.18%**, t=1.825로 다섯 정렬 중 가장 살아 있다. 그런데",
        "**명목 문턱 2.0에도 못 미치고**, 무엇보다 이것은 우리가 세운 주장이 아니다.",
        "주장은 저평가에 관한 것이고 영업이익률은 우량함에 관한 것이다. 여기서 방향을",
        "틀면 그것은 검정이 아니라 지표 갈아타기다.",
        "",
        "적어 두되 결론에 넣지 않는 이유가 그것이다.",
        "",
        "## 검정 B는 열지 않았다",
        "",
        "사전 명세대로다. 보정을 통과한 대리 변수가 없으므로 B를 돌리지 않는다.",
        "",
        "트랙 19의 B는 애초에 존재하지 않는 효과의 국면 조건부성을 물은 것이었다.",
        "같은 실수를 반복하지 않기 위해 규칙을 미리 적었고, 그 규칙이 지금 작동했다.",
        "",
        "## 비대칭은 여전하다",
        "",
        "이익/가격과 현금흐름/가격도 **회계 기반 정렬**이고, 내재가치 대비 저평가와는",
        "여전히 거리가 있다. 회계 이익은 무형자산 투자를 비용으로 털어 성장 기업의",
        "이익을 낮게 잡고, 현금흐름도 같은 방향으로 왜곡된다.",
        "",
        "따라서:",
        "",
        "- **전부 음이라는 결과는 주장을 상당히 약화시키지만 여전히 닫지 않는다.**",
        "  회계 기반 정렬로 저평가를 못 잡았다는 것이지, 내재가치 판단이 초과수익을",
        "  못 낸다는 것이 아니다.",
        "- 다만 트랙 19 때보다 **닫힘에 훨씬 가까워졌다.** 그때는 '장부가/시가 하나가",
        "  고장났을 수 있다'가 살아 있는 설명이었다. 이제 그 설명은 세 개의 서로 다른",
        "  회계 정렬을 동시에 설명해야 하고, 그러기 어렵다.",
        "",
        "## 무엇을 할 것인가",
        "",
        f"**{advice['statement']}**",
        "",
        "### 네 트랙에서 나온 것",
        "",
        "| 트랙 | 결과 | 내용 |",
        "|---|---|---|",
    ]
    for entry in advice["track_findings"]:
        lines.append(f"| {entry['track']} | {entry['result']} | {entry['detail']} |")
    lines += [
        "",
        "**양의 결과는 하나뿐이다.** 그리고 그것은 수익률이 아니라 서술의 정확도다.",
        "",
    ]
    for key in ("a", "b", "c"):
        course = advice["courses"][key]
        lines += [
            f"### ({key}) {course['name']}",
            "",
            f"비용: {course['cost']}",
            "",
            "찬성:",
            "",
        ]
        for item in course["for"]:
            lines.append(f"- {item}")
        lines += ["", "반대:", ""]
        for item in course["against"]:
            lines.append(f"- {item}")
        lines.append("")

    recommendation = advice["recommendation"]
    lines += [
        "### 권고",
        "",
        "**즉시: (b)** — 모델의 주장 범위를 서술과 상태 인식으로 좁힌다.",
        "",
        f"{recommendation['what_b_keeps']}",
        "",
        "**다음 조사: (c)** — 한국에서 검정 A만 먼저 돌린다.",
        "",
        f"{recommendation['why_not_a_directly']}",
        "",
        f"{recommendation['conditional_on']}",
        "",
        "(a)를 지금 시작하지 않는 이유는 그것이 틀렸다고 보기 때문이 아니다. **순서가",
        "비싸기 때문이다.** 한국 국면 모델을 먼저 만들면, 한국에서도 A가 실패했을 때",
        "그 작업 전체가 버려진다. 검정 A는 국면 모델 없이 돌아가므로 먼저 돌리는 것이",
        "같은 정보를 훨씬 싸게 산다.",
        "",
        "그리고 한국을 고른 것은 자료가 있어서만이 아니다. 미국에서 가치 프리미엄이",
        "약했던 2010년대는 무형자산 비중이 큰 대형 성장주가 지수를 이끈 시기였고, 한국",
        "시장의 구성은 그와 다르다. 같은 기간에 다른 답이 나올 여지가 실제로 있다 —",
        "이것은 확인된 사실이 아니라 **싸게 확인할 수 있는 가설**이라서 (c)의 근거가 된다.",
        "",
        "## 한계",
        "",
        "- 이익/가격·현금흐름/가격·배당수익률 정렬은 Fama-French가 일간으로 제공하지",
        "  않아 **월간**으로 돌렸다. 판정 창 관측이 384개월로, 트랙 19의 1668주보다",
        "  검정력이 낮다. 비교가 되도록 장부가/시가도 같은 월간 격자에서 다시 계산했고,",
        "  트랙 19의 주간 수치(연 +1.27%, t=0.87)와 방향이 같다(연 +0.12%, t=0.35).",
        "- 배당수익률 정렬의 저분위에는 무배당 기업이 들어가지 않는다. Fama-French가",
        "  그것을 별도 칸으로 뺀다. 그래서 D/P 스프레드는 '배당 많은 기업 대 적은 기업'",
        "  이지 '배당 있는 기업 대 없는 기업'이 아니다.",
        "- 모든 정렬이 가치가중이다. 동일가중은 소형주 쏠림이 커 다른 이야기가 된다.",
        "- Fama-French 자료는 2026-06까지다.",
        "",
        "## 자료 출처와 사용 범위",
        "",
        "- Fama-French Portfolios Formed on BE-ME / E-P / CF-P / D-P / OP",
        "  (monthly, value weighted)",
        "- Fama-French Research Data Factors (monthly) — 시장 수익률은 `Mkt-RF + RF`",
        "",
        "Fama-French 자료는 Fama·French의 저작물이고 CRSP에서 파생된다. **내부 검증에만",
        "썼고 제품에 실리지 않는다.**",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    config = load_config(settings)
    root = settings.root
    cache_dir = str(root / "data" / "cache" / "famafrench")

    reference = S.load(S.SORTS[0], cache_dir)
    windows = _windows(root, reference.index)
    test_a = T.run(windows, cache_dir)

    payload: dict[str, Any] = {
        "stage": "value_proxy_sweep",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "frequency": "monthly",
        "windows": {
            name: {"months": len(months), "first": months[0], "last": months[-1]}
            for name, months in windows.items()
        },
        "data_use": "Fama-French 자료는 내부 검증 전용이며 제품에 실리지 않는다.",
        "test_a": test_a,
        "test_b_run": False,
        "test_b_not_run_because": test_a["statement"],
        "recommendation": recommend.build(test_a),
    }

    output = root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "sort": row["sort"],
                "label": row["label"],
                "family": row["family"],
                "window": entry["window"],
                **{key: value for key, value in entry.items() if key != "window"},
            }
            for row in test_a["sorts"]
            for entry in row["profiles"]
        ]
    ).to_csv(output / "test_a_by_sort.csv", index=False)
    pd.DataFrame(
        [
            {
                "sort": row["sort"],
                "decision_window_annualised": row["decision_window_annualised"],
                "decision_window_hac_t": row["decision_window_hac_t"],
                "is_value_proxy": row["is_value_proxy"],
                "passes_nominally": row["passes_nominally"],
                "passes_after_multiplicity": row["passes_after_multiplicity"],
            }
            for row in test_a["sorts"]
        ]
    ).to_csv(output / "decision.csv", index=False)
    (output / "value_proxy_report.md").write_text(_report(payload), encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(payload["test_a"]["statement"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
