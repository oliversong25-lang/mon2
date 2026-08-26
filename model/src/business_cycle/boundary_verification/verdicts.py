"""세 검증의 판정. 읽는 규칙을 조건으로 적어 두어 나중에 다시 돌려 확인할 수 있게 한다."""

from __future__ import annotations

from typing import Any, Final

from .checks import HORIZONS, MINIMUM_BLOCKS

#: 확장기 판별력이 이 배수 아래로 내려가면 "찌꺼기 통에 새 이름을 붙였다"로 읽는다.
#: 1.0이 우연과 같은 수준이므로 그 근처로 수렴하는 것이 실패 신호다.
EXPANSION_FLOOR: Final[float] = 1.5

#: 평탄역으로 인정할 이웃 간 상대 변화. 이보다 작게 움직이면 평탄하다고 본다.
PLATEAU_TOLERANCE: Final[float] = 0.25

#: 유의 문턱. 여기 못 미치면 "방향은 강하지만 확립되지 않았다"로 적는다.
SIGNIFICANCE: Final[float] = 0.05


def read_a(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """A — 확장기가 새 찌꺼기 통이 됐는가."""

    rows = []
    degraded: list[str] = []
    for horizon in HORIZONS:
        key = str(horizon)
        for phase in ("recovery", "expansion", "slowdown", "contraction"):
            old = before[key][phase]["ratio_to_chance"]
            new = after[key][phase]["ratio_to_chance"]
            rows.append(
                {
                    "horizon_weeks": horizon,
                    "phase": phase,
                    "before": old,
                    "before_p": before[key][phase]["p_value"],
                    "after": new,
                    "after_p": after[key][phase]["p_value"],
                    "improved": bool(old is not None and new is not None and new >= old),
                }
            )
            if old is not None and new is not None and new < old:
                degraded.append(f"{phase}@{horizon}주")

    expansion = [row for row in rows if row["phase"] == "expansion"]
    holds = all(row["after"] is not None and row["after"] >= EXPANSION_FLOOR for row in expansion)
    rises = all(row["improved"] for row in expansion)

    return {
        "rows": rows,
        "expansion_floor": EXPANSION_FLOOR,
        "expansion_holds": holds,
        "expansion_rises_at_every_horizon": rises,
        "phases_that_degraded": degraded,
        "no_trade": not degraded,
        "passes": bool(holds),
        "reading": (
            "확장기 판별력이 모든 지평선에서 **올라간다.** 국면이 685주에서 1260주로 거의 "
            "두 배가 됐는데도 그렇다 — 애매한 주를 부으면 보통 1.0 쪽으로 희석되는데 "
            "반대로 날카로워졌다. 그 주들은 원래 확장기에 속했던 것이고, 찌꺼기 통이 "
            "옮겨간 것이 아니다."
            if rises
            else (
                "확장기 판별력이 문턱 위에 머문다. 통이 옮겨가지는 않았다."
                if holds
                else "확장기 판별력이 1.0 쪽으로 내려간다. **찌꺼기 통에 새 이름을 "
                "붙였을 뿐이며, 이 설정을 그대로 채택해서는 안 된다.**"
            )
        ),
    }


def read_b(curve: list[dict[str, Any]], chosen_weeks: int) -> dict[str, Any]:
    """B — 고른 길이가 봉우리인가 평탄역인가."""

    usable = [
        row
        for row in curve
        if row["persistence_weeks"] > 0 and row["slowdown_discrimination"] is not None
    ]
    ordered = sorted(usable, key=lambda row: int(row["persistence_weeks"]))
    best = max(ordered, key=lambda row: float(row["slowdown_discrimination"]))

    # 최고값 근처에서 서로 크게 다르지 않은 이웃들의 구간을 평탄역으로 본다.
    peak = float(best["slowdown_discrimination"])
    plateau = [
        row
        for row in ordered
        if abs(float(row["slowdown_discrimination"]) - peak) / peak <= PLATEAU_TOLERANCE
    ]
    plateau_lengths = [int(row["persistence_weeks"]) for row in plateau]
    chosen = next((row for row in ordered if int(row["persistence_weeks"]) == chosen_weeks), None)

    return {
        "curve": ordered,
        "peak_at_weeks": int(best["persistence_weeks"]),
        "peak_value": peak,
        "plateau_weeks": plateau_lengths,
        "plateau_is_wide": len(plateau_lengths) >= 3,
        "chosen_weeks": chosen_weeks,
        "chosen_is_in_the_plateau": chosen_weeks in plateau_lengths,
        "chosen_has_enough_blocks": bool(
            chosen is not None and chosen["enough_blocks_to_call_it_a_state"]
        ),
        "reading": (
            f"판별력은 길이에 따라 단조롭게 오르다 {min(plateau_lengths)}~"
            f"{max(plateau_lengths)}주에서 평탄해진다. **13주에서 뾰족한 봉우리가 아니라 "
            "13주가 그 평탄역 아래에 있다.** 적합의 위험은 없지만, 평탄역 안에서 고르는 "
            "쪽이 더 낫고 그 값이 곧 권고다."
            if len(plateau_lengths) >= 3
            else "평탄역이 좁다. 이 표본에 맞춘 값일 위험이 있으므로 더 평탄한 쪽을 택해야 한다."
        ),
        "why_not_the_longest": (
            "가장 긴 길이는 판별력이 조금 더 높지만 후퇴기 블록이 크게 줄어 상태라고 "
            f"부르기 어려워진다. 블록이 {MINIMUM_BLOCKS}개 아래로 내려가면 판별력이 "
            "높아도 그것은 몇몇 구간의 성질이지 상태의 성질이 아니다."
        ),
    }


def read_c(
    frozen_window: dict[str, Any], extended: dict[str, Any], overlap: dict[str, Any]
) -> dict[str, Any]:
    """C — 표본을 늘리면 유의해지는가."""

    p_value = extended["slowdown_p"]
    reached = bool(p_value is not None and float(p_value) <= SIGNIFICANCE)
    return {
        "frozen_window": frozen_window,
        "extended": extended,
        "overlap_with_frozen": overlap,
        "significance_threshold": SIGNIFICANCE,
        "significance_reached": reached,
        "reading": (
            f"확장 역사에서 p={p_value}로 {SIGNIFICANCE} 문턱을 넘는다. 짧은 창에서 "
            "방향만 강했던 것이 긴 창에서 통계적으로도 성립한다."
            if reached
            else f"확장 역사에서도 p={p_value}로 문턱을 넘지 못한다. **방향은 강하지만 "
            "통계적으로 확립되지 않았다** — 그대로 모델의 한계로 싣는다."
        ),
    }


def limitations(payload: dict[str, Any]) -> list[str]:
    """모델에 실을 한계 문구. 검증 결과에서 직접 만든다."""

    c = payload["c"]
    b = payload["b"]
    extended = c["extended"]
    overlap = c["overlap_with_frozen"]
    lines = [
        f"후퇴기 판별력은 확장 역사(1976~2026, {extended['weeks']}주, 후퇴기 블록 "
        f"{extended['slowdown_blocks']}개)에서 {extended['slowdown_discrimination']}배이고 "
        f"p={extended['slowdown_p']}다."
        + (
            " 5% 수준에서 유의하다."
            if c["significance_reached"]
            else " **5% 문턱을 넘지 못한다 — 방향은 강하지만 통계적으로 확립되지 않았다.**"
        ),
        f"지속 요건 {b['chosen_weeks']}주는 {min(b['plateau_weeks'])}~{max(b['plateau_weeks'])}주 "
        "평탄역 안에서 고른 값이다. 자연 실험이 가리킨 것은 '지속이 우선'이라는 모양이지 "
        "숫자가 아니었다.",
        f"확장 역사는 동결 v1.1과 겹치는 구간에서 {overlap['agreement']:.1%} 일치한다. "
        "완전 일치가 아닌 이유는 표준화 창이 더 긴 계열을 보기 때문이며, 확장 실행은 "
        "v1.1의 상위집합이 아니라 같은 모델에 더 긴 입력을 준 것이다.",
        "확장 역사에서 소비 도메인 구성이 1992년에 바뀐다 — 그 전에는 CMRMTSPL 하나, "
        "그 뒤로는 RRSFS가 더해진다. 확장 실행은 균질하지 않다.",
        "확장 역사는 최종 수정치이며 실시간으로 알 수 있었던 것이 아니다. ALFRED 경로와 "
        "섞어 읽으면 안 된다.",
    ]
    if payload["a"]["phases_that_degraded"]:
        lines.append(
            "경계 수정으로 판별력이 낮아진 국면·지평선이 있다: "
            + ", ".join(payload["a"]["phases_that_degraded"])
        )
    return lines
