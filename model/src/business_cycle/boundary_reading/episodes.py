"""후퇴기 블록이 실제로 무엇이었는지 하나씩 적는다.

판별력은 분명히 좋아졌다. 그런데 지금 잡히는 것이 **확장기 안의 짧고 날카로운 감속**인지
**확장에서 침체로 넘어가는 국면**인지가 숫자만으로는 갈리지 않는다. 둘은 다른 물건이고,
화면 문구와 이후 업종 매핑이 달라진다.

결함을 찾자는 것이 아니라 후퇴기가 이제 무엇을 뜻하는지 정의하기 위한 목록이다.

## 무엇으로 가르는가

``progressed``  다음 국면이 침체기다. 전조였다.
``reverted``    다음 국면이 확장기다. 확장기 안의 감속이었다.
``open``        아직 끝나지 않았다. 어느 쪽인지 아직 모른다.

여기에 NBER 침체와의 거리를 함께 단다. 되돌아간 블록이라도 실제 침체 직전에 있었으면
"거짓 경보"와 "조금 이른 경보"는 다른 말이다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

from ..slowdown_boundary.natural import blocks
from .regimes import NBER_RECESSIONS

#: 블록 끝과 NBER 침체 시작 사이가 이 안이면 "침체를 앞두고 있었다"로 적는다.
#: 반년이다 — 월 단위 자료에서 한두 번의 발표 안에 들어오는 거리다.
NEAR_RECESSION_WEEKS: Final[int] = 26


def _weeks_between(earlier: str, later: str) -> int:
    return int((pd.Timestamp(later) - pd.Timestamp(earlier)).days // 7)


def _nearest_recession(end: str) -> dict[str, Any]:
    """이 블록이 끝난 뒤 가장 먼저 오는 NBER 침체까지 몇 주인가."""

    ahead = [(start, _weeks_between(end, start)) for start, _ in NBER_RECESSIONS]
    upcoming = [(start, gap) for start, gap in ahead if gap >= 0]
    if not upcoming:
        return {"next_nber_recession": None, "weeks_until_recession": None, "near": False}
    start, gap = min(upcoming, key=lambda item: item[1])
    return {
        "next_nber_recession": start[:7],
        "weeks_until_recession": gap,
        "near": bool(gap <= NEAR_RECESSION_WEEKS),
    }


def listing(phase: pd.Series) -> list[dict[str, Any]]:
    """후퇴기 블록 전부. 시작·종료·주수·간 곳·침체와의 거리."""

    out: list[dict[str, Any]] = []
    for index, span in enumerate(
        [item for item in blocks(phase) if item["phase"] == "slowdown"], start=1
    ):
        destination = span["next"]
        out.append(
            {
                "index": index,
                "start": span["start"],
                "end": span["end"],
                "weeks": int(span["weeks"]),
                "came_from": span["previous"],
                "went_to": destination,
                "outcome": (
                    "open"
                    if destination is None
                    else ("progressed" if destination == "contraction" else "reverted")
                ),
                **_nearest_recession(str(span["end"])),
            }
        )
    return out


def recession_coverage(rows: list[dict[str, Any]], first: str, last: str) -> dict[str, Any]:
    """반대 방향의 물음 — **침체가 후퇴기를 앞세웠는가.**

    블록 쪽에서만 보면 "후퇴기 뒤에 무엇이 왔는가"밖에 알 수 없다. 그것은 정밀도다.
    정의를 하려면 재현율도 있어야 한다: 실제 침체 중 몇 번이 후퇴기를 앞에 두었는가.
    """

    covered: list[dict[str, Any]] = []
    for start, _ in NBER_RECESSIONS:
        if not (first <= start <= last):
            continue
        ahead = [
            row
            for row in rows
            if 0 <= _weeks_between(str(row["end"]), start) <= NEAR_RECESSION_WEEKS
        ]
        covered.append(
            {
                "recession": start[:7],
                "preceded_by_slowdown": bool(ahead),
                "lead_weeks": (
                    min(_weeks_between(str(row["end"]), start) for row in ahead) if ahead else None
                ),
            }
        )
    hit = sum(1 for entry in covered if entry["preceded_by_slowdown"])
    return {
        "recessions_in_window": len(covered),
        "preceded_by_a_slowdown_block": hit,
        "coverage": round(hit / len(covered), 3) if covered else None,
        "lead_weeks_window": NEAR_RECESSION_WEEKS,
        "detail": covered,
    }


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """목록이 후퇴기를 무엇으로 정의하는가."""

    closed = [row for row in rows if row["outcome"] != "open"]
    progressed = [row for row in closed if row["outcome"] == "progressed"]
    reverted = [row for row in closed if row["outcome"] == "reverted"]
    reverted_near = [row for row in reverted if row["near"]]
    lengths = sorted(int(row["weeks"]) for row in closed)
    median = float(lengths[len(lengths) // 2]) if lengths else float("nan")

    # 침체로 나아갔거나, 되돌아갔어도 침체를 코앞에 두고 있었던 블록. 둘 다 "전조"쪽이다.
    forewarning = len(progressed) + len(reverted_near)
    share = round(forewarning / len(closed), 3) if closed else None

    return {
        "blocks": len(rows),
        "closed_blocks": len(closed),
        "progressed_to_contraction": len(progressed),
        "reverted_to_expansion": len(reverted),
        "reverted_but_within_six_months_of_a_recession": len(reverted_near),
        "forewarning_blocks": forewarning,
        "forewarning_share": share,
        "median_weeks": median,
        "shortest_weeks": lengths[0] if lengths else None,
        "longest_weeks": lengths[-1] if lengths else None,
        "near_recession_weeks": NEAR_RECESSION_WEEKS,
        "precision_reading": (
            "닫힌 블록의 절반 이상이 침체로 나아갔거나 침체를 반년 안에 두고 있었다."
            if share is not None and share >= 0.5
            else "닫힌 블록의 절반 이상이 침체와 무관하게 확장기로 되돌아갔다."
        ),
    }


def define(summary: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    """정밀도와 재현율을 함께 놓고 후퇴기가 무엇인지 정한다.

    한쪽만 보면 반드시 틀린다. "블록 뒤에 무엇이 왔는가"만 보면 후퇴기는 잡음처럼
    보이고, "침체 앞에 무엇이 있었는가"만 보면 전조처럼 보인다. 둘 다 적어야 화면
    문구를 정할 수 있다.
    """

    precision = summary["forewarning_share"]
    recall = coverage["coverage"]
    mostly_forewarning = precision is not None and float(precision) >= 0.5
    mostly_covered = recall is not None and float(recall) >= 0.5

    if mostly_forewarning and mostly_covered:
        statement = (
            "후퇴기는 **확장에서 침체로 넘어가는 국면**이다. 블록 대부분이 침체로 이어졌고, "
            "실제 침체 대부분이 후퇴기를 앞에 두었다."
        )
    elif mostly_covered:
        statement = (
            "후퇴기는 **확장기 안의 감속**이며, 그중 일부가 침체의 전조였다. 블록 하나를 "
            "보고 침체를 말할 수는 없지만"
            f"(닫힌 블록 {summary['closed_blocks']}개 중 {summary['forewarning_blocks']}개), "
            f"실제 침체는 대부분 후퇴기를 앞에 두었다"
            f"({coverage['preceded_by_a_slowdown_block']}/"
            f"{coverage['recessions_in_window']}회). **필요조건에 가깝고 충분조건이 아니다.**"
        )
    elif mostly_forewarning:
        statement = (
            "후퇴기는 드물지만 뜰 때는 침체로 이어진다. 다만 침체 중 상당수가 후퇴기 없이 "
            "왔으므로 **놓치는 쪽이 많다.**"
        )
    else:
        statement = (
            "후퇴기는 **확장기 안의 감속**이다. 블록 대부분이 확장기로 되돌아갔고, 실제 "
            "침체도 대부분 후퇴기를 앞에 두지 않았다. 침체와 잇대어 읽으면 안 된다."
        )

    return {
        "precision": precision,
        "recall": recall,
        "reads_as_a_transition_phase": bool(mostly_forewarning and mostly_covered),
        "statement": statement + " 화면 문구는 이 정의에 맞춰야 하고, 둘을 섞어 쓰면 안 된다.",
    }
