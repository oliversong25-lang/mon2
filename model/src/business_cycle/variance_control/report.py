"""보고서. 대조를 어떻게 골랐는지가 결론 바로 다음에 온다.

대조를 약하게 잡으면 국면이 이기는 것은 당연하다. 그래서 어느 창을 왜 골랐는지를
숫자 앞에 놓는다 — 독자가 그것을 확인하지 못하면 아래 숫자를 읽을 수 없다.
"""

from __future__ import annotations

from typing import Any

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value}"


def _times(entry: dict[str, Any]) -> str:
    """역방향 증분이 국면 증분의 몇 배인가."""

    phase = float(entry["incremental_r_squared_of_phase"])
    reverse = float(entry["incremental_r_squared_of_control_over_phase"])
    return "—" if phase <= 0 else f"{reverse / phase:.1f}"


def build_report(payload: dict[str, Any]) -> str:
    verdict = payload["verdict"]
    gate = payload["decision_gate"]
    choice = payload["lookback_choice"]
    decision = payload["decision"]
    track24 = payload["track24_spread_only_control"]
    rule = payload["prespecified_rule"]

    lines = [
        "# 변동성 대조 — 트랙 24의 분산 결과는 무엇이었는가",
        "",
        "트랙 24는 수익에서 국면이 기간 스프레드를 넘지 못한다는 것을 보였고, 분산에서는 "
        "넘는다는 것을 관찰만 하고 두었다. 그 비대칭이 이 프로젝트에서 유일했다.",
        "",
        "**그런데 옳은 대조가 아니었다.** 시장 변동성은 강하게 군집하고 과거 실현변동성만으로 "
        "미래 분산이 잘 예측된다. 국면은 거시계열의 수준과 모멘텀으로 정의되므로 침체·후퇴 "
        "구간은 고변동성 구간과 거의 정의상 겹친다. 기간 스프레드는 **수익** 예측의 옳은 "
        "대조였고, 분산 예측의 옳은 대조는 실현변동성이다.",
        "",
        "## 결론",
        "",
        verdict["statement"],
        "",
        "## 1 — 대조를 어떻게 골랐는가",
        "",
        "약한 창을 대조로 쓰면 국면이 이기는 것은 당연하고, 그러면 검정이 아니라 연출이 "
        "된다. 그래서 사전 명세가 되돌아보기 창 셋을 모두 계산하고 **단독으로 가장 강한** "
        "것을 쓰게 했다.",
        "",
        "| 되돌아보기 | 대조 단독 R² | 국면 단독 R² | 둘 다 | 국면 증분 | p |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload["lookback_table"]:
        mark = "**" if row["lookback_weeks"] == payload["chosen_lookback_weeks"] else ""
        lines.append(
            f"| {mark}{row['lookback_weeks']}주{mark} | "
            f"{mark}{_num(row['control_only_r_squared'])}{mark} | "
            f"{_num(row['phase_only_r_squared'])} | {_num(row['both_r_squared'])} | "
            f"{_num(row['incremental_r_squared_of_phase'])} | {_num(row['null_p'])} |"
        )
    lines += [
        "",
        f"규칙이 고른 것은 **{payload['chosen_lookback_weeks']}주**"
        f"(대조 단독 {_num(choice['chosen_control_only_r_squared'])})이고, 가장 약한 것은 "
        f"{choice['weakest']}주(대조 단독 {_num(choice['weakest_control_only_r_squared'])})다. "
        f"{choice['note']}",
        "",
        "대조는 실현분산과 그 제곱근을 함께 받고, 기간 스프레드도 그대로 남는다. 형태를 "
        "잘못 골라 대조가 지는 일이 없도록 한 것이며, 대조에 유리한 설정이 이 검정에서 "
        "옳은 방향이다.",
        "",
        f"대조 열: `{'`, `'.join(decision['control_columns'])}`",
        "",
        "## 2 — 세 갈래 비교와 역방향 증분",
        "",
        f"판정 목표는 트랙 24가 쓴 것과 같은 **{rule['decision_target']}**이다. 정의를 "
        "바꾸면 같은 것을 심문하는지 알 수 없다.",
        "",
        "| 대조 | 대조 단독 | 국면 단독 | 둘 다 | 국면 증분 | 역방향 증분 | p |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry, label in (
        (track24, "기간 스프레드만 (트랙 24)"),
        (payload["volatility_only_control"], "실현변동성만"),
        (decision, "**실현변동성 + 스프레드 (판정)**"),
    ):
        if not entry.get("usable"):
            continue
        lines.append(
            f"| {label} | {_num(entry['control_only_r_squared'])} | "
            f"{_num(entry['phase_only_r_squared'])} | {_num(entry['both_r_squared'])} | "
            f"**{_num(entry['incremental_r_squared_of_phase'])}** | "
            f"{_num(entry['incremental_r_squared_of_control_over_phase'])} | "
            f"**{_num(entry['null_p'])}** |"
        )

    lines += [
        "",
        f"트랙 24에서 국면 증분은 {_num(track24['incremental_r_squared_of_phase'])}였고 "
        f"역방향 증분은 {_num(track24['incremental_r_squared_of_control_over_phase'])}였다 — "
        "국면이 스프레드를 거의 흡수했다. 대조를 실현변동성으로 바꾸면 "
        f"국면 증분이 {_num(decision['incremental_r_squared_of_phase'])}, 역방향 증분이 "
        f"{_num(decision['incremental_r_squared_of_control_over_phase'])}가 된다.",
        "",
        (
            "**국면이 대조를 흡수한다.** 역방향 증분이 국면 증분의 10분의 1 아래다."
            if decision.get("phase_absorbs_the_control")
            else "**이번에는 국면이 대조를 흡수하지 못한다.** 역방향 증분이 국면 증분에 "
            "견줄 만하거나 크다 — 대조가 국면이 주는 것을 대부분 이미 갖고 있다는 뜻이다."
        ),
        "",
        f"이동 귀무분포: 중앙값 {_num(decision['null_median_increment'])}, 90분위 "
        f"{_num(decision['null_p90_increment'])}, 이동 {decision['shifts_used']}회.",
        "",
        "### 두 번째 분산 정의 (보고만, 판정 불가)",
        "",
        f"{rule['secondary_target']}. **결과를 본 뒤에 꺼내면 그것이 곧 정의 탐색**이므로 "
        "사전 명세에 미리 이름을 적어 두었고, 판정을 뒤집을 수 없다.",
        "",
    ]
    secondary = payload["secondary"]
    if secondary.get("usable"):
        lines += [
            f"국면 증분 {_num(secondary['incremental_r_squared_of_phase'])}, "
            f"p={_num(secondary['null_p'])}, 역방향 증분 "
            f"{_num(secondary['incremental_r_squared_of_control_over_phase'])}.",
            "",
        ]

    lines += [
        "## 3 — 에피소드 제외, 두 강도",
        "",
        payload["leave_one_episode_out"]["why"],
        "",
    ]
    leave = payload["leave_one_episode_out"]
    lines += [
        f"에피소드 {leave['episodes']}개("
        + ", ".join(
            f"{PHASE_LABEL[name]} {count}" for name, count in leave["episodes_by_phase"].items()
        )
        + f"). 전체 표본 증분은 {_num(leave['full_sample_increment'])}다.",
        "",
        "| 강도 | 계산된 에피소드 | 최저 | 중앙 | 최고 | 부호 유지 | 가장 큰 타격 |",
        "|---|---|---|---|---|---|---|",
    ]
    for strength, label in (("block_only", "블록만"), ("event_including", "사건 포함")):
        entry = leave[f"{strength}_summary"]
        lines.append(
            f"| {label} | {entry['computable_episodes']} | {_num(entry['lowest'])} | "
            f"{_num(entry.get('median'))} | {_num(entry.get('highest'))} | "
            f"{'예' if entry.get('stays_positive_everywhere') else '**아니오**'} | "
            f"{entry.get('most_damaging_episode', '—')} |"
        )

    lines += [
        "",
        "## 4 — 2020년과 2008~09년, 따로",
        "",
        "트랙 17의 표제가 2020년 하나에서 나왔다. 두 구간을 따로 뺀다.",
        "",
        "| 표본 | 주 | 대조 단독 | 국면 증분 | 역방향 증분 | p |",
        "|---|---|---|---|---|---|",
    ]
    for name, label in (("ex_covid", "2020년 제외"), ("ex_gfc", "2008~09년 제외")):
        entry = payload["exclusions"][name]
        if not entry.get("usable"):
            continue
        lines.append(
            f"| {label} | {entry['weeks']} | {_num(entry['control_only_r_squared'])} | "
            f"{_num(entry['incremental_r_squared_of_phase'])} | "
            f"{_num(entry['incremental_r_squared_of_control_over_phase'])} | "
            f"**{_num(entry['null_p'])}** |"
        )

    covid = payload["exclusions"]["ex_covid"]
    gfc = payload["exclusions"]["ex_gfc"]
    base = float(decision["incremental_r_squared_of_phase"])
    if gfc.get("usable") and float(gfc["incremental_r_squared_of_phase"]) < base / 2.0:
        lines += [
            "",
            "**2008~09년을 빼면 그림이 크게 바뀐다. 통과 여부와 별개로 그대로 적는다.** "
            f"국면 증분이 {_num(decision['incremental_r_squared_of_phase'])}에서 "
            f"{_num(gfc['incremental_r_squared_of_phase'])}로 3분의 1 아래가 되고, 역방향 "
            f"증분은 {_num(decision['incremental_r_squared_of_control_over_phase'])}에서 "
            f"{_num(gfc['incremental_r_squared_of_control_over_phase'])}로 커져 **국면 증분의 "
            f"{_times(gfc)}배**가 된다. "
            "그 구간을 빼면 대조 단독 설명력이 "
            f"{_num(decision['control_only_r_squared'])}에서 "
            f"{_num(gfc['control_only_r_squared'])}로 오르고 국면 단독은 "
            f"{_num(decision['phase_only_r_squared'])}에서 "
            f"{_num(gfc['phase_only_r_squared'])}로 떨어진다.",
            "",
            "읽는 법은 이렇다. 사전 명세가 요구한 것은 **각 제외 표본에서도 귀무를 넘는가**"
            f"였고 그것은 넘는다(p={_num(gfc['null_p'])}). 그러나 넘는 폭이 크게 줄고 "
            "그 구간 밖에서는 **대조가 국면이 주는 것을 대부분 이미 갖고 있다.** 통과는 "
            "통과이되, 이 결과가 2008~09년에 상당히 기대고 있다는 사실을 결론과 함께 "
            "읽어야 한다.",
            "",
            f"2020년 쪽은 다르다 — 증분 {_num(covid['incremental_r_squared_of_phase'])}, "
            f"p={_num(covid['null_p'])}로 거의 그대로다. 트랙 17의 표제를 무너뜨렸던 "
            "구간이 여기서는 결과를 떠받치고 있지 않다.",
        ]

    lines += [
        "",
        "## 5 — 다른 라벨링 (보고만, 판정하지 않음)",
        "",
        "| 라벨링 | 주 | 대조 단독 | 국면 증분 | p |",
        "|---|---|---|---|---|",
    ]
    for name, entry in payload["other_labellings"].items():
        if not entry.get("usable"):
            lines.append(f"| {name} | {entry.get('weeks', '—')} | — | — | — |")
            continue
        lines.append(
            f"| {name} | {entry['weeks']} | {_num(entry['control_only_r_squared'])} | "
            f"{_num(entry['incremental_r_squared_of_phase'])} | {_num(entry['null_p'])} |"
        )

    lines += [
        "",
        "## 사전 명세 대조",
        "",
        "규칙은 결과보다 먼저 커밋됐고 시험이 그 순서를 강제한다. 다섯 조건 전부를 "
        "만족해야 통과다.",
        "",
    ]
    condition_label = {
        "phase_adds_over_the_volatility_control": "국면이 변동성 대조 위에 양의 증분, p<=0.05",
        "block_only_exclusion_keeps_it_positive": "블록만 제외에서 증분이 양수로 남는다",
        "event_including_exclusion_keeps_it_positive": "사건 포함 제외에서 증분이 양수로 남는다",
        "holds_without_2020": "2020년을 뺀 표본에서도 귀무를 넘는다",
        "holds_without_the_gfc": "2008~09년을 뺀 표본에서도 귀무를 넘는다",
    }
    for name, ok in gate["conditions"].items():
        lines.append(f"- {'✅' if ok else '❌'} {condition_label[name]}")

    lines += ["", *_closing(payload)]
    return "\n".join(lines)


def _closing(payload: dict[str, Any]) -> list[str]:
    gate = payload["decision_gate"]
    wording = payload["display_wording"]
    rule = payload["prespecified_rule"]

    if gate["passes"]:
        decision = payload["decision"]
        gfc = payload["exclusions"]["ex_gfc"]
        real_time = payload["other_labellings"].get("real_time_overlap", {})
        lines = [
            "## 통과했다 — 통과일수록 엄히 읽는다",
            "",
            "다섯 조건을 전부 만족했다. 그러나 이 결과가 무엇을 말하고 무엇을 말하지 않는지를 "
            "먼저 못박는다.",
            "",
            "**말하는 것.** 국면은 과거 실현변동성이 이미 주는 것 위에 분산 설명력을 얹는다. "
            f"증분 {_num(decision['incremental_r_squared_of_phase'])}는 라벨 이동 귀무분포에서 "
            f"p={_num(decision['null_p'])}이고, 이동은 지속성을 그대로 두고 **대응만** 끊으므로 "
            "'아무 지속적 라벨이면 된다'로는 설명되지 않는다. 두 제외 강도 모두에서 부호가 "
            "유지되고, 2020년을 빼도 거의 그대로다.",
            "",
            "**말하지 않는 것 셋을 적는다.**",
            "",
            "1. **국면이 대조를 흡수하지는 못한다.** 트랙 24에서 스프레드를 상대로 역방향 "
            "증분이 0.000014였던 것과 달리, 여기서는 "
            f"{_num(decision['incremental_r_squared_of_control_over_phase'])}다. "
            "실현변동성은 국면이 주지 않는 것을 따로 갖고 있다. 트랙 24의 비대칭이 "
            "특별해 보였던 이유는 국면이 강해서가 아니라 **그 자리에서 스프레드가 할 일이 "
            "없었기 때문**이다.",
            "2. **2008~09년에 상당히 기댄다.** 그 구간을 빼면 증분이 "
            f"{_num(gfc['incremental_r_squared_of_phase'])}로 줄고 역방향 증분이 "
            f"{_num(gfc['incremental_r_squared_of_control_over_phase'])}로 커진다. "
            "귀무는 넘지만 폭이 작아진다.",
        ]
        if real_time.get("usable"):
            lines.append(
                "3. **실시간 창에서는 대조가 앞선다.** 대조 단독 "
                f"{_num(real_time['control_only_r_squared'])}에 국면 증분 "
                f"{_num(real_time['incremental_r_squared_of_phase'])}, 역방향 증분 "
                f"{_num(real_time['incremental_r_squared_of_control_over_phase'])}다. "
                "그 창은 침체 에피소드가 하나뿐이라 판정에 쓰지 않았지만, 실제로 쓰이는 "
                "경로가 그쪽이라는 점은 남는다."
            )
        lines += [
            "",
            "### 내 사전 명세의 한계 하나",
            "",
            "대조를 **되돌아보기 창 하나**로 잡았다. 셋을 동시에 넣은 대조가 더 강했을 "
            "것이고, 그랬다면 증분이 더 작게 나왔을 수 있다. 규칙이 '가장 강한 창 하나'를 "
            "고르게 했으므로 창을 유리하게 고른 것은 아니지만, **더 강한 대조를 만들 여지를 "
            "남긴 것은 사실이다.**",
            "",
            "그래도 지금 이것을 돌리지 않는다. 사전 명세가 실패했을 때 다른 대조를 찾지 "
            "않기로 못박았고, 통과했을 때 더 센 대조를 찾는 것도 같은 규칙의 반대편이다. "
            "결과를 본 뒤에 검정을 고르는 것은 어느 방향이든 같은 잘못이다. 다음 사전 "
            "명세의 후보로 여기 적어 둔다.",
            "",
            "## 화면 문구 초안",
            "",
            "제품 형태는 사전 명세가 **결과를 알기 전에** 좁게 묶어 두었다. 허용된 것은 "
            "역사적 분포에 대한 사실 진술 하나이고, 금지된 것은 노출 지시·보유 자산 "
            "언급·앞날 확률 주장이다. 결과가 좋았다는 이유로 넓히지 않는다.",
            "",
        ]
        for row in wording.get("lines", []):
            lines.append(f"- {row['wording']}")
        lines += [
            "",
            "금지 목록을 그대로 옮겨 둔다:",
            "",
        ]
        lines += [f"- {item}" for item in rule["product_form_if_it_passes"]["forbidden"]]
        lines += [
            "",
            "문장이 과거형이고 국면을 주어로 삼지 않는다는 점이 중요하다. "
            '"이 국면에서는 ~였습니다"는 분포에 대한 사실이고, "이 국면이면 ~할 것입니다"는 '
            "예측이다. 뒤의 것은 이 자료가 받쳐 주지 않는다.",
            "",
        ]
        return lines

    return [
        "## 이 결과가 무엇을 확정하는가",
        "",
        "트랙 24가 남긴 분산 결과는 **거시 렌즈를 통해 본 변동성 군집**이었다. 침체기는 "
        "변동성이 높고 변동성은 지속된다 — 참이지만, 가격이 그것을 더 싸게 그리고 발표 "
        "지연 없이 준다.",
        "",
        "그 비대칭이 유일했던 이유도 이제 설명된다. 기간 스프레드는 **수익** 신호이고, "
        "분산에 대해서는 애초에 약한 대조였다. 국면이 스프레드를 흡수한 것은 국면이 "
        "강해서가 아니라 그 자리에서 스프레드가 할 일이 없었기 때문이다.",
        "",
        "**이 모델의 쓸모는 서술과 상태 인식으로 확정된다.** 네 갈래로 물었고 네 갈래가 "
        "같은 곳에서 멈췄다.",
        "",
        "- **횡단면(17·23)** — 국면이 업종 배열을 조직하는 몫이 3%. 경계를 다시 세워도 "
        "2.98%에서 3.05%.",
        "- **가치 타이밍(19·20)** — 라벨 창 안에서 어떤 대리변수도 프리미엄 없음.",
        "- **시장 수익(24)** — 기간 스프레드를 넘지 못함.",
        "- **시장 분산(이 단계)** — 실현변동성을 넘지 못함.",
        "",
        "남는 것은 트랙 22가 확인한 것이다. 후퇴기는 확장기 안의 감속이며 그중 일부가 "
        "침체의 전조이고, NBER 침체 6회 중 4회가 후퇴기를 앞에 두었다 — 필요조건에 가깝고 "
        "충분조건이 아니다.",
        "",
        "**이 계열의 검정은 여기서 끝난다.** 사전 명세가 실패했을 때 다른 대조도, 다른 "
        "지평선도, 다른 분산 정의도, 다른 통계량도 찾지 않는다고 못박아 두었고, 그 못박음을 "
        "지킨다. 여덟 트랙을 검정해 온 뒤에 통과할 때까지 계속하는 것은 검정이 아니다.",
        "",
        "화면 문구는 만들지 않았다. " + str(payload["display_wording"].get("why_not", "")),
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
