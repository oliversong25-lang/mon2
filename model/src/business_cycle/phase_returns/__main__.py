"""국면-산업 수익률 검증 실행기.

    python -m business_cycle.phase_returns

동결 v1.1을 하나도 건드리지 않는다. 이미 확정된 주간 국면 경로를 읽기만 한다.
산출물은 ``outputs/phase_returns/``에만 쓴다.

Fama-French 자료는 내부 검증 전용이며 제품에 실리지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..config import load_settings
from ..four_phase.engine import load_config
from . import distribution as D
from . import forward as F
from . import labels as L
from . import latency as LAT
from . import rotation as R
from . import samples as SA
from . import significance as SIG
from .french import INDUSTRIES

OUTPUT_NAME = "phase_returns"

#: 순환매 귀무분포를 만들 때 이동 간격. 촘촘히 해도 거의 같은 정렬이라 정보가 늘지 않는다.
ROTATION_NULL_STRIDE = 4

#: 지연 비교는 688주짜리 창에서 한다. 32년 창의 52주 요건을 그대로 쓰면 거래 주가 너무 줄어든다.
SHORT_WINDOW_MINIMUM_HISTORY = 26


def _analyse(sample: SA.Sample, weekly: pd.DataFrame) -> dict[str, Any]:
    """한 표본의 세 지평선 결과."""

    phase = sample.phase.reindex(sample.weeks)
    out: dict[str, Any] = {"profile": sample.profile(), "horizons": {}}
    for horizon in F.HORIZONS:
        relative = F.forward_relative(weekly, horizon).reindex(sample.weeks)
        test = SIG.shift_test(phase, relative)
        correction = SIG.correct(test["cells"])
        out["horizons"][str(horizon)] = {
            "coverage": F.coverage(relative),
            "cells": D.cells(phase, relative),
            "separability": D.separability(phase, relative),
            "shift_test": test,
            "multiple_comparison": correction,
        }
    return out


def _rotation(sample: SA.Sample, weekly: pd.DataFrame, minimum: int) -> dict[str, Any]:
    phase = sample.phase.reindex(sample.weeks)
    frame = weekly.reindex(sample.weeks)
    result = R.run(phase, frame, minimum=minimum)
    result["null"] = R.shift_null(
        phase, frame, SIG.MINIMUM_SHIFT, minimum=minimum, stride=ROTATION_NULL_STRIDE
    )
    result["sample"] = sample.name
    return result


def _distribution_rows(analysis: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, result in analysis.items():
        for horizon, block in result["horizons"].items():
            for phase, industries in block["cells"].items():
                for industry, summary in industries.items():
                    rows.append(
                        {
                            "sample": name,
                            "horizon_weeks": int(horizon),
                            "phase": phase,
                            "industry": industry,
                            **summary,
                        }
                    )
    return rows


def _cell_rows(analysis: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, result in analysis.items():
        for horizon, block in result["horizons"].items():
            for cell in block["shift_test"]["cells"]:
                rows.append({"sample": name, "horizon_weeks": int(horizon), **cell})
    return rows


def _phase_rows(analysis: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, result in analysis.items():
        for horizon, block in result["horizons"].items():
            for entry in block["shift_test"]["by_phase"]:
                rows.append(
                    {
                        "sample": name,
                        "horizon_weeks": int(horizon),
                        "overall_p_value": block["shift_test"]["taxonomy_dispersion_p_value"],
                        **entry,
                    }
                )
    return rows


def _verdict(analysis: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """결론을 문장이 아니라 조건으로 적는다. 나중에 자료가 바뀌면 그대로 다시 돌려 확인한다."""

    def overall(name: str, horizon: int) -> float | None:
        block = analysis.get(name, {}).get("horizons", {}).get(str(horizon))
        if block is None:
            return None
        return float(block["shift_test"]["taxonomy_dispersion_p_value"])

    long_run = [overall("revised_long", h) for h in F.HORIZONS]
    ex_covid = [overall("revised_long_ex_covid", h) for h in F.HORIZONS]
    survivors = {
        name: {
            str(h): len(analysis[name]["horizons"][str(h)]["multiple_comparison"]["survives_bh"])
            for h in F.HORIZONS
        }
        for name in analysis
    }
    discriminates = all(p is not None and p <= 0.05 for p in long_run)
    survives_covid = all(p is not None and p <= 0.05 for p in ex_covid)
    return {
        "long_history_overall_p_by_horizon": {
            str(h): overall("revised_long", h) for h in F.HORIZONS
        },
        "long_history_ex_covid_overall_p_by_horizon": {
            str(h): overall("revised_long_ex_covid", h) for h in F.HORIZONS
        },
        "cells_surviving_bh_by_sample": survivors,
        "taxonomy_discriminates_industry_returns": discriminates,
        "result_survives_removing_covid": survives_covid,
        "statement": (
            "국면 분류는 산업 상대수익률을 유의하게 가르지 못한다."
            if not discriminates
            else "국면 분류는 산업 상대수익률을 가른다."
        ),
    }


def _report(payload: dict[str, Any]) -> str:
    analysis = payload["analysis"]
    verdict = payload["verdict"]
    phase_rows = payload["phase_rows"]
    rotations = payload["rotation"]

    def phase_block(sample: str, horizon: int, title: str) -> list[str]:
        rows = [r for r in phase_rows if r["sample"] == sample and r["horizon_weeks"] == horizon]
        lines = [
            f"**{title}**",
            "",
            "| 국면 | 주 | 에피소드 | 관측 분산 ÷ 우연 분산 | p |",
            "|---|---|---|---|---|",
        ]
        profile = analysis[sample]["profile"]
        for row in rows:
            ratio = row["ratio_to_null_median"]
            marker = "**" if row["p_value"] is not None and row["p_value"] <= 0.05 else ""
            lines.append(
                f"| {row['phase']} | {row['weeks']} | "
                f"{profile['phase_episodes'][row['phase']]} | "
                f"{ratio if ratio is not None else '—'} | "
                f"{marker}{row['p_value'] if row['p_value'] is not None else '—'}{marker} |"
            )
        return lines

    def rotation_row(entry: dict[str, Any]) -> str:
        rot = entry["rotation"]
        return (
            f"| {entry['sample']} | {rot['annualised_relative_return']:+.4f} | "
            f"{rot['information_ratio']} | "
            f"{entry['equal_weight']['annualised_relative_return']:+.4f} | "
            f"{entry['rotation_full_sample_ceiling']['annualised_relative_return']:+.4f} | "
            f"{entry['null']['null_p90']:+.4f} | {entry['null']['p_value']} |"
        )

    long_profile = analysis["revised_long"]["profile"]
    rt_profile = analysis["real_time_overlap"]["profile"]
    lat = payload["latency"]

    lines = [
        "# 국면 분류는 산업 수익률을 가르는가",
        "",
        "## 결론",
        "",
        f"**{verdict['statement']}**",
        "",
        "긴 역사(1994~2026, 1675주)에서 네 국면이 만드는 산업 상대수익률 분산은 "
        "**라벨을 아무렇게나 밀어 놓았을 때와 구분되지 않는다.**",
        "",
        "| 지평선 | 전체 p (긴 역사) | 2020년 제외 시 |",
        "|---|---|---|",
    ]
    for horizon in F.HORIZONS:
        lines.append(
            f"| {horizon}주 | {verdict['long_history_overall_p_by_horizon'][str(horizon)]} | "
            f"{verdict['long_history_ex_covid_overall_p_by_horizon'][str(horizon)]} |"
        )
    ex_gfc = {
        str(h): analysis["revised_long_ex_gfc"]["horizons"][str(h)]["shift_test"][
            "taxonomy_dispersion_p_value"
        ]
        for h in F.HORIZONS
    }
    lines += [
        "",
        "**2020년 한 해를 빼면 남는 것이 없다.** 세 지평선 모두 p가 0.47 위로 올라간다.",
        "",
        "2008~09년을 대신 빼면 반대로 움직인다 — "
        + " / ".join(f"{h}주 p={value}" for h, value in ex_gfc.items())
        + ". 즉 이 결과는 '침체를 빼면 당연히 약해진다'가 아니다. 세계 금융위기를 통째로",
        "빼도 지표는 오히려 선명해진다. **판별력은 '침체 일반'이 아니라 코로나 한 에피소드에**",
        "**얹혀 있다.**",
        "",
        "그리고 이 모델은 침체탐지가 아니라 경기국면판단을 목적으로 한다. 목적에 해당하는",
        "네 국면 중 **전체 주의 81%를 차지하는 확장·후퇴가 전혀 갈리지 않는다.**",
        "",
        "## 표본을 먼저 밝힌다",
        "",
        "| 표본 | 주 | recovery | expansion | slowdown | contraction | 보류 |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, result in analysis.items():
        weeks = result["profile"]["phase_weeks"]
        episode = result["profile"]["phase_episodes"]
        lines.append(
            f"| `{name}` | {result['profile']['weeks']} | "
            + " | ".join(
                f"{weeks[phase]}주 / {episode[phase]}건"
                for phase in ("recovery", "expansion", "slowdown", "contraction")
            )
            + f" | {weeks['withheld']} |"
        )
    lines += [
        "",
        "주 수가 아니라 **에피소드 수**가 실질 표본이다. 실시간 창의 침체 18주는 한 덩어리라",
        "독립 관측이 18이 아니라 **1**이다. 긴 역사에서도 침체 5건, 회복 4건뿐이다.",
        "",
        "산업 12개 x 국면 4개 = **48칸**을 동시에 본다. 5% 수준에서 우연히 유의한 칸이 "
        "**2.4개** 나온다.",
        "",
        "## 국면별 결과 — 하나의 점수로 합치지 않는다",
        "",
    ]
    lines += phase_block("revised_long", 13, "긴 역사 (1994~2026, 13주 지평선)")
    lines += [
        "",
        "`slowdown`의 비율이 **1보다 작다**. 후퇴기 라벨이 붙은 주의 산업 분산은 "
        "라벨을 무작위로 밀었을 때보다 **작다** — 정보가 없는 정도가 아니라, 산업 차이가",
        "가장 흐릿한 구간에 후퇴기 라벨이 붙는다는 뜻이다.",
        "",
    ]
    lines += phase_block("real_time_overlap", 13, "실시간 창 (2013~2026, 13주 지평선)")
    lines += [
        "",
        "여기서는 침체·회복이 유의해 보인다. 그러나 그 침체는 **18주 한 덩어리**이고 회복은",
        "**34주 한 덩어리**다. 검정이 말하는 것은 '이 모델의 침체 라벨이 유용하다'가 아니라",
        "'2020년 3~4월은 시장 역사에서 특이한 구간이었다'이다.",
        "",
    ]
    lines += phase_block("real_time_ex_covid", 13, "실시간 창에서 2020년 제외 (13주 지평선)")
    lines += [
        "",
        "침체 주가 **0**이 된다. 실시간 창에는 코로나 말고 침체가 없다.",
        "",
        "## 다중비교를 견디는 칸",
        "",
        "| 표본 | 지평선 | 명목 유의 (5%) | BH 통과 | Bonferroni 통과 | 우연 기대치 |",
        "|---|---|---|---|---|---|",
    ]
    for name, result in analysis.items():
        for horizon in F.HORIZONS:
            block = result["horizons"][str(horizon)]["multiple_comparison"]
            lines.append(
                f"| `{name}` | {horizon}주 | "
                f"{block['nominally_significant_at_five_percent']} | "
                f"{len(block['survives_bh'])} | {len(block['survives_bonferroni'])} | "
                f"{block['expected_false_positives_at_five_percent']} |"
            )

    bh = analysis["revised_long"]["horizons"]["13"]["multiple_comparison"]
    lines += [
        "",
        "### 긴 역사 13주에서 살아남은 칸",
        "",
    ]
    if bh["survives_bh"]:
        lines += ["| 국면 | 산업 | 효과 | p |", "|---|---|---|---|"]
        for cell in bh["survives_bh"]:
            lines.append(
                f"| {cell['phase']} | {cell['industry']} | {cell['effect']:+.4f} | {cell['p']} |"
            )
    else:
        lines.append("없다.")
    lines += [
        "",
        "### 명목상 유의했지만 보정을 못 견딘 칸",
        "",
    ]
    if bh["fails_correction_but_nominally_significant"]:
        for cell in bh["fails_correction_but_nominally_significant"]:
            lines.append(f"- {cell['phase']} x {cell['industry']} (p={cell['p']})")
    else:
        lines.append("없다.")

    lines += [
        "",
        "48칸 중 명목 유의가 "
        f"{bh['nominally_significant_at_five_percent']}칸인데 우연 기대치가 2.4칸이다.",
        "우연보다 조금 많은 정도이며, 그 조금이 통계적으로 의미 있는 초과인지를 전체 검정이",
        f"묻고 답한 결과가 p={verdict['long_history_overall_p_by_horizon']['13']}이다.",
        "",
        "## 순환매 — 완벽한 시계라도 이길 것이 있는가",
        "",
        "| 표본 | 순환매 (연) | IR | 동일가중 (연) | 정답을 본 상한 | 우연 90분위 | p |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in rotations:
        lines.append(rotation_row(entry))
    lines += [
        "",
        "모두 시장 대비 상대수익률이다. `우연 90분위`는 같은 라벨 계열을 통째로 밀어 다시",
        "돌렸을 때 나오는 성과의 90분위다.",
        "",
        "32년 표본에서 순환매는 시장을 연 "
        f"{rotations[0]['rotation']['annualised_relative_return']:+.2%} 앞선다. 그런데 라벨을",
        f"무작위로 민 경우의 90분위가 {rotations[0]['null']['null_p90']:+.2%}로 그보다 높다 "
        f"(p={rotations[0]['null']['p_value']}). **우연과 구분되지 않는다.**",
        "",
        "정답을 미리 본 상한조차 연 "
        f"{rotations[0]['rotation_full_sample_ceiling']['annualised_relative_return']:+.2%}다.",
        "국면을 완벽하게 맞혀도 그 이상은 없다는 뜻이며, 실현 가능한 값은 그보다 훨씬 아래다.",
        "",
        "## 인식 지연의 비용",
        "",
        "### 주 단위로는 잴 수 있다",
        "",
        f"- 두 라벨이 어긋난 주: **{lat['disagreement']['weeks_that_disagree']}주 / "
        f"{lat['disagreement']['weeks_compared']}주 "
        f"({lat['disagreement']['share_that_disagree']:.1%})**",
        f"- 수정치가 국면을 바꾼 뒤 실시간이 따라오기까지 중앙 "
        f"**{lat['recognition_delay']['median_delay_weeks']}주**, 평균 "
        f"{lat['recognition_delay']['mean_delay_weeks']}주",
        "",
        "| 국면 | 변화 건수 | 중앙 지연 | 최대 지연 |",
        "|---|---|---|---|",
    ]
    for phase, entry in lat["recognition_delay"]["by_phase"].items():
        median = entry["median_delay_weeks"]
        longest = entry["max_delay_weeks"]
        lines.append(
            f"| {phase} | {entry['changes']} | "
            f"{median if median is not None else '—'}주 | "
            f"{longest if longest is not None else '—'}주 |"
        )

    cost = lat["cost"]
    lines += [
        "",
        "### 수익률로는 잴 수 없다",
        "",
        f"- 수정치 라벨 순환매: 연 {cost['revised_rotation_annualised']:+.2%} "
        f"(우연 대비 p={cost['revised_rotation_p_versus_chance']})",
        f"- 실시간 라벨 순환매: 연 {cost['real_time_rotation_annualised']:+.2%} "
        f"(우연 대비 p={cost['real_time_rotation_p_versus_chance']})",
        f"- 차이(= 지연 비용): **{cost['latency_cost_in_annualised_relative_return']:+.2%}**",
        "",
        f"지연 비용을 잴 수 있는가: "
        f"**{'예' if cost['latency_cost_is_measurable'] else '아니오'}**.",
        "",
        "두 추정치 **어느 쪽도** 라벨을 무작위로 민 경우를 이기지 못한다. 각각이 우연 범위",
        "안인데 그 둘을 뺀 값을 '지연 비용'이라 부를 수는 없다 — 잡음에서 잡음을 뺀 것이다.",
        "",
        "게다가 **부호가 뒤집혀 있다** — 늦게 알았던 실시간 라벨 쪽이 더 나은 성과를 냈다.",
        "지연이 도움이 됐다는 뜻이 아니라, 이 표본에서 두 라벨의 우열을 가릴 수 없다는 뜻이다.",
        "",
        "> 그래서 이 단계는 **선행 신호가 줄여야 할 목표 수치를 넘겨주지 못한다.**",
        "> 목표를 세울 만큼 정밀하게 측정되지 않는다는 것 자체가 결과다.",
        "",
        "판별력(분산 통계량)으로 본 지연 비용도 같은 결론이다.",
        "",
        "| 지평선 | 수정치 | 실시간 | 차이 |",
        "|---|---|---|---|",
    ]
    for horizon, entry in cost["dispersion_by_horizon"].items():
        lines.append(
            f"| {horizon}주 | {entry['revised']:.3e} | {entry['real_time']:.3e} | "
            f"{entry['latency_cost']:+.3e} |"
        )

    lines += [
        "",
        "실시간 쪽이 오히려 크다. 코로나 침체를 실시간 라벨이 더 좁게(18주) 잡아 그 구간의",
        "극단적 수익률이 덜 희석됐기 때문이며, 판별력이 아니라 구간 폭의 부산물이다.",
        "",
        "## 전이 게이트를 걸면 달라지는가",
        "",
    ]
    if "real_time_gated" in analysis:
        lines += ["| 지평선 | 게이트 없음 | 게이트(raw:on) |", "|---|---|---|"]
        for horizon in F.HORIZONS:
            plain = analysis["real_time_overlap"]["horizons"][str(horizon)]["shift_test"]
            gated = analysis["real_time_gated"]["horizons"][str(horizon)]["shift_test"]
            lines.append(
                f"| {horizon}주 | {plain['taxonomy_dispersion_p_value']} | "
                f"{gated['taxonomy_dispersion_p_value']} |"
            )
        gated_rotation = next((r for r in rotations if r["sample"] == "real_time_gated"), None)
        lines += [
            "",
            "전이 72건을 63건으로 줄여도 판별력은 그대로다.",
        ]
        if gated_rotation is not None:
            lines.append(
                "순환매 성과는 오히려 연 "
                f"{gated_rotation['rotation']['annualised_relative_return']:+.2%}로 "
                f"게이트 없는 {rotations[2]['rotation']['annualised_relative_return']:+.2%}보다 "
                "낮다. 둘 다 우연 범위 안이라 우열을 말할 수는 없다."
            )
        lines += [
            "",
            "**전이를 다듬어도 산업 수익률 판별력은 생기지 않는다.** 문제는 경계가 흔들리는",
            "것이 아니라 경계 양쪽이 수익률상 같은 곳이라는 데 있다.",
        ]
    else:
        lines.append("게이트 경로 산출물이 없어 이 대조는 건너뛰었다.")

    lines += [
        "",
        "## 표본 한계",
        "",
        f"- 긴 역사라 해도 침체 **{long_profile['phase_episodes']['contraction']}건**, "
        f"회복 **{long_profile['phase_episodes']['recovery']}건**뿐이다. "
        "1994년 시작은 RRSFS가 1992년부터라는 제약에서 온다.",
        f"- 실시간 창은 침체 **{rt_profile['phase_episodes']['contraction']}건**, "
        f"회복 **{rt_profile['phase_episodes']['recovery']}건**이고 모두 코로나다.",
        "- Fama-French 자료는 2026-06-30까지다. 26주 전방창을 닫으려면 라벨 주가 "
        "2026-01-02 이전이어야 한다.",
        "- 겹치는 전방창 때문에 통상적인 t 검정은 쓸 수 없다. 순환 이동 검정으로 대체했고,",
        "  그 검정의 유효 표본은 주 수가 아니라 **에피소드 수**에 가깝다.",
        "- 12산업으로 고정했다. 49산업은 이미 얇은 표본을 더 쪼갠다.",
        "",
        "## 자료 출처와 사용 범위",
        "",
        "- Fama-French 12 Industry Portfolios (daily, value weighted), Ken French Data Library",
        "- Fama-French Research Data Factors (daily) — 시장 수익률은 `Mkt-RF + RF`",
        "",
        "이 자료는 Fama·French의 저작물이고 CRSP에서 파생된다. **내부 검증에만 썼고 제품에",
        "실리지 않는다.** S&P 섹터 지수와 GICS 분류를 쓰지 않은 것도 같은 이유다 — 지수값이",
        "라이선스 대상이고 GICS 분류 체계 자체가 S&P·MSCI 소유라 직접 지수를 만들어도",
        "회피가 되지 않는다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
        "## 이 결과가 뜻하는 것",
        "",
        "1. **섹터 선택을 위한 국면 판단**이라는 용도는 현재 증거로 뒷받침되지 않는다.",
        "   국면 정확도를 더 올려도 이 결론은 바뀌지 않는다 — 정답을 미리 본 상한 자체가 낮다.",
        "2. 확장·후퇴 경계는 수익률상 존재하지 않는다. 전이 게이트 단계에서 본 채터링은",
        "   모델의 결함이라기보다 **가를 것이 없는 곳에서 가르려 한 결과**로 읽힌다.",
        "3. 남는 용도는 서술이다 — 지금 경제가 어느 국면에 있는지를 설명하는 것. 그것은",
        "   수익률 판별력을 전제하지 않는다.",
        "",
        "음의 결과를 뒤집는 설정을 찾아 나서지 않았다. 12산업·세 지평선·두 라벨링·다중비교",
        "보정을 미리 정해 두고 한 번 돌린 결과가 위의 표다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    config = load_config(settings)
    root = settings.root

    revised = L.load_revised(str(root / L.REVISED_PATH))
    real_time = L.load_real_time(str(root / L.REAL_TIME_PATH))
    overlap_weeks = L.overlap(revised, real_time)

    gated_path = root / SA.GATED_PATH
    gated = SA.load_gated(str(gated_path)) if gated_path.exists() else None

    weekly = F.weekly_returns(revised.weeks, str(root / "data" / "cache" / "famafrench"))

    sample_list = SA.build(revised, real_time, overlap_weeks, gated)
    analysis = {sample.name: _analyse(sample, weekly) for sample in sample_list}

    by_name = {sample.name: sample for sample in sample_list}
    rotations = [
        _rotation(by_name["revised_long"], weekly, R.MINIMUM_PHASE_HISTORY),
        _rotation(by_name["revised_overlap"], weekly, SHORT_WINDOW_MINIMUM_HISTORY),
        _rotation(by_name["real_time_overlap"], weekly, SHORT_WINDOW_MINIMUM_HISTORY),
    ]
    if "real_time_gated" in by_name:
        rotations.append(
            _rotation(by_name["real_time_gated"], weekly, SHORT_WINDOW_MINIMUM_HISTORY)
        )

    revised_rotation = rotations[1]
    real_time_rotation = rotations[2]
    latency_block = {
        "window": {"first": overlap_weeks[0], "last": overlap_weeks[-1]},
        "disagreement": LAT.disagreement(
            revised.phase.reindex(overlap_weeks), real_time.phase.reindex(overlap_weeks)
        ),
        "recognition_delay": LAT.recognition_delay(
            revised.phase.reindex(overlap_weeks), real_time.phase.reindex(overlap_weeks)
        ),
        "cost": LAT.cost(
            revised_rotation,
            real_time_rotation,
            revised_rotation["null"],
            real_time_rotation["null"],
            {
                h: analysis["revised_overlap"]["horizons"][str(h)]["shift_test"][
                    "taxonomy_dispersion"
                ]
                for h in F.HORIZONS
            },
            {
                h: analysis["real_time_overlap"]["horizons"][str(h)]["shift_test"][
                    "taxonomy_dispersion"
                ]
                for h in F.HORIZONS
            },
        ),
    }

    distribution_rows = _distribution_rows(analysis)
    cell_rows = _cell_rows(analysis)

    # 칸 단위 표는 CSV 두 개에 그대로 있다. JSON에까지 실으면 1MB가 넘고, 그러면 아무도
    # 열지 않는 산출물이 된다. JSON에는 국면별 요약과 보정 결과만 남긴다.
    for result in analysis.values():
        for block in result["horizons"].values():
            block.pop("cells", None)
            block["shift_test"].pop("cells", None)

    payload: dict[str, Any] = {
        "stage": "phase_returns_validation",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "industries": list(INDUSTRIES),
        "horizons": list(F.HORIZONS),
        "data_use": (
            "Fama-French 자료는 내부 검증 전용이며 제품에 실리지 않는다. "
            "S&P 섹터 지수와 GICS 분류는 라이선스 문제로 쓰지 않는다."
        ),
        "analysis": analysis,
        "rotation": rotations,
        "latency": latency_block,
        "phase_rows": _phase_rows(analysis),
        "verdict": _verdict(analysis),
    }

    output = root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(distribution_rows).to_csv(output / "forward_distributions.csv", index=False)
    pd.DataFrame(cell_rows).to_csv(output / "cell_significance.csv", index=False)
    pd.DataFrame(payload["phase_rows"]).to_csv(output / "phase_significance.csv", index=False)
    pd.DataFrame(
        [
            {
                "sample": entry["sample"],
                "rotation_annualised": entry["rotation"]["annualised_relative_return"],
                "information_ratio": entry["rotation"]["information_ratio"],
                "equal_weight_annualised": entry["equal_weight"]["annualised_relative_return"],
                "full_sample_ceiling": entry["rotation_full_sample_ceiling"][
                    "annualised_relative_return"
                ],
                "null_median": entry["null"]["null_median"],
                "null_p90": entry["null"]["null_p90"],
                "p_value": entry["null"]["p_value"],
                "average_weekly_turnover": entry["average_weekly_turnover"],
            }
            for entry in rotations
        ]
    ).to_csv(output / "rotation.csv", index=False)
    (output / "phase_returns_report.md").write_text(
        _report(payload), encoding="utf-8", newline="\n"
    )
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(payload["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
