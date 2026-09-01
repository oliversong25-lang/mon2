"""보고서. 천장이 맨 앞이고, 선택 의존성은 결과 안에 들어간다.

"재실행 통과"만 보고 맥락을 못 본 독자는 그 결과를 과대평가한다. 그래서 선택 의존성
문단은 결론 옆에 두고, 결과가 어느 쪽이든 뺀다.
"""

from __future__ import annotations

from typing import Any

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}


def _selection_paragraph(payload: dict[str, Any]) -> list[str]:
    """결과가 어느 쪽이든 반드시 들어간다."""

    positive = bool(payload["verdict"]["usable_for_rotation"])
    lines = [
        "## 선택 의존성 — 결과와 함께 읽어야 한다",
        "",
        "경계 모수 **지속 17주는 트랙 17·22의 판별력 기계로 골랐다.** 여기서 검정하는 "
        "양(순환매 수익성, 국면 분리도)과 그것을 고르는 데 쓴 양은 다른 양이지만 "
        "독립이 아니다. 검증에 실패했고, 모델을 바꿨고, 같은 검증을 다시 돌린 구조 자체가 "
        "결과를 낙관 쪽으로 민다.",
        "",
    ]
    if positive:
        lines += [
            "이번 결과가 긍정이므로 이 문단이 특히 중요하다. **개선분 중 얼마가 진짜 "
            "개선이고 얼마가 선택인지 이 자료만으로는 가를 수 없다.** 가르려면 셋 중 "
            "하나가 필요하다.",
            "",
            "- **표본 밖 구간** — 경계를 고를 때 보지 않은 기간. 확장 역사(1976~1994)가 "
            "후보이지만 소비 도메인 구성이 달라 같은 물건이 아니다.",
            "- **다른 시장** — 한국이나 유럽 산업 포트폴리오. 경계는 미국 자료로만 골랐다.",
            "- **사전 등록된 전향 검정** — 지금부터의 라벨과 수익을 앞으로 모아 보는 것. "
            "가장 깨끗하고 가장 느리다.",
            "",
        ]
    else:
        lines += [
            "이번 결과가 **부정**이라는 사실이 이 위험을 상당 부분 상쇄한다. 선택 편의는 "
            "결과를 통과 쪽으로 밀지 실패 쪽으로 밀지 않는다. 판별력을 올리는 방향으로 "
            "고른 모수 위에서 잰 순환매가 그래도 관문을 넘지 못했다면, 그 부정은 "
            "선택 편의를 **거스르고** 나온 것이다.",
            "",
            "다만 반대 방향의 한계는 남는다. 이 부정이 '국면 모델 일반'이 아니라 '이 일곱 "
            "계열, 이 임계값, 이 미국 산업 분류'에 대한 것이라는 점이다. 그것을 넓히려면 "
            "위와 같은 표본 밖 구간이나 다른 시장이 필요하다.",
            "",
        ]
    return lines


def build_report(payload: dict[str, Any]) -> str:
    ceiling = payload["ceiling"]
    gate = ceiling["gate"]
    after = ceiling["persist17w"]
    before = ceiling["v1_1"]
    verdict = payload["verdict"]
    counts = payload["label_counts"]

    lines = [
        "# 트랙 17 재실행 — persist17w 라벨 위에서",
        "",
        "## 결론",
        "",
        verdict["statement"],
        "",
        "## 1 — 완전예지 천장 (결정적 숫자)",
        "",
        "국면을 **이미 알고** 국면별로 어느 산업이 가장 좋았는지까지 **이미 아는** 전략의 "
        "수익이다. 실현 가능한 어떤 전략도 이 위로 갈 수 없으므로, 이것이 낮으면 국면 "
        "정확도를 아무리 올려도 소용이 없다. 그래서 다른 무엇보다 먼저 잰다.",
        "",
        "| | v1.1 | persist17w | 관문 |",
        "|---|---|---|---|",
        f"| 국면 순위 천장(연율) | {before['ranking_ceiling']['annualised_relative_return']:.2%} | "
        f"**{after['ranking_ceiling']['annualised_relative_return']:.2%}** | "
        f"{gate['floor_annual']:.0%} |",
        f"| 정보비율 | {before['ranking_ceiling']['information_ratio']} | "
        f"**{after['ranking_ceiling']['information_ratio']}** | "
        f"{gate['floor_information_ratio']:.1f} |",
        f"| 최악 낙폭 | {before['ranking_ceiling']['worst_drawdown_versus_market']:.1%} | "
        f"{after['ranking_ceiling']['worst_drawdown_versus_market']:.1%} | — |",
        "",
        ceiling["reading"],
        "",
        "### 낮은 천장이 무엇 때문인가",
        "",
        "천장이 낮은 이유는 둘 중 하나다. 산업 간 차이가 애초에 작거나, 차이는 있는데 "
        "국면이 그것을 잡지 못하거나. 처방이 다르므로 갈라야 한다. 매주 그 주의 최고 "
        "3개를 고르는 **주간 신탁**이 그 상한이다.",
        "",
        "| | 연율 | 정보비율 |",
        "|---|---|---|",
        f"| 주간 신탁(국면을 아예 쓰지 않음) | "
        f"{after['oracle_ceiling']['annualised_relative_return']:.1%} | "
        f"{after['oracle_ceiling']['information_ratio']} |",
        f"| 국면 순위 천장 | {after['ranking_ceiling']['annualised_relative_return']:.2%} | "
        f"{after['ranking_ceiling']['information_ratio']} |",
        f"| **국면이 조직하는 몫** | **{after['phase_share_of_the_oracle']:.1%}** | — |",
        "",
        "산업 간 차이는 크다 — 매주 옳게 고르면 연 165%다. 그런데 국면으로 조직되는 몫은 "
        f"**{after['phase_share_of_the_oracle']:.1%}**뿐이다. 문제는 자료가 아니라 국면이 "
        "산업 수익의 배열을 거의 설명하지 못한다는 것이다. 이 진단은 경계를 고쳐도 "
        f"움직이지 않았다(v1.1 {before['phase_share_of_the_oracle']:.1%} → "
        f"persist17w {after['phase_share_of_the_oracle']:.1%}).",
        "",
        "### 라벨이 실제로 얼마나 바뀌었는가",
        "",
        "천장이 안 움직인 것이 라벨이 안 바뀌어서인지 확인해 둔다. 크게 바뀌었다.",
        "",
        "| 국면 | v1.1 주 | persist17w 주 | v1.1 에피소드 | persist17w 에피소드 |",
        "|---|---|---|---|---|",
    ]
    for name in ("recovery", "expansion", "slowdown", "contraction"):
        lines.append(
            f"| {PHASE_LABEL[name]} | {counts['v1_1']['weeks'][name]} | "
            f"{counts['persist17w']['weeks'][name]} | "
            f"{counts['v1_1']['episodes'][name]} | {counts['persist17w']['episodes'][name]} |"
        )
    lines += [
        "",
        "후퇴기가 677주 44에피소드에서 87주 11에피소드로 줄고 그 주들이 확장기로 갔다. "
        "라벨은 분명히 달라졌는데 천장은 움직이지 않았다 — **어느 주를 어느 국면이라 "
        "부르든 국면별 최고 산업의 수익 차이가 거기서 거기다.**",
        "",
    ]

    if payload["ceiling_gate_passes"]:
        lines += ["## 2 — 순환매", ""]
    else:
        lines += [
            "## 2 — 순환매 (관문이 막혔으므로 숫자만)",
            "",
            "사전 명세는 천장 관문이 아래 단계를 막게 했다. 그래서 여기 숫자에는 결론을 "
            "달지 않는다 — 천장 아래에서 무엇이 나오든 그것은 천장을 넘을 수 없다. "
            "**기록으로만 남긴다.**",
            "",
        ]

    lines += [
        "| 표본 | 라벨 | 순환매 | 동일가중 | 초과 | 귀무 p | 귀무 90분위 |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, label in (("v1_1", "v1.1"), ("persist17w", "persist17w")):
        for row in payload["rotation"][key]:
            lines.append(
                f"| {row['sample']} | {label} | "
                f"{row['rotation']['annualised_relative_return']:.2%} | "
                f"{row['equal_weight']['annualised_relative_return']:.2%} | "
                f"{row['excess_over_equal_weight']:+.2%} | "
                f"{row['null']['p_value']} | {row['null']['null_p90']:.2%} |"
            )
    ex = payload["rotation"]["ex_covid_contiguous"]
    lines += [
        f"| 2020년 제외 | persist17w | {ex['rotation']['annualised_relative_return']:.2%} | "
        f"{ex['equal_weight']['annualised_relative_return']:.2%} | "
        f"{ex['excess_over_equal_weight']:+.2%} | {ex['null']['p_value']} | "
        f"{ex['null']['null_p90']:.2%} |",
        "",
        "사전 명세의 네 조건 대조:",
        "",
    ]
    condition_label = {
        "beats_equal_weight_by_the_margin": "동일가중을 연 2%p 이상 이긴다",
        "beats_the_random_label_null": "무작위 라벨 귀무분포에서 p<=0.05",
        "survives_removing_2020": "2020년을 빼도 초과수익의 절반이 남는다",
        "survives_leave_one_episode_out": "에피소드 제외에서 부호가 유지된다",
    }
    for name, ok in payload["rotation"]["gate"]["conditions"].items():
        lines.append(f"- {'✅' if ok else '❌'} {condition_label[name]}")

    leave = payload["leave_one_episode_out"]
    lines += [
        "",
        "## 3 — 에피소드 제외, 두 강도",
        "",
        f"{leave['why']}",
        "",
        f"에피소드 {leave['episodes']}개("
        + ", ".join(
            f"{PHASE_LABEL[name]} {count}" for name, count in leave["episodes_by_phase"].items()
        )
        + f"). 전체 표본 초과수익은 {leave['full_sample_excess']:+.2%}다.",
        "",
        "| 강도 | 계산된 에피소드 | 최저 | 중앙 | 최고 | 부호 뒤집힘 | 가장 큰 타격 |",
        "|---|---|---|---|---|---|---|",
    ]
    for strength, label in (
        ("block_only", "블록만 (약함)"),
        ("event_including", "**사건 포함 (판정)**"),
    ):
        entry = leave[f"{strength}_summary"]
        lines.append(
            f"| {label} | {entry['computable_episodes']} | {entry['range_low']:+.2%} | "
            f"{entry['median']:+.2%} | {entry['range_high']:+.2%} | "
            f"{entry['episodes_that_flip_the_sign']} | {entry['most_damaging_episode']} |"
        )

    weak = leave["block_only_summary"]
    strong = leave["event_including_summary"]
    if weak["episodes_that_flip_the_sign"] > strong["episodes_that_flip_the_sign"]:
        lines += [
            "",
            "**방향이 트랙 19와 반대다. 그대로 적는다.** 트랙 19에서는 강한 제외가 더 "
            "가혹했는데 여기서는 강한 제외 쪽이 더 관대하다 — 블록만 빼면 "
            f"{', '.join(weak['which_flip'])}에서 부호가 뒤집히고, 사건 포함으로 빼면 "
            "뒤집히지 않는다. 그 에피소드가 만든 비중이 에피소드 **직후** 몇 주에 손실을 "
            "냈고, 넓게 빼면 그 손실까지 함께 지워지기 때문이다.",
            "",
            "사전 명세는 사건 포함 쪽을 판정 강도로 못박아 두었으므로 이 조건은 통과로 "
            "적는다. 다만 **약한 쪽으로 판정했다면 이 조건도 실패였다**는 사실을 함께 "
            "남긴다. 통과한 유일한 조건이 강도 선택에 걸려 있다는 뜻이고, 그것을 감추면 "
            "'네 조건 중 하나는 통과했다'가 실제보다 튼튼해 보인다.",
        ]

    taxonomy = payload["taxonomy"]
    lines += [
        "",
        "## 4 — 국면 분리도, 트랙 17 숫자와 나란히",
        "",
        '판별력은 **판정하지 않는다.** 사전 명세에 그렇게 적었다 — "분류에 뜻이 있는가"와 '
        '"순환매에 쓸 수 있는가"는 다른 질문이고, 트랙 22가 이미 앞의 것에 답했다. '
        "여기서는 트랙 17의 격자 위에서 어떻게 보이는지만 놓는다.",
        "",
        "### 분류 전체",
        "",
        "| 표본 | 지평선 | v1.1 | persist17w | v1.1 BH 생존칸 | p17 BH 생존칸 |",
        "|---|---|---|---|---|---|",
    ]
    for row in taxonomy:
        lines.append(
            f"| {row['sample']} | {row['horizon_weeks']}주 | "
            f"{row['v1_1_dispersion_ratio']} (p={row['v1_1_p']}) | "
            f"{row['persist17w_dispersion_ratio']} (p={row['persist17w_p']}) | "
            f"{row['v1_1_cells_surviving_bh']}/{row['cells_tested']} | "
            f"{row['persist17w_cells_surviving_bh']}/{row['cells_tested']} |"
        )
    lines += [
        "",
        "생존칸은 4국면 x 12산업 격자에 BH(FDR 5%)를 걸고 남은 칸이다. 우연히 기대되는 "
        f"거짓 양성은 {taxonomy[0]['expected_false_positives']}칸이다.",
        "",
        "### 국면별 — 판정 표본(`revised_long`)",
        "",
        "| 지평선 | 국면 | v1.1 주 | v1.1 비율 | p17 주 | p17 비율 |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload["per_phase"]:
        if row["sample"] != "revised_long":
            continue
        lines.append(
            f"| {row['horizon_weeks']}주 | {PHASE_LABEL[row['phase']]} | "
            f"{row['v1_1_weeks']} | {row['v1_1_ratio']} (p={row['v1_1_p']}) | "
            f"{row['persist17w_weeks']} | {row['persist17w_ratio']} (p={row['persist17w_p']}) |"
        )

    covid = payload["covid_dependence"]
    lines += [
        "",
        "유의칸은 4국면 x 12산업 격자에 BH(FDR 5%)를 걸고 남은 칸이다.",
        "",
        "## 5 — 2020 의존성, 다시",
        "",
        '트랙 17의 핵심 발견은 "판별력이 2020년 하나에 얹혀 있다"였다. 같은 검사를 '
        "persist17w 라벨에 다시 건다. 2008~09년 제외는 대조군이다 — 2020년만 빼는 것이 "
        "자의적이지 않은지 본다.",
        "",
        "| 지평선 | 전체 | 2020 제외 | 남은 비율 | 2008~09 제외 |",
        "|---|---|---|---|---|",
    ]
    for row in covid["rows"]:
        retained = row["retained_without_covid"]
        lines.append(
            f"| {row['horizon_weeks']}주 | {row['full']} (p={row['full_p']}) | "
            f"{row['ex_covid']} (p={row['ex_covid_p']}) | "
            f"{f'{retained:.0%}' if retained is not None else '—'} | "
            f"{row['ex_gfc']} (p={row['ex_gfc_p']}) |"
        )
    lines += [
        "",
        covid["reading"],
        "",
    ]
    lines += _selection_paragraph(payload)
    lines += [
        "## 사전 명세가 무엇을 정해 두었는가",
        "",
        f"규칙은 결과보다 먼저 커밋됐고 시험이 그 순서를 강제한다. 판정 표본은 "
        f"`{payload['prespecified_rule']['decision_sample']}` 하나다.",
        "",
        f"- 통과: {payload['prespecified_rule']['what_counts_as_usable']}",
        f"- 실패: {payload['prespecified_rule']['what_counts_as_failure']}",
        "",
        "## 이 결과가 무엇을 말하고 무엇을 말하지 않는가",
        "",
        "**말하는 것.** 이 국면 모델의 라벨은 미국 12산업 상대수익을 순환매에 쓸 만큼 "
        "가르지 못한다. 경계 결함을 고쳐도 그렇고, 라벨이 크게 달라졌는데도 그렇다. "
        "천장이 관문 아래이므로 이것은 정확도의 문제가 아니라 **관계 자체의 문제**다.",
        "",
        "**말하지 않는 것.** 국면 모델이 쓸모없다는 것은 아니다. 트랙 22는 후퇴기가 "
        "침체를 앞세우는 필요조건에 가깝다는 것을 보였고, 그것은 상태 서술로서의 값이다. "
        "여기서 부정된 것은 **그 라벨로 업종을 갈아타는 것**이지 라벨 자체가 아니다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)
