"""후보를 고르는 규칙. **스윕을 돌리기 전에** 적는다.

트랙 20에서 배운 것을 그대로 적용한다 — 결과를 보고 기준을 움직일 여지가 있으면
그 뒤 어떤 수치가 나와도 해석할 수 없다.

## 결정적 지표

과제가 이미 정해 주었다. 전이 건수가 아니라 **후퇴기가 실제 상태가 됐는가**다.

    discrimination   0.37배에서 올라야 한다. 1.0을 못 넘으면 이름만 바꾼 것이다.
    week_share       49%(실시간) / 40%(장기)에서 내려와야 한다.
    progression      44건 중 4건에서 올라야 한다.

셋 중 **판별력이 1순위**다. 나머지 둘은 판별력 없이도 만들 수 있기 때문이다 — 후퇴기를
드물게 만들면 비중은 저절로 내려가고, 남은 블록이 우연히 침체로 이어지면 진행률도
올라간다. 판별력만이 "그 라벨이 붙은 주가 서로 닮았는가"를 직접 묻는다.

## 깨지면 안 되는 것

넷 다 **문턱이지 목적이 아니다.** 하나라도 어기면 후보에서 뺀다.

- 침체·회복 인식이 기준선보다 늦어지지 않을 것
- NBER 오탐 구간이 기준선보다 늘지 않을 것
- 침체 폭 게이트(동행 도메인 >= 2)가 계속 성립할 것
- 동결 v1.1이 그대로 재현될 것
"""

from __future__ import annotations

from typing import Any, Final

#: 판별력이 이 값을 넘어야 "실제 상태가 됐다"고 부른다. 우연과 같은 수준이 1.0이다.
DISCRIMINATION_TARGET: Final[float] = 1.0

#: 기준선의 NBER 오탐 구간 수. 이보다 늘면 탈락.
BASELINE_FALSE_POSITIVE_EPISODES: Final[int] = 5


def admissible(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """깨지면 안 되는 것들. 어긴 이유를 함께 돌려준다."""

    broken: list[str] = []
    recognition = row["recognition"]
    for name in ("contraction", "recovery"):
        entry = recognition[name]
        if entry["never_called_somewhere"]:
            broken.append(f"{name}를 부르지 못한 침체가 있다")
        elif entry["max_delay_weeks"] is not None and int(entry["max_delay_weeks"]) > 0:
            broken.append(f"{name} 인식이 {entry['max_delay_weeks']}주 늦어진다")
    if int(row["nber"]["false_positive_episodes"]) > BASELINE_FALSE_POSITIVE_EPISODES:
        broken.append(f"NBER 오탐 구간이 {row['nber']['false_positive_episodes']}건으로 늘어난다")
    if not row["breadth_gate_holds"]:
        broken.append("침체 폭 게이트가 깨진다")
    return (not broken), broken


def rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """허용되는 후보를 판별력 순으로. 동률이면 진행률로 가른다."""

    usable = []
    for row in rows:
        ok, broken = admissible(row)
        row["admissible"] = ok
        row["violations"] = broken
        if ok:
            usable.append(row)

    def key(row: dict[str, Any]) -> tuple[float, float]:
        ratio = row["discrimination"]["slowdown"]["ratio_to_chance"]
        progressed = row["progression"]["progression_rate"]
        return (
            float(ratio) if ratio is not None else -1.0,
            float(progressed) if progressed is not None else -1.0,
        )

    return sorted(usable, key=key, reverse=True)


def rule() -> dict[str, Any]:
    """보고서와 산출물에 그대로 실리는 선택 규칙."""

    return {
        "written_before_the_sweep": True,
        "primary_metric": "slowdown discrimination ratio",
        "why_primary": (
            "비중과 진행률은 판별력 없이도 만들 수 있다 — 후퇴기를 드물게 만들면 비중은 "
            "저절로 내려간다. 판별력만이 '그 라벨이 붙은 주가 서로 닮았는가'를 직접 묻는다."
        ),
        "tie_break": "progression rate",
        "target": (
            f"판별력이 {DISCRIMINATION_TARGET} 이상이어야 '실제 상태가 됐다'고 부른다. "
            "그 아래면 전이 건수가 줄어도 이름만 바꾼 것이다."
        ),
        "must_not_break": [
            "침체·회복 인식이 기준선보다 늦어지지 않을 것",
            f"NBER 오탐 구간이 {BASELINE_FALSE_POSITIVE_EPISODES}건보다 늘지 않을 것",
            "침체 폭 게이트(동행 도메인 >= 2)가 계속 성립할 것",
            "동결 v1.1이 그대로 재현될 것",
        ],
    }
