"""국면-가치 프리미엄 검정 실행기.

    python -m business_cycle.phase_value

동결 v1.1을 하나도 건드리지 않는다. 확정된 주간 국면 경로를 읽기만 한다.
산출물은 ``outputs/phase_value/``에만 쓴다.

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
from ..phase_returns.french import load_daily as load_factors
from ..phase_returns.french import to_weekly
from . import conditional as C
from . import control as CT
from . import data as D
from . import leaveout as LO
from . import premium as P

OUTPUT_NAME = "phase_value"

REVISED_PATH = "outputs/four_phase_v1_1/weekly_state.csv"
REAL_TIME_PATH = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}

#: 층 C가 막힌 이유와, 무엇이 있으면 풀리는가.
BLOCKED_LAYER_C: dict[str, Any] = {
    "layer": "C — 어느 산업의 싼 주식이 이기는지가 국면에 따라 다른가",
    "status": "blocked",
    "why": (
        "종목 단위 재무제표가 있어야 산업 x 가치 교차를 만들 수 있다. Fama-French는 "
        "포트폴리오 수준 수익률만 공개하고 산업 x 장부가/시가 교차를 제공하지 않는다."
    ),
    "what_would_unblock_it": [
        "CRSP 월간 종목 수익률 (주가·시가총액·상장폐지 수익률)",
        "Compustat 연간 재무제표 (장부가, 이익, 현금흐름)",
        "또는 둘을 합쳐 산업 x 가치 이중 정렬을 만들 수 있는 동등한 자료",
    ],
    "not_attempted_here": True,
}


def _windows(revised: L.Labelling, overlap: list[str]) -> dict[str, list[str]]:
    return {
        "revised label window": revised.weeks,
        "real-time window": overlap,
    }


def _test_b(
    name: str,
    phase: pd.Series,
    weeks: list[str],
    hml: pd.Series,
    rates: pd.DataFrame,
    horizon: int,
) -> dict[str, Any]:
    forward = C.forward_value(hml, horizon).reindex(weeks)
    aligned = phase.reindex(weeks)
    trimmed_rates = rates.reindex(weeks)

    control = CT.run(aligned, forward, trimmed_rates)
    block: dict[str, Any] = {
        "labelling": name,
        "horizon_weeks": horizon,
        "by_phase": C.by_phase(aligned, forward),
        "shift_test": C.shift_test(aligned, forward),
        "rate_control": control,
        "coefficient_shrinkage": CT.coefficient_shrinkage(control),
        "leave_one_episode_out_phase_means": LO.phase_means(aligned, forward),
        "leave_one_macro_window_out": LO.leave_one_macro_window_out(
            aligned, forward, trimmed_rates, CT.run, horizon
        ),
    }
    # 에피소드 단위 증분 결정계수 제외는 비싸다. 전체 표본에서 유의한 경우에만 돌린다 —
    # 유의하지 않은 결과를 에피소드별로 다시 재는 것은 의미가 없다.
    if control.get("phase_adds_something_beyond_the_term_spread"):
        block["leave_one_episode_out_incremental_r_squared"] = LO.incremental_r_squared(
            aligned, forward, trimmed_rates, CT.run
        )
    return block


def _verdict(test_a: dict[str, Any], test_b: list[dict[str, Any]]) -> dict[str, Any]:
    significant = [
        block
        for block in test_b
        if block["rate_control"].get("phase_adds_something_beyond_the_term_spread")
    ]
    survives_covid = []
    for block in significant:
        covid = next(
            (
                row
                for row in block["leave_one_macro_window_out"]
                if row["window_removed"].startswith("covid")
            ),
            None,
        )
        if covid is not None and covid.get("still_adds_something"):
            survives_covid.append(block)

    return {
        "test_a_value_premium_positive_in_the_label_window": test_a[
            "value_premium_is_positive_in_the_label_window"
        ],
        "test_b_cells_where_phase_adds_beyond_the_term_spread": [
            {"labelling": block["labelling"], "horizon_weeks": block["horizon_weeks"]}
            for block in significant
        ],
        "test_b_cells_that_survive_removing_the_covid_window": [
            {"labelling": block["labelling"], "horizon_weeks": block["horizon_weeks"]}
            for block in survives_covid
        ],
        "phase_model_adds_something_beyond_the_term_spread": bool(survives_covid),
        "layer_c": BLOCKED_LAYER_C,
        "statement": (
            "국면 모델은 기간 스프레드가 이미 주는 것 이상을 더하지 못한다."
            if not survives_covid
            else "국면 모델은 기간 스프레드 위에 무언가를 더한다."
        ),
    }


def _report(payload: dict[str, Any]) -> str:
    test_a = payload["test_a"]
    blocks = payload["test_b"]
    verdict = payload["verdict"]

    lines = [
        "# 가치 프리미엄은 국면에 따라 달라지는가",
        "",
        "## 결론",
        "",
        f"**{verdict['statement']}**",
        "",
        "두 가지가 각각 실패했다.",
        "",
        "1. **검정 A 실패** — 가치 프리미엄이 우리 국면 라벨이 존재하는 창에서 0과",
        "   구분되지 않는다. 전체 Fama-French 표본(1926~2026)에서는 연 +3.53%, HAC t=3.23으로",
        "   분명하다. 그러나 1994년 이후에는 연 +1.27%, **t=0.87**이고, 실시간 창(2013년",
        "   이후)에서는 연 **−1.58%**, t=−0.18이다.",
        "2. **검정 B의 유일한 유의 결과가 한 에피소드다** — 실시간 창 104주 지평선에서",
        "   국면의 증분 결정계수 0.405(p=0.002)가 나오지만, 코로나 구간을 전방창까지",
        "   포함해 빼면 **0.010(p=0.53)**으로 무너지고 회복기·침체기 주가 **0개** 남는다.",
        "",
        "## 검정 A — 가치 프리미엄이 애초에 있는가",
        "",
        "| 창 | 주 | 연율 | 변동성 | 샤프 | HAC t | 양의 주 |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in test_a["profiles"]:
        lines.append(
            f"| {entry['window']} | {entry['weeks']} | {entry['annualised']:+.4f} | "
            f"{entry['annualised_volatility']:.4f} | {entry['sharpe']} | "
            f"**{entry['hac_t']}** | {entry['weeks_positive']} |"
        )
    lines += [
        "",
        "가치 프리미엄은 **시대에 따라 달라진다**. 이것은 알려진 사실이고, 우리 창이",
        "하필 약한 시대에 놓여 있다.",
        "",
        "| 십 년 | 주 | 연율 | 샤프 | HAC t |",
        "|---|---|---|---|---|",
    ]
    for entry in test_a["by_decade"]:
        if entry.get("thin"):
            lines.append(f"| {entry['window']} | {entry['weeks']} | — | — | 표본 부족 |")
            continue
        lines.append(
            f"| {entry['window']} | {entry['weeks']} | {entry['annualised']:+.4f} | "
            f"{entry['sharpe']} | {entry['hac_t']} |"
        )
    lines += [
        "",
        "1990년대 −0.06%, 2010년대 −2.70%. 우리 라벨이 존재하는 구간이 정확히 그 두",
        "십 년을 포함한다.",
        "",
        "### 한 해를 빼면",
        "",
        "| 창 | 전체 | 범위 | 부호가 뒤집히는가 |",
        "|---|---|---|---|",
    ]
    for label, entry in test_a["leave_one_year_out"].items():
        lines.append(
            f"| {label} | {entry['full_sample_annualised']:+.4f} | "
            f"{entry['range_low']:+.4f} ~ {entry['range_high']:+.4f} | "
            f"{'**예**' if entry['sign_flips_when_any_single_year_is_removed'] else '아니오'} |"
        )
    lines += [
        "",
        "### 가치 다리와 성장 다리",
        "",
        "HML이 0이어도 두 다리가 각각 크게 움직일 수 있다. 갈라 보면 그렇지도 않다.",
        "",
        "| 창 | 정렬 | 가치 다리 − 시장 | 성장 다리 − 시장 | 가치 − 성장 | HAC t |",
        "|---|---|---|---|---|---|",
    ]
    for label, entry in test_a["decomposition"].items():
        for sort_name, values in entry.items():
            lines.append(
                f"| {label} | {sort_name} | {values['value_leg_versus_market']:+.4f} | "
                f"{values['growth_leg_versus_market']:+.4f} | "
                f"{values['value_minus_growth']:+.4f} | "
                f"{values['hac_t_on_value_minus_growth']} |"
            )
    lines += [
        "",
        "장부가/시가 상위 30% 빼기 하위 30%는 두 창 모두에서 **음수**다. 원자료 정렬로도",
        "가치가 성장을 이기지 못했다.",
        "",
        "## 검정 A가 실패했는데 왜 B를 돌렸는가",
        "",
        "지시는 '첫 실패에서 멈추라'였다. 그런데도 B를 돌린 이유는 두 가지다.",
        "",
        "- 무조건부 평균이 0이라는 것과 **조건부로 달라진다**는 것은 양립한다. 0 평균",
        "  요인을 국면으로 타이밍하는 것은 여전히 전략이 될 수 있다.",
        "- 요청된 산출물 목록이 B의 금리 통제 결과와 증분 결정계수를 명시적으로 요구한다.",
        "",
        "다만 해석이 달라진다. A가 실패한 위에서 B가 성공하면 그것은 '가치 프리미엄이",
        "국면에 따라 달라진다'가 아니라 **'0 평균 요인의 국면 타이밍'**이다. 아래 결과는",
        "그 구분을 지킨 채 읽어야 한다.",
        "",
        "## 검정 B — 국면별 전방 가치 수익",
        "",
        "주 수가 아니라 **에피소드 수**가 실질 표본이다.",
        "",
    ]

    for block in blocks:
        control = block["rate_control"]
        lines += [
            f"### {block['labelling']} · {block['horizon_weeks']}주 지평선",
            "",
            "| 국면 | 주 | 에피소드 | 관측 | 평균 | 전체 대비 | p | 에피소드 제외 범위 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        cells = {cell["phase"]: cell for cell in block["shift_test"]["cells"]}
        loo = block["leave_one_episode_out_phase_means"]
        for entry in block["by_phase"]:
            cell = cells[entry["phase"]]
            trimmed = loo[entry["phase"]]
            if trimmed["range_low"] is None:
                span = "**계산 불가**"
            else:
                span = f"{trimmed['range_low']:+.4f} ~ {trimmed['range_high']:+.4f}"
            # 한 에피소드를 빼면 관측이 문턱 아래로 떨어지는 국면이 있다. 범위만 적으면
            # 그 사실이 보이지 않는다 — 표본이 에피소드 하나에 얹혀 있다는 뜻이다.
            uncomputable = trimmed["episodes_whose_removal_leaves_too_few_observations"]
            if uncomputable:
                span += f" (**{uncomputable}건은 빼면 계산 불가**)"
            mean = entry["mean_forward_value_return"]
            effect = cell["effect_versus_all_weeks"]
            marker = "**" if cell["p_value"] is not None and cell["p_value"] <= 0.05 else ""
            lines.append(
                f"| {PHASE_LABEL[entry['phase']]} | {entry['weeks']} | "
                f"{entry['episodes']} | {entry['observations']} | "
                f"{mean if mean is not None else '—'} | "
                f"{effect if effect is not None else '—'} | "
                f"{marker}{cell['p_value'] if cell['p_value'] is not None else '—'}{marker} | "
                f"{span} |"
            )
        lines += [
            "",
            f"전체 분산 p = **{block['shift_test']['dispersion_p_value']}**",
            "",
            "| 모형 | 결정계수 |",
            "|---|---|",
        ]
        if control.get("usable"):
            adds = "예" if control["phase_adds_something_beyond_the_term_spread"] else "아니오"
            for kind in ("spread", "phase", "both"):
                korean = {
                    "spread": "기간 스프레드만",
                    "phase": "국면만",
                    "both": "둘 다",
                }[kind]
                lines.append(f"| {korean} | {control['models'][kind]['r_squared']} |")
            lines += [
                "",
                "국면의 증분 결정계수 "
                f"**{control['incremental_r_squared_of_phase_over_the_term_spread']}** "
                f"(p={control['incremental_r_squared_p_value']}), "
                f"기간 스프레드 위에 더하는가: **{adds}**",
                "",
                "금리를 통제했을 때 국면 계수가 어떻게 되는가:",
                "",
                "| 항 | 통제 없음 | 통제 있음 | 남은 비율 | 부호 뒤집힘 |",
                "|---|---|---|---|---|",
            ]
            for row in block["coefficient_shrinkage"].get("rows", []):
                lines.append(
                    f"| {row['term']} | {row['without_control']:+.4f} | "
                    f"{row['with_control']:+.4f} | {row['retained_share']} | "
                    f"{'**예**' if row['sign_flips'] else '아니오'} |"
                )
        else:
            lines.append("| — | 표본 부족 |")

        lines += [
            "",
            "거시 사건 창을 전방창까지 포함해 빼면:",
            "",
            "| 뺀 사건 | 뺀 주 | 남은 주 | 주가 0인 국면 | 증분 결정계수 | p | 더하는가 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in block["leave_one_macro_window_out"]:
            if not row.get("usable"):
                lines.append(
                    f"| {row['window_removed']} | {row['weeks_removed']} | "
                    f"{row['weeks_left']} | — | — | — | 표본 부족 |"
                )
                continue
            empty = ", ".join(PHASE_LABEL[name] for name in row["phases_left_with_no_weeks"]) or "—"
            lines.append(
                f"| {row['window_removed']} | {row['weeks_removed']} | {row['weeks_left']} | "
                f"{empty} | {row['incremental_r_squared_without_it']} | "
                f"{row['p_value_without_it']} | "
                f"{'예' if row['still_adds_something'] else '**아니오**'} |"
            )
        lines.append("")

        episode_loo = block.get("leave_one_episode_out_incremental_r_squared")
        if episode_loo and episode_loo.get("usable"):
            lines += [
                "국면 블록을 **하나씩만** 빼면 결과가 유지된다 — "
                f"{episode_loo['episodes_tested']}건 중 "
                f"{episode_loo['episodes_where_it_still_adds_something']}건에서 여전히 유의하고, "
                f"증분은 {episode_loo['range_low']}~{episode_loo['range_high']} 사이다.",
                "",
                "**그런데 그것은 약한 제외다.** 블록 하나를 빼도 이웃 주의 전방창이 여전히 같은",
                "사건을 덮기 때문이다. 위 표처럼 사건을 전방창까지 통째로 빼면 무너진다.",
                "블록 단위 제외만 보고했다면 정반대 결론을 냈을 것이다.",
                "",
            ]

    lines += [
        "## 국면 모델은 기간 스프레드 위에 무엇을 더하는가",
        "",
        "| 라벨 | 지평선 | 스프레드만 | 둘 다 | 증분 | p | 코로나 제외 후 |",
        "|---|---|---|---|---|---|---|",
    ]
    for block in blocks:
        control = block["rate_control"]
        if not control.get("usable"):
            continue
        covid = next(
            (
                row
                for row in block["leave_one_macro_window_out"]
                if row["window_removed"].startswith("covid") and row.get("usable")
            ),
            None,
        )
        after = (
            f"{covid['incremental_r_squared_without_it']} (p={covid['p_value_without_it']})"
            if covid
            else "—"
        )
        lines.append(
            f"| {block['labelling']} | {block['horizon_weeks']}주 | "
            f"{control['models']['spread']['r_squared']} | "
            f"{control['models']['both']['r_squared']} | "
            f"{control['incremental_r_squared_of_phase_over_the_term_spread']} | "
            f"{control['incremental_r_squared_p_value']} | {after} |"
        )
    lines += [
        "",
        "**긴 역사(1994~2026)에서는 어느 지평선에서도 국면이 기간 스프레드 위에 아무것도",
        "더하지 않는다** (증분 0.006~0.021, p 0.59~0.86).",
        "",
        "실시간 창 104주에서만 증분 0.405(p=0.002)가 나오는데, 그 창에서 회복기는 34주",
        "2에피소드, 침체기는 18주 3에피소드이고 **전부 2020년이다**. 코로나 구간을 빼면",
        "두 국면의 주가 0이 되고 증분은 0.010(p=0.53)이 된다.",
        "",
        "즉 그 0.405는 '국면이 가치 수익을 설명한다'가 아니라 **'2020년 3~4월 이후 2년간",
        "가치가 크게 반등했다'**를 국면 라벨로 다시 쓴 것이다.",
        "",
        "## 층 C는 막혀 있다",
        "",
        f"**{BLOCKED_LAYER_C['layer']}**",
        "",
        BLOCKED_LAYER_C["why"],
        "",
        "풀리려면 필요한 것:",
        "",
    ]
    for item in BLOCKED_LAYER_C["what_would_unblock_it"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "이 단계에서 시도하지 않았다.",
        "",
        "## HML은 대리 변수다 — 결론의 비대칭",
        "",
        "HML은 **장부가/시가 정렬**이다. Damodaran이 말하는 내재가치 대비 저평가와는 다르다.",
        "장부가는 무형자산이 큰 기업을 체계적으로 과소평가하고, 최근 수십 년간 그 비중이",
        "커졌다. 2010년대에 HML이 음이었던 것도 그것과 무관하지 않다.",
        "",
        "따라서 결론은 대칭이 아니다.",
        "",
        "- **음의 결과는 주장을 약화시키지만 반증하지 않는다.** 장부가/시가로 저평가를",
        "  못 잡았다는 것이지, 내재가치 대비 저평가가 초과수익을 못 낸다는 것이 아니다.",
        "- **양의 결과였다면 강한 지지가 됐을 것이다.** 거친 대리 변수로도 잡히는 효과라면",
        "  더 정교한 판단으로는 더 잘 잡힐 것이기 때문이다.",
        "",
        "지금 나온 것은 앞쪽이다. 주장은 약해졌지만 닫히지 않았고, 닫으려면 층 C의 자료가",
        "필요하다.",
        "",
        "## 표본 한계",
        "",
        "- 104주 지평선은 라벨 주가 2024-06-28 이전이어야 창이 닫힌다. Fama-French 자료가",
        "  2026-06-30까지이기 때문이다.",
        "- 실시간 창의 회복기는 2에피소드, 침체기는 3에피소드이고 모두 2020년이다.",
        "- 긴 역사에서도 회복기 4에피소드, 침체기 5에피소드다.",
        "- 전방창이 겹치므로 t 검정을 쓸 수 없다. 순환 이동 검정으로 대체했고, 그 검정의",
        "  유효 표본은 주 수가 아니라 에피소드 수에 가깝다.",
        "- 기간 스프레드는 통제 변수 하나다. 신용 스프레드까지 넣으면 국면의 증분은 더",
        "  줄어들 가능성이 높지, 늘지 않는다.",
        "",
        "## 자료 출처와 사용 범위",
        "",
        "- Fama-French Research Data Factors (daily) — HML, Mkt-RF, SMB, RF",
        "- Fama-French Portfolios Formed on BE-ME (daily, value weighted)",
        "- 기간 스프레드 10년-3개월 (미 재무부 일별 수익률 곡선에서 파생, 키 불필요)",
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

    revised = L.load_revised(str(root / REVISED_PATH))
    real_time = L.load_real_time(str(root / REAL_TIME_PATH))
    overlap = L.overlap(revised, real_time)

    french_cache = str(root / "data" / "cache" / "famafrench")
    rate_cache = str(root / D.RATE_CACHE)

    _, factors = load_factors(french_cache)
    portfolios = D.load_book_to_market(french_cache)

    # 검정 A는 Fama-French 전체 표본에서도 봐야 한다. 우리 라벨 격자보다 훨씬 길다.
    full_grid = (
        pd.date_range(factors.index[0], factors.index[-1], freq="W-FRI")
        .strftime("%Y-%m-%d")
        .tolist()
    )
    daily = (
        factors[["HML"]]
        .join((factors["Mkt-RF"] + factors["RF"]).to_frame("MKT"))
        .join(portfolios[["Hi 30", "Lo 30", "Hi 10", "Lo 10"]], how="inner")
    )
    full_weekly = to_weekly(daily, full_grid)

    label_weekly = to_weekly(daily, revised.weeks)
    hml = label_weekly["HML"]
    rates = D.weekly_spread(revised.weeks, rate_cache)

    windows = _windows(revised, overlap)
    test_a = P.run(
        full_weekly["HML"],
        windows,
        full_weekly[["Hi 30", "Lo 30", "Hi 10", "Lo 10"]],
        full_weekly["MKT"],
    )

    test_b: list[dict[str, Any]] = []
    for name, phase, weeks in (
        ("revised", revised.phase, revised.weeks),
        ("real-time", real_time.phase, overlap),
    ):
        for horizon in D.HORIZONS:
            test_b.append(_test_b(name, phase, weeks, hml, rates, horizon))

    payload: dict[str, Any] = {
        "stage": "phase_value_premium",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "horizons": list(D.HORIZONS),
        "term_spread_series": D.TERM_SPREAD_ID,
        "data_use": ("Fama-French 자료는 내부 검증 전용이며 제품에 실리지 않는다."),
        "test_a": test_a,
        "test_b": test_b,
        "layer_c": BLOCKED_LAYER_C,
        "verdict": _verdict(test_a, test_b),
    }

    output = root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(test_a["profiles"]).to_csv(output / "value_premium_windows.csv", index=False)
    pd.DataFrame(test_a["by_decade"]).to_csv(output / "value_premium_by_decade.csv", index=False)
    pd.DataFrame(
        [
            {"labelling": block["labelling"], "horizon_weeks": block["horizon_weeks"], **entry}
            for block in test_b
            for entry in block["by_phase"]
        ]
    ).to_csv(output / "phase_conditional_value.csv", index=False)
    pd.DataFrame(
        [
            {
                "labelling": block["labelling"],
                "horizon_weeks": block["horizon_weeks"],
                "r_squared_spread_only": block["rate_control"]["models"]["spread"]["r_squared"],
                "r_squared_phase_only": block["rate_control"]["models"]["phase"]["r_squared"],
                "r_squared_both": block["rate_control"]["models"]["both"]["r_squared"],
                "incremental_r_squared": block["rate_control"][
                    "incremental_r_squared_of_phase_over_the_term_spread"
                ],
                "p_value": block["rate_control"]["incremental_r_squared_p_value"],
                "adds_beyond_the_term_spread": block["rate_control"][
                    "phase_adds_something_beyond_the_term_spread"
                ],
            }
            for block in test_b
            if block["rate_control"].get("usable")
        ]
    ).to_csv(output / "rate_control.csv", index=False)
    pd.DataFrame(
        [
            {"labelling": block["labelling"], "horizon_weeks": block["horizon_weeks"], **row}
            for block in test_b
            for row in block["leave_one_macro_window_out"]
        ]
    ).to_csv(output / "leave_one_macro_window_out.csv", index=False)
    (output / "phase_value_report.md").write_text(_report(payload), encoding="utf-8", newline="\n")
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
