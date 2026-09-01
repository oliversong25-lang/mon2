"""전이 게이트 시뮬레이터. v1.1의 주간 경로를 입력으로 받아 **어느 전이를 받아들일지만** 정한다.

점수를 다시 계산하지 않는다. 그래서 v1.1은 그대로 재현되고 두 경로를 나란히 놓을 수 있다.

게이트는 두 조건으로 이루어진다. 어느 쪽이 일을 하는지 보이도록 **따로도 켤 수 있게** 한다.

``separation >= threshold``   그 주 1·2위 국면 점수 차가 충분한가
``raw agrees``               그 주 관측 승자가 새 국면과 같은가

막힌 전이는 직전 국면을 유지한다. 그런데 오래 막혀 있으면 모델은 자기 증거가 더는
뒷받침하지 않는 판정을 계속 내보내는 셈이 된다. 그래서 유지에 **시효**를 둔다 —
연속으로 오래 막히면 국면을 지어내지 않고 `withheld`로 내려간다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

from .characterise import PHASES, SHORT_PHASE_WEEKS, runs

#: 유지가 이 주 수를 넘겨 이어지면 국면을 내리고 판정을 보류한다.
#: 확인 규칙의 3주와 그 뒤 한 달을 더 본 값으로, 게이트가 상태를 영구히 얼리지 못하게 한다.
DEFAULT_STALE_HOLD_WEEKS: Final[int] = 8

#: 2020년 인식 시점. v1.1이 실제로 부른 주이며 지연은 여기서부터 잰다.
CONTRACTION_CALL: Final[str] = "2020-04-03"
RECOVERY_CALL: Final[str] = "2020-07-17"


@dataclass(frozen=True)
class GateConfig:
    """게이트 모수. v1.1 설정 파일을 건드리지 않고 여기에만 둔다."""

    separation_threshold: float | None = None
    require_raw_agreement: bool = False
    stale_hold_weeks: int = DEFAULT_STALE_HOLD_WEEKS

    @property
    def name(self) -> str:
        parts = []
        parts.append(
            f"sep>={self.separation_threshold:.1f}"
            if self.separation_threshold is not None
            else "sep:off"
        )
        parts.append("raw:on" if self.require_raw_agreement else "raw:off")
        return " · ".join(parts)


def apply(frame: pd.DataFrame, config: GateConfig) -> pd.DataFrame:
    """게이트를 걸어 새 공식 경로를 만든다. 원래 경로는 그대로 남긴다."""

    weeks = list(frame.index)
    ungated = frame["official_phase"].tolist()
    raw = frame["raw_phase"].tolist()
    status = frame["phase_status"].astype(str).tolist()
    separation = frame["phase_separation"].astype(float).tolist()

    gated: list[str] = []
    gated_status: list[str] = []
    blocked_run: list[int] = []
    reasons: list[str] = []

    current = ""
    held = 0
    for position, week in enumerate(weeks):
        del week
        # 보류 주는 게이트의 대상이 아니다. v1.1이 이미 국면을 내지 않기로 한 주다.
        if status[position] == "withheld" or ungated[position] not in PHASES:
            gated.append("")
            gated_status.append("withheld")
            blocked_run.append(0)
            reasons.append("upstream_withheld")
            held = 0
            continue

        if not current:
            # 첫 판정은 게이트를 걸지 않는다. 막을 직전 상태가 없다.
            current = ungated[position]
            gated.append(current)
            gated_status.append(status[position])
            blocked_run.append(0)
            reasons.append("initial")
            continue

        if ungated[position] == current:
            held = 0
            gated.append(current)
            gated_status.append(status[position])
            blocked_run.append(0)
            reasons.append("unchanged")
            continue

        # 전이 후보. 두 조건을 따로 본다.
        separation_ok = (
            config.separation_threshold is None
            or separation[position] >= config.separation_threshold
        )
        raw_ok = (not config.require_raw_agreement) or raw[position] == ungated[position]

        if separation_ok and raw_ok:
            current = ungated[position]
            held = 0
            gated.append(current)
            gated_status.append(status[position])
            blocked_run.append(0)
            reasons.append("accepted")
            continue

        # 막혔다. 직전 국면을 유지하되 얼마나 오래 유지했는지 센다.
        held += 1
        failed = []
        if not separation_ok:
            failed.append("separation")
        if not raw_ok:
            failed.append("raw")

        if held > config.stale_hold_weeks:
            # 시효를 넘겼다. 국면을 지어내지 않고 판정을 내린다.
            gated.append("")
            gated_status.append("withheld")
            blocked_run.append(held)
            reasons.append("stale_hold_degraded_to_withheld")
        else:
            gated.append(current)
            gated_status.append(status[position])
            blocked_run.append(held)
            reasons.append("blocked_" + "_".join(failed))

    out = frame.copy()
    out["gated_phase"] = gated
    out["gated_status"] = gated_status
    out["blocked_run_weeks"] = blocked_run
    out["gate_reason"] = reasons
    return out


def _first_week_of(frame: pd.DataFrame, column: str, phase: str, after: str) -> str | None:
    for week in frame.index:
        if str(week) >= after and str(frame.at[week, column]) == phase:
            return str(week)
    return None


def _delay_weeks(baseline: str, actual: str | None) -> int | None:
    if actual is None:
        return None
    return int((pd.Timestamp(actual) - pd.Timestamp(baseline)).days // 7)


def evaluate(frame: pd.DataFrame, config: GateConfig) -> dict[str, Any]:
    """한 게이트 설정의 결과. 채터링과 지연을 같은 표에 놓는다."""

    result = apply(frame, config)
    weeks = list(result.index)
    phases = result["gated_phase"].tolist()

    moves = [
        {"week": weeks[i], "from": phases[i - 1], "to": phases[i]}
        for i in range(1, len(weeks))
        if phases[i - 1] in PHASES and phases[i] in PHASES and phases[i - 1] != phases[i]
    ]

    spans = runs(phases, [str(week) for week in weeks])
    # 마지막 구간은 아직 진행 중이라 짧다고 셀 수 없다.
    closed = spans[:-1] if spans else []
    short = [span for span in closed if span["weeks"] < SHORT_PHASE_WEEKS]

    counts = {name: sum(1 for value in phases if value == name) for name in PHASES}
    withheld = sum(1 for value in result["gated_status"].tolist() if value == "withheld")

    # 2020년 인식 지연. v1.1이 부른 주부터 게이트가 부른 주까지.
    contraction_at = _first_week_of(result, "gated_phase", "contraction", CONTRACTION_CALL)
    recovery_at = _first_week_of(result, "gated_phase", "recovery", RECOVERY_CALL)

    # 공식 침체 주가 기존 폭 게이트(동행 도메인 >=2)를 여전히 만족하는가.
    contraction_weeks = result[result["gated_phase"].eq("contraction")]
    breadth_ok = bool(
        contraction_weeks.empty or (contraction_weeks["confirming_domains"].astype(int) >= 2).all()
    )

    degraded = sum(
        1 for value in result["gate_reason"].tolist() if value == "stale_hold_degraded_to_withheld"
    )

    # 게이트가 **무엇을 막았는가**. 전이 수만 세면 채터링을 지운 것과 옳은 전이를 지운 것이
    # 구별되지 않는다 — 매끄럽지만 틀린 경로가 바로 그렇게 만들어진다.
    #
    # 경로가 갈라져 그 주에 전이가 없어진 것을 "차단"으로 세면 안 된다. 그래서 게이트가
    # 실제로 막았다고 기록한 주(`gate_reason`)만 본다. 처음에 그 구분을 놓쳐 차단 건수를
    # 두 배 넘게 세었고, 그 잘못된 수로 권고를 잡을 뻔했다.
    from .characterise import transitions as _transitions

    catalogue = {entry["week"]: entry for entry in _transitions(frame)}
    blocked_weeks = [
        week for week in result.index if str(result.at[week, "gate_reason"]).startswith("blocked")
    ]
    blocked_short = sum(
        1 for week in blocked_weeks if week in catalogue and catalogue[week]["short"]
    )
    blocked_long = sum(
        1
        for week in blocked_weeks
        if week in catalogue
        and not catalogue[week]["censored"]
        and catalogue[week]["duration_weeks"] >= SHORT_PHASE_WEEKS
    )

    return {
        "gate": config.name,
        "separation_threshold": config.separation_threshold,
        "require_raw_agreement": config.require_raw_agreement,
        "stale_hold_weeks": config.stale_hold_weeks,
        "transitions": len(moves),
        "phases_shorter_than_four_weeks": len(short),
        "short_phase_list": [
            {"phase": s["phase"], "start": s["start"], "weeks": s["weeks"]} for s in short
        ],
        "contraction_call_week": contraction_at,
        "contraction_delay_weeks": _delay_weeks(CONTRACTION_CALL, contraction_at),
        "recovery_call_week": recovery_at,
        "recovery_delay_weeks": _delay_weeks(RECOVERY_CALL, recovery_at),
        "phase_weeks": counts,
        "withheld_weeks": withheld,
        "degraded_to_withheld_weeks": degraded,
        "contraction_weeks_meet_the_two_domain_breadth_gate": breadth_ok,
        "blocked_weeks": len(blocked_weeks),
        "blocked_transitions_that_were_short": blocked_short,
        "blocked_transitions_that_lasted_four_weeks_or_more": blocked_long,
        # 1보다 크면 왕복을 진짜보다 많이 잡은 것이고, 작으면 그 반대다.
        "whipsaw_to_real_ratio": round(blocked_short / blocked_long, 2) if blocked_long else None,
        "longest_blocked_run_weeks": int(max(result["blocked_run_weeks"].tolist() or [0])),
        "final_phase": next((value for value in reversed(phases) if value in PHASES), ""),
        "final_week_phase": phases[-1] or "withheld",
    }


def sweep(
    frame: pd.DataFrame, thresholds: tuple[float, ...] = (0.4, 0.5, 0.6, 0.7)
) -> list[dict[str, Any]]:
    """두 조건을 따로, 그리고 함께. 어느 쪽이 일을 하는지 보이게 만든다."""

    configs = [GateConfig()]  # 기준선 = v1.1 그대로
    configs.append(GateConfig(require_raw_agreement=True))
    for threshold in thresholds:
        configs.append(GateConfig(separation_threshold=threshold))
    for threshold in thresholds:
        configs.append(GateConfig(separation_threshold=threshold, require_raw_agreement=True))
    return [evaluate(frame, config) for config in configs]
