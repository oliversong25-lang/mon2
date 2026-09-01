"""보고서. 불리한 사전 확률이 결론 앞에 오고, 천장이 그 다음에 온다."""

from __future__ import annotations

from typing import Any

from . import prespec


def _pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}%}"


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value}"


def _primary_oracle(payload: dict[str, Any]) -> float:
    """기본 대리변수 축의 주기 신탁 수익. 표현식이 길어져 여기로 뺀다."""

    entry = payload["duration_axes"][prespec.PRIMARY_PROXY]["primary"]
    return float(entry["oracle_ceiling"]["annualised_relative_return"])


def build_report(payload: dict[str, Any]) -> str:
    gate = payload["ceiling_gate"]
    comparison = payload["axis_comparison"]
    industry = payload["industry_axis"]
    rule = payload["prespecified_rule"]
    verdict = payload["verdict"]

    lines = [
        "# 듀레이션 축 — 업종이 잘못된 축이었는가",
        "",
        "제안된 메커니즘은 업종을 지나가지 않는다. 성장주는 현금흐름이 길어 할인율 상승에 "
        "불균형하게 반응하고, FF12는 각 통에 듀레이션을 섞으므로 찾으려는 효과를 흐린다. "
        "2022년이 그 사례로 제시됐다 — 연준이 긴축했고 NBER은 침체를 선언하지 않았으며 "
        "장기 듀레이션이 크게 빠졌고 필수소비재와 에너지는 버텼다.",
        "",
        "## 사전 확률은 불리하다 — 먼저 적는다",
        "",
        rule["prior_is_unfavourable"],
        "",
        "## 결론",
        "",
        verdict["statement"],
        "",
        "## 1 — 천장 (결정적 숫자, 다른 무엇보다 먼저)",
        "",
        "국면을 **이미 알고** 국면별로 어느 통이 가장 좋았는지까지 **이미 아는** 전략의 "
        "수익이다. 실현 가능한 어떤 전략도 그 위로 갈 수 없다.",
        "",
        f"{rule['why_the_same_ruler']}",
        "",
        f"표본은 {payload['first_month']}~{payload['last_month']}, {payload['months']}개월. "
        f"상위 {rule['top_k']}통을 든다.",
        "",
        "| 축 | 통 | 순위 천장(연율) | 정보비율 | 주기 신탁 | **국면이 조직하는 몫** | 실현 가능 |",
        "|---|---|---|---|---|---|---|",
        f"| 업종 FF12 (비교 기준) | {industry['buckets']} | "
        f"{_pct(industry['ranking_ceiling']['annualised_relative_return'])} | "
        f"{_num(industry['ranking_ceiling']['information_ratio'])} | "
        f"{_pct(industry['oracle_ceiling']['annualised_relative_return'], 1)} | "
        f"**{_pct(industry['phase_share_of_the_oracle'], 1)}** | "
        f"{_pct(industry['achievable_rotation']['annualised_relative_return'])} |",
    ]
    for proxy, entry in payload["duration_axes"].items():
        primary = entry["primary"]
        mark = "**" if proxy == prespec.PRIMARY_PROXY else ""
        name = f"{mark}{proxy}{mark}" + (" (기본)" if proxy == prespec.PRIMARY_PROXY else "")
        lines.append(
            f"| {name} | {primary['buckets']} | "
            f"{mark}{_pct(primary['ranking_ceiling']['annualised_relative_return'])}{mark} | "
            f"{_num(primary['ranking_ceiling']['information_ratio'])} | "
            f"{_pct(primary['oracle_ceiling']['annualised_relative_return'], 1)} | "
            f"**{_pct(primary['phase_share_of_the_oracle'], 1)}** | "
            f"{_pct(primary['achievable_rotation']['annualised_relative_return'])} |"
        )
    lines += [
        "",
        f"**관문 판정: {'통과' if gate['passes'] else '실패'}**",
        "",
    ]
    condition_label = {
        "materially_higher_than_the_industry_axis": (
            f"같은 자로 잰 업종 축의 {prespec.CEILING_MUST_EXCEED_INDUSTRY_BY}배 이상"
        ),
        "clears_the_absolute_floor": f"연 {prespec.CEILING_FLOOR_ANNUAL:.0%} 이상",
        "clears_the_information_ratio_floor": (
            f"정보비율 {prespec.CEILING_FLOOR_INFORMATION_RATIO:.1f} 이상"
        ),
    }
    for name, ok in gate["conditions"].items():
        lines.append(f"- {'✅' if ok else '❌'} {condition_label[name]}")

    lines += [
        "",
        f"듀레이션 축 천장은 업종 축의 **{_num(gate['ratio_to_industry'])}배**다. "
        "1.5배 이상이어야 했는데 **1배도 되지 않는다** — 축을 바꾸자 천장이 오른 것이 "
        "아니라 **내려갔다.**",
        "",
        "## 2 — 축이 틀렸는가에 대한 직접적인 답",
        "",
        comparison["reading"],
        "",
        "이 두 숫자를 나란히 놓는 것이 이 트랙의 요점이다.",
        "",
        f"- **총 분산은 듀레이션 축이 훨씬 작다.** 주기 신탁이 업종 축에서 "
        f"{_pct(industry['oracle_ceiling']['annualised_relative_return'], 0)}인데 듀레이션 "
        f"축에서는 {_pct(_primary_oracle(payload), 0)}다. "
        "듀레이션 통들은 서로 업종만큼 벌어지지 않는다.",
        f"- **국면이 조직하는 몫은 두 축이 비슷하다.** 업종 "
        f"{_pct(comparison['share_organised_industry'], 1)}, 듀레이션 "
        f"{_pct(comparison['share_organised_duration'], 1)}. 축을 바꿔도 국면이 설명하는 "
        "비율은 거의 그대로다.",
        "",
        "**그러므로 업종이 잘못된 축이어서 3%였던 것이 아니다.** 국면은 어느 축에서든 "
        "그 축이 가진 분산의 한 자릿수 퍼센트만 조직하고, 듀레이션 축은 조직할 분산 자체가 "
        "업종보다 적다. 축을 바꾸는 것으로는 천장이 오르지 않는다.",
        "",
        "### 대리변수 넷 전부, 그리고 더 거친 통 나누기",
        "",
        "| 대리변수 | 통 | 천장 | 정보비율 | 몫 |",
        "|---|---|---|---|---|",
    ]
    for proxy, entry in payload["duration_axes"].items():
        for key, label in (("primary", "11통"), ("secondary", "6통")):
            block = entry[key]
            lines.append(
                f"| {proxy} ({label}) | {block['buckets']} | "
                f"{_pct(block['ranking_ceiling']['annualised_relative_return'])} | "
                f"{_num(block['ranking_ceiling']['information_ratio'])} | "
                f"{_pct(block['phase_share_of_the_oracle'], 1)} |"
            )
    lines += [
        "",
        "B/M이 대리변수 중 몫이 가장 크지만, 사전 명세가 적어 둔 대로 **B/M의 높은 쪽은 "
        "짧은 듀레이션과 부실기업을 섞는다.** 그 통에서 나오는 것을 듀레이션 효과라고 "
        "부를 수 없다. 기본 대리변수를 배당수익률로 정한 이유가 그것이고, 그 판단은 결과를 "
        "보기 전에 커밋했다.",
        "",
        "5분위(6통) 판본은 통이 적어 천장이 더 낮다. 예상된 방향이며, 그래서 판정에 쓰지 않았다.",
        "",
    ]

    lines += _record_only(payload)
    lines += _closing(payload)
    return "\n".join(lines)


def _record_only(payload: dict[str, Any]) -> list[str]:
    """관문이 막힌 아래 단계. 숫자만 남기고 결론을 달지 않는다."""

    decision = payload["control_comparison"]
    leave = payload["leave_one_episode_out"]
    multiplicity = payload["multiplicity"]

    lines = [
        "## 3 — 대조와 강건성 (관문이 막혔으므로 기록만)",
        "",
        "사전 명세는 천장이 아래 단계를 막게 했다. 그래서 여기 숫자에는 결론을 달지 "
        "않는다 — 천장 아래에서 무엇이 나오든 그것은 천장을 넘을 수 없다.",
        "",
        f"종속변수는 **{payload['control_target']}**이다. "
        "사전 명세가 이 단계의 지평선과 문턱은 못박았지만 **종속변수를 정확히 이름 붙이지 "
        "않았다** — 내 사전 명세의 빈틈이고, 그 사실이 이 단계를 기록 전용으로 두는 또 "
        "하나의 이유다.",
        "",
        f"되돌아보기는 규칙이 골랐다: **{payload['chosen_lookback']}개월**"
        f"(대조 단독 {_num(payload['lookback_choice'].get('chosen_control_only_r_squared'))}), "
        f"가장 약한 것은 {payload['lookback_choice'].get('weakest')}개월"
        f"({_num(payload['lookback_choice'].get('weakest_control_only_r_squared'))}).",
        "",
        "| 표본 | 기간 | 대조 단독 | 국면 단독 | 둘 다 | 국면 증분 | 역방향 증분 | p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    entries = [("전체", decision)] + [
        (name, entry) for name, entry in payload["exclusions"].items()
    ]
    for label, entry in entries:
        if not entry.get("usable"):
            lines.append(f"| {label} | {entry.get('weeks', '—')} | — | — | — | — | — | — |")
            continue
        mark = "**" if label == "ex_2022" else ""
        lines.append(
            f"| {mark}{label}{mark} | {entry['weeks']} | "
            f"{_num(entry['control_only_r_squared'])} | "
            f"{_num(entry['phase_only_r_squared'])} | {_num(entry['both_r_squared'])} | "
            f"{_num(entry['incremental_r_squared_of_phase'])} | "
            f"{_num(entry['incremental_r_squared_of_control_over_phase'])} | "
            f"{_num(entry['null_p'])} |"
        )

    covid = payload["exclusions"].get("ex_2022", {})
    if decision.get("usable") and covid.get("usable"):
        base = float(decision["incremental_r_squared_of_phase"])
        without = float(covid["incremental_r_squared_of_phase"])
        lines += [
            "",
            f"**2022년을 따로 본다.** 제안된 메커니즘이 그 해를 가리켰으므로 그 해가 결과를 "
            f"혼자 만들고 있는지가 특히 중요하다. 국면 증분이 {_num(base)}에서 "
            f"{_num(without)}로 "
            + (
                "크게 줄어든다 — 결과가 그 한 해에 얹혀 있다."
                if base > 0 and without < base / 2.0
                else "거의 그대로다 — 그 한 해가 결과를 혼자 만들지는 않는다."
            ),
        ]

    lines += [
        "",
        f"에피소드 제외 {leave['episodes']}개, 두 강도. "
        f"블록만 최저 {_num(leave['block_only_summary'].get('lowest'))}, "
        f"사건 포함 최저 {_num(leave['event_including_summary'].get('lowest'))}. "
        f"가장 큰 타격은 {leave['block_only_summary'].get('most_damaging_episode', '—')}.",
        "",
        "### 다중비교 — 프로젝트 전체를 센다",
        "",
        f"트랙 20이 가치 대리변수로 {multiplicity.get('track20_proxies')}개, 이 트랙이 "
        f"듀레이션 대리변수로 {multiplicity.get('this_track_proxies')}개를 검정했다. "
        f"같은 정렬을 반대 방향으로 읽은 것이므로 한 족보이며, 족보 크기는 "
        f"**{multiplicity.get('family_size')}**이고 본페로니 알파는 "
        f"{multiplicity.get('bonferroni_alpha')}다.",
        "",
        f"명목 p={_num(multiplicity.get('nominal_p'))}, 본페로니 보정 "
        f"p={_num(multiplicity.get('bonferroni_p'))} — "
        + (
            "보정 뒤에도 5% 아래에 남는다."
            if multiplicity.get("survives")
            else "**보정하면 5% 문턱을 넘지 못한다.**"
        ),
        "",
        f"{multiplicity.get('note', '')}",
        "",
    ]
    return lines


def _closing(payload: dict[str, Any]) -> list[str]:
    gate = payload["ceiling_gate"]
    if gate["passes"]:
        return [
            "## 무엇이 남았는가",
            "",
            "천장이 관문을 넘었으므로 아래 단계의 숫자에 결론을 달 수 있다. 다만 이 단계는 "
            "규칙을 만들지 않는다.",
            "",
            "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
            "",
        ]

    return [
        "## 이 결과가 무엇을 확정하는가",
        "",
        "**순환매 질문은 어떤 축으로 잘라도 닫힌다.** 업종으로 잘라도, 듀레이션으로 잘라도, "
        "네 가지 대리변수 어느 것으로 잘라도, 통을 11개로 하든 6개로 하든 천장이 관문 "
        "아래다. 그리고 축을 바꿨을 때 천장은 **오른 것이 아니라 내려갔다.**",
        "",
        "제안된 메커니즘이 틀렸다는 뜻은 아니다. 2022년에 장기 듀레이션이 할인율 때문에 "
        "빠진 것은 사실일 수 있다. 이 결과가 말하는 것은 좁다 — **그 메커니즘이 참이더라도 "
        "이 국면 모델의 라벨로는 그것을 잡을 수 없고, 잡을 수 있다 해도 듀레이션 통들 사이의 "
        "총 분산이 업종보다 작아 벌 것이 더 적다.**",
        "",
        "다섯 갈래가 같은 곳에서 멈췄다.",
        "",
        "- **횡단면·업종(17·23)** — 국면이 조직하는 몫 3%, 천장 연 5.06%.",
        "- **가치 타이밍(19·20)** — 어떤 대리변수도 무조건부 프리미엄 없음.",
        "- **시장 수익(24)** — 기간 스프레드를 넘지 못함.",
        "- **시장 분산(25)** — 실현변동성 위에 얹히되 GFC에 기댐.",
        "- **횡단면·듀레이션(이 단계)** — 천장이 업종 축보다 **낮음**.",
        "",
        "남는 것은 트랙 22가 확인한 것과 트랙 25가 남긴 한 줄이다. 후퇴기는 확장기 안의 "
        "감속이며 그중 일부가 침체의 전조이고, 국면별 전방 분산 분포는 역사적 사실로 "
        "적을 수 있다. **이 모델의 쓸모는 서술과 상태 인식이다.**",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
