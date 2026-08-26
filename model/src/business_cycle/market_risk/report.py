"""보고서. 기간 스프레드 대조가 결론 바로 다음에 온다.

세 단계를 순서대로 놓되, 스프레드가 결정적이므로 그것을 뒤에 숨기지 않는다. 앞의 두
단계가 통과해도 스프레드에서 막히면 결론은 부정이고, 독자가 그 구조를 먼저 알아야
앞의 숫자를 과대평가하지 않는다.
"""

from __future__ import annotations

from typing import Any

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}%}"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value}"


def build_report(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    separation = payload["separation_gate"]
    robustness = payload["robustness_gate"]
    control = payload["term_spread"]
    spread_gate = payload["term_spread_gate"]
    horizon = payload["prespecified_rule"]["decision_horizon_weeks"]

    lines = [
        "# 시계열 질문 — 국면이 시장 수준의 위험을 가르는가",
        "",
        "지금까지의 검정은 전부 횡단면이었다. 어느 업종이 앞서는가. 트랙 23이 그 질문을 "
        "닫았다. 이 단계가 묻는 것은 **무엇을 들지가 아니라 얼마나 들지**이고, 약한 판본이 "
        "아니라 다른 질문이다.",
        "",
        "## 결론",
        "",
        verdict["statement"],
        "",
        "## 0 — 결정 구조를 먼저 밝힌다",
        "",
        "사전 명세는 세 단계를 두되 **기간 스프레드를 결정적으로** 두었다. 앞의 두 단계가 "
        "통과해도 스프레드에서 막히면 결론은 부정이다. 기간 스프레드는 그 자체로 잘 알려진 "
        "위험 신호이고 매일 공짜로 받는 계열 하나이기 때문이다.",
        "",
        f"- 1단계 분리: **{'통과' if separation['passes'] else '실패'}**",
        f"- 2단계 강건성: **{'통과' if robustness['passes'] else '실패'}**",
        f"- 3단계 기간 스프레드(결정적): **{'통과' if spread_gate['passes'] else '실패'}**",
        "",
        f"판정 표본은 `{payload['prespecified_rule']['decision_sample']}`, 판정 지평선은 "
        f"{horizon}주다. 표본은 {payload['first_week']}~{payload['last_week']}, "
        f"{payload['weeks']}주.",
        "",
        "## 1 — 국면별 시장 위험, 동시점",
        "",
        "그 국면이던 주들은 어땠는가. **국면을 서술하는** 숫자이지 결정이 쓸 수 있었던 "
        "것이 아니다.",
        "",
        "| 국면 | 주 | 에피소드 | 주평균 | 연변동성 | 연하방변동성 | -3% 주 빈도 "
        "| 국면 내 낙폭 | 국면에서 시작한 낙폭 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["contemporaneous"]:
        lines.append(
            f"| {PHASE_LABEL[row['phase']]} | {row['weeks']} | {row['episodes']} | "
            f"{_pct(row['mean_weekly'], 3)} | {_pct(row['annualised_volatility'], 1)} | "
            f"{_pct(row['annualised_downside_volatility'], 1)} | "
            f"{_pct(row['large_negative_week_frequency'], 1)} | "
            f"{_pct(row['max_drawdown_within_phase'], 1)} | "
            f"{_pct(row['worst_drawdown_starting_in_phase'], 1)} |"
        )
    lines += [
        "",
        "국면 내 낙폭은 구간을 잘라 재므로 구간이 짧으면 작게 나온다. 국면에서 시작한 "
        "낙폭은 그 안의 어느 주에서 고점을 찍고 **구간 밖까지** 52주를 따라간 값이다. "
        "침체기가 짧게 잡히는 모델에서는 뒤의 것이 실제 위험에 가깝다.",
        "",
        "## 2 — 전방, 결정이 쓸 수 있었던 것",
        "",
        f"국면이라 부른 날부터 앞으로 h주. 판정은 {horizon}주에서 한다.",
        "",
        "| 지평선 | 국면 | 관측 | 에피소드 | 평균 | 중앙 | 연하방변동성 | 음수 비율 "
        "| 5분위 | 최악 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, rows in payload["forward"].items():
        for row in rows:
            mark = "**" if int(key) == horizon else ""
            lines.append(
                f"| {key}주 | {mark}{PHASE_LABEL[row['phase']]}{mark} | {row['observations']} | "
                f"{row['episodes']} | {_pct(row['mean_forward'], 2)} | "
                f"{_pct(row['median_forward'], 2)} | "
                f"{_pct(row['annualised_downside_volatility'], 1)} | "
                f"{_pct(row['share_negative'], 0)} | {_pct(row['fifth_percentile'], 1)} | "
                f"{_pct(row['worst_forward'], 1)} |"
            )

    lines += [
        "",
        "**에피소드 수를 주 수와 함께 읽어야 한다.** 전방 창이 겹치므로 관측 수는 독립 "
        "표본 크기가 아니다. 침체기 관측이 수백 개라도 그것은 에피소드 5개에서 나온 것이다.",
        "",
        "### 하방변동성 비 — 판정 통계량",
        "",
        "| 지평선 | 가장 위험 | 하방변동성 | 에피소드 | 가장 안전 | 하방변동성 | 에피소드 | 비 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, entry in payload["downside_ratio_by_horizon"].items():
        mark = "**" if int(key) == horizon else ""
        lines.append(
            f"| {key}주 | {PHASE_LABEL.get(str(entry['riskiest']), '—')} | "
            f"{_pct(entry.get('riskiest_downside'), 1)} | {_num(entry.get('riskiest_episodes'))} | "
            f"{PHASE_LABEL.get(str(entry['safest']), '—')} | "
            f"{_pct(entry.get('safest_downside'), 1)} | {_num(entry.get('safest_episodes'))} | "
            f"{mark}{_num(entry['ratio'])}{mark} |"
        )

    null = payload["random_label_null"]
    lines += [
        "",
        "### 분포가 실제로 갈리는가 — 평균만으로는 알 수 없다",
        "",
        "| 지평선 | 겹침 계수 | 안전 국면이 위험 국면을 넘을 확률 | 관측 |",
        "|---|---|---|---|",
    ]
    for row in payload["overlap_by_horizon"]:
        if row.get("overlap_coefficient") is None:
            continue
        mark = "**" if row["horizon_weeks"] == horizon else ""
        lines.append(
            f"| {row['horizon_weeks']}주 | {mark}{row['overlap_coefficient']}{mark} | "
            f"{row['probability_second_exceeds_first']} | "
            f"{row['observations'][0]} vs {row['observations'][1]} |"
        )
    lines += [
        "",
        f"무작위 라벨 귀무분포: 관측 비 {_num(null['observed_ratio'])}, 귀무 중앙값 "
        f"{_num(null['null_median'])}, 90분위 {_num(null['null_p90'])}, "
        f"**p={_num(null['p_value'])}** (이동 {null['shifts_used']}회).",
        "",
        "사전 명세 1단계 대조:",
        "",
    ]
    labels = {
        "downside_volatility_ratio_is_large_enough": (
            f"하방변동성 비 >= {separation['downside_ratio_floor']}"
        ),
        "the_two_distributions_do_not_simply_overlap": (
            f"분포 겹침 <= {separation['overlap_ceiling']}"
        ),
        "beats_the_random_label_null": "무작위 라벨 귀무분포에서 p <= 0.05",
    }
    for name, ok in separation["conditions"].items():
        lines.append(f"- {'✅' if ok else '❌'} {labels[name]}")

    if not separation["conditions"]["beats_the_random_label_null"]:
        lines += [
            "",
            f"**비 {_num(separation['downside_ratio'])}는 커 보이지만 증거가 아니다.** "
            f"라벨을 통째로 이동시켜도 귀무 중앙값이 {_num(null['null_median'])}이고 "
            f"90분위가 {_num(null['null_p90'])}다. 네 집단의 하방변동성 가운데 최대와 "
            "최소를 골라 나누는 통계량은 **극값을 두 번 고르므로** 아무 지속적 라벨링에서도 "
            "이만큼 벌어진다. 국면이 실제로 무엇을 가르는지는 이 숫자가 답하지 못한다.",
            "",
            "**이것은 내가 고른 통계량의 한계다. 그대로 적는다.** 사전 명세가 판정을 이 "
            "최대/최소 비에 걸었고, 아래 4절의 회귀는 네 국면을 **함께** 쓰고 모든 관측을 "
            "쓰므로 같은 물음에 훨씬 강한 검정이다. 그럼에도 판정은 사전 명세대로 간다 — "
            "결과를 보고 통계량을 바꾸면 사전 명세가 있으나 마나이기 때문이다.",
        ]

    leave = payload["leave_one_episode_out"]
    lines += [
        "",
        "## 3 — 에피소드 제외, 두 강도",
        "",
        leave["why"],
        "",
        f"에피소드 {leave['episodes']}개("
        + ", ".join(
            f"{PHASE_LABEL[name]} {count}" for name, count in leave["episodes_by_phase"].items()
        )
        + f"). 전체 표본 비는 {_num(leave['full_sample_ratio'])}다.",
        "",
        "| 강도 | 계산된 에피소드 | 최저 | 중앙 | 최고 | 가장 큰 타격 |",
        "|---|---|---|---|---|---|",
    ]
    for strength, label in (("block_only", "블록만"), ("event_including", "사건 포함")):
        entry = leave[f"{strength}_summary"]
        lines.append(
            f"| {label} | {entry['computable_episodes']} | {_num(entry['lowest'])} | "
            f"{_num(entry.get('median'))} | {_num(entry.get('highest'))} | "
            f"{entry.get('most_damaging_episode', '—')} |"
        )
    lines += [
        "",
        f"2020년 제외 시 비는 {_num(payload['ex_covid_ratio']['ratio'])}, "
        f"2008~09년 제외 시 {_num(payload['ex_gfc_ratio']['ratio'])}다. "
        "뒤의 것은 대조군이다 — 2020년만 빼는 것이 자의적이지 않은지 본다.",
        "",
        "사전 명세 2단계 대조:",
        "",
    ]
    robust_labels = {
        "block_only_stays_above_the_floor": f"블록만 제외에서 비가 {robustness['floor']} 위",
        "event_including_stays_above_the_floor": (
            f"사건 포함 제외에서 비가 {robustness['floor']} 위"
        ),
        "survives_removing_2020": "2020년을 빼도 1.0 초과분의 절반이 남는다",
    }
    for name, ok in robustness["conditions"].items():
        lines.append(f"- {'✅' if ok else '❌'} {robust_labels[name]}")

    returns = control["returns"]
    variance = control["variance"]
    lines += [
        "",
        "## 4 — 기간 스프레드 대조 (결정적)",
        "",
        "기간 스프레드(10년-3개월)는 그 자체로 잘 알려진 위험 신호다. 국면이 그 위에 "
        "무언가를 얹지 못하면, 이 용도에서도 계열 하나가 모델을 대신한다.",
        "",
        "설명변수를 더하면 R²는 **반드시** 오르므로, 증분이 우연보다 큰지를 라벨 이동 "
        "귀무분포로 잰다.",
        "",
        "| 대상 | 스프레드 단독 | 국면 단독 | 둘 다 | 국면 증분 | 귀무 90분위 | p |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry, label in ((returns, f"전방 {horizon}주 수익"), (variance, "전방 수익 제곱(분산)")):
        if not entry.get("usable"):
            lines.append(f"| {label} | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {label} | {entry['spread_only_r_squared']} | {entry['phase_only_r_squared']} | "
            f"{entry['both_r_squared']} | **{entry['incremental_r_squared_of_phase']}** | "
            f"{entry['null_p90_increment']} | **{entry['null_p']}** |"
        )
    lines += [
        "",
        control["reading"],
        "",
        "### 2020년 하나에 얹혀 있는가",
        "",
        "트랙 17의 표제가 그 한 해에서 나왔다. 같은 검사를 여기에도 건다.",
        "",
        "| 표본 | 대상 | 국면 증분 | p |",
        "|---|---|---|---|",
    ]
    for name, label in (("ex_covid", "2020년 제외"), ("ex_gfc", "2008~09년 제외")):
        entry = payload["term_spread_without_episode"].get(name, {})
        for key, what in (("returns", "수익"), ("variance", "분산")):
            block = entry.get(key, {})
            if not block.get("usable"):
                continue
            lines.append(
                f"| {label} | {what} | {block['incremental_r_squared_of_phase']} | "
                f"{block['null_p']} |"
            )
    lines += [
        "",
        f"반대 방향도 적어 둔다 — 국면 위에 스프레드를 얹으면 수익 회귀에서 "
        f"{_num(returns.get('incremental_r_squared_of_spread_over_phase'))}가 더해진다. "
        "두 증분의 크기를 견주면 어느 쪽이 어느 쪽을 포함하는지가 보인다.",
        "",
        spread_gate["verdict"],
        "",
    ]

    other = payload["other_labellings"]
    lines += [
        "## 5 — 다른 라벨링 (보고만, 판정하지 않음)",
        "",
        "| 라벨링 | 주 | 가장 위험 | 가장 안전 | 비 | 침체 에피소드 |",
        "|---|---|---|---|---|---|",
    ]
    for name, entry in other.items():
        ratio = entry["downside_ratio"]
        lines.append(
            f"| {name} | {entry['weeks']} | "
            f"{PHASE_LABEL.get(str(ratio['riskiest']), '—')} | "
            f"{PHASE_LABEL.get(str(ratio['safest']), '—')} | {_num(ratio['ratio'])} | "
            f"{entry['episodes']['contraction']} |"
        )
    lines += [
        "",
        "실시간 창은 침체 에피소드가 2020년 하나뿐이라 판정에서 뺐다. 그 창의 숫자가 좋아도 "
        "그것은 한 사건의 성질이다.",
        "",
    ]

    lines += _closing(payload)
    return "\n".join(lines)


def _closing(payload: dict[str, Any]) -> list[str]:
    verdict = payload["verdict"]
    rule = payload["prespecified_rule"]
    lines = [
        "## 사전 명세가 무엇을 정해 두었는가",
        "",
        "규칙은 결과보다 먼저 커밋됐고 시험이 그 순서를 강제한다.",
        "",
        f"- 통과: {rule['what_counts_as_usable']}",
        f"- 실패: {rule['what_counts_as_failure']}",
        "",
        "**문턱을 순환매에서 가져오지 않은 이유**: "
        + rule["why_the_threshold_differs_from_the_rotation_gate"],
        "",
    ]
    if verdict["usable_for_exposure"]:
        lines += [
            "## 무엇이 남았는가",
            "",
            "국면이 노출 조절에 쓸 정보를 담고 있다는 결과다. 다음은 그 정보를 규칙으로 "
            "옮기는 일이고, 그것은 이 단계의 범위가 아니다. 규칙을 만들 때는 이 단계가 "
            "쓰지 않은 것 — 비용, 세금, 실행 지연 — 이 다시 문제가 된다.",
            "",
        ]
    else:
        lines += [
            "## 이 결과가 무엇을 확정하는가",
            "",
            "**이 모델의 쓸모는 서술과 상태 인식으로 확정된다.** 물러선 것이 아니라 "
            "정해진 것이다. 세 갈래로 물었고 세 갈래가 같은 곳에서 멈췄다.",
            "",
            "- **횡단면(트랙 17·23)** — 국면이 업종 배열을 조직하는 몫이 3%다. 경계를 "
            "다시 세워도 2.98%에서 3.05%로 움직였다.",
            "- **가치 타이밍(트랙 19·20)** — 라벨 창 안에서 어떤 가치 대리변수도 프리미엄을 "
            "내지 않았고, 기간 스프레드를 넘어서지 못했다.",
            "- **시계열(이 단계)** — "
            + (
                "기간 스프레드를 넘어서지 못한다."
                if not verdict["beats_the_term_spread"]
                else "분리 자체가 사전 조건을 채우지 못한다."
            ),
            "",
            "남는 것은 트랙 22가 확인한 것이다. 후퇴기는 확장기 안의 감속이며 그중 일부가 "
            "침체의 전조이고, NBER 침체 6회 중 4회가 후퇴기를 앞에 두었다 — 필요조건에 "
            "가깝고 충분조건이 아니다. 그것이 이 모델이 하는 일이고, 화면 문구는 거기에 "
            "맞춰야 한다.",
            "",
        ]
        if verdict["phase_adds_on_variance"]:
            lines += [
                "**한 가지 예외를 지우지 않는다.** 수익 회귀에서는 스프레드를 넘지 못했지만 "
                "분산 회귀에서는 넘었다. 수익의 방향이 아니라 흔들림의 크기를 가른다는 "
                "뜻이고, 노출 결정이 실제로 묻는 것이 그쪽이다. 사전 명세가 수익 쪽을 "
                "결정적으로 두었으므로 판정은 부정이지만, 이 관찰은 다음 단계의 후보로 "
                "남을 값이 있다 — 다만 그때는 **분산 쪽을 결정 기준으로 두는 새 사전 "
                "명세**가 먼저 있어야 하고, 지금 결과를 근거로 삼아서는 안 된다.",
                "",
            ]
    lines += [
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return lines
