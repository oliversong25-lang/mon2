"""이미 있는 값들의 **2차 읽기**. 성숙도는 여기 산다.

주간 경로는 ``level``, ``momentum``, ``confirming_domains``, ``concentration``을 매주
내보낸다. 없는 것은 그 값들의 **변화율**이고, 후반부의 특징은 대부분 거기서 보인다.

확장기 후반은 보통 이렇게 생겼다 — 수준은 여전히 높은데 모멘텀이 둔화되고 폭이 좁아진다.
저점 통과는 이렇게 생겼다 — 수준은 아직 음인데 악화 속도가 줄고 확인 도메인이 늘기
시작한다. 둘 다 손에 있는 값의 2차 읽기다.
"""

from __future__ import annotations

from typing import Final

import pandas as pd

#: 2차 읽기의 기준 창. 모델의 모멘텀 창(8주)과 같게 미리 정해 둔다. 결과를 보고 고르지
#: 않기 위해서이며, 4주·13주는 민감도로만 함께 싣는다.
CHANGE_WEEKS: Final[int] = 8

#: 민감도로 함께 보고할 창. 이 중에서 좋은 것을 고르지 않는다.
SENSITIVITY_WEEKS: Final[tuple[int, ...]] = (4, 8, 13)

REQUIRED: Final[tuple[str, ...]] = (
    "official_phase",
    "activity_level",
    "activity_momentum",
    "confirming_domains",
    "concentration",
    "negative_level_domains",
    "negative_momentum_domains",
)


def load_path(path: str) -> pd.DataFrame:
    """주간 경로. 값을 다시 계산하지 않는다."""

    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.Index([str(week) for week in frame.index], name="week")
    missing = [name for name in REQUIRED if name not in frame.columns]
    if missing:
        raise KeyError(f"주간 경로에 필요한 열이 없다: {missing}")
    return frame


def derive(frame: pd.DataFrame, weeks: int = CHANGE_WEEKS) -> pd.DataFrame:
    """2차 읽기를 붙인다. 원래 열은 그대로 둔다."""

    out = pd.DataFrame(index=frame.index)
    out["phase"] = frame["official_phase"].fillna("").astype(str)
    out["level"] = frame["activity_level"].astype(float)
    out["momentum"] = frame["activity_momentum"].astype(float)
    out["breadth"] = frame["confirming_domains"].astype(float)
    out["concentration"] = frame["concentration"].astype(float)
    out["negative_level_domains"] = frame["negative_level_domains"].astype(float)
    out["negative_momentum_domains"] = frame["negative_momentum_domains"].astype(float)

    for name in ("level", "momentum", "breadth", "concentration", "negative_momentum_domains"):
        out[f"d_{name}"] = out[name] - out[name].shift(weeks)

    out["change_weeks"] = weeks
    return out


def elapsed(phase: pd.Series) -> pd.Series:
    """현재 국면이 몇 주째인가. **혼자서는 신호로 쓰지 않는다.**

    "이 확장기는 오래됐으니 끝날 때가 됐다"는 도박사의 오류다. 확장기는 늙어서 죽지
    않는다는 것이 실증적으로 확립돼 있다. 여기서 재는 이유는 성숙도 신호가 실은 경과
    기간을 다시 쓴 것에 불과한지 **확인하기 위해서**다.
    """

    values = [str(item) for item in phase.tolist()]
    counts: list[int] = []
    run = 0
    previous = ""
    for value in values:
        run = run + 1 if value == previous else 1
        counts.append(run)
        previous = value
    return pd.Series(counts, index=phase.index, name="elapsed_weeks")


def runs(phase: pd.Series) -> list[dict[str, object]]:
    """국면 블록. 검정 결과 옆에 이 개수를 놓아야 표본 크기가 정직해진다."""

    values = [str(item) for item in phase.tolist()]
    weeks = [str(week) for week in phase.index]
    blocks: list[dict[str, object]] = []
    start = 0
    for position in range(1, len(values) + 1):
        if position == len(values) or values[position] != values[start]:
            blocks.append(
                {
                    "phase": values[start],
                    "start": weeks[start],
                    "end": weeks[position - 1],
                    "weeks": position - start,
                    "next_phase": values[position] if position < len(values) else None,
                }
            )
            start = position
    return blocks
