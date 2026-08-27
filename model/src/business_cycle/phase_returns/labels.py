"""같은 주를 두 번 라벨링한다.

``revised``    최종 수정치로 다시 계산한 국면. "돌아보니 무슨 국면이었나"에 답한다.
``real_time``  ALFRED 경로의 ``official``. "그 시점에 알 수 있었던 국면"에 답한다.

둘의 차이가 **인식 지연의 비용**이다. 주 단위가 아니라 수익률로 표현하기 위해, 두 라벨을
같은 주에 나란히 놓을 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd

PHASES: Final[tuple[str, ...]] = ("recovery", "expansion", "slowdown", "contraction")

#: 판정을 내지 않은 주. 국면 칸에 넣지 않고 따로 센다 — 지연 비용의 일부다.
WITHHELD: Final[str] = "withheld"

REVISED_PATH: Final[str] = "outputs/four_phase_v1_1/weekly_state.csv"
REAL_TIME_PATH: Final[str] = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"


@dataclass(frozen=True)
class Labelling:
    """한 라벨링. ``phase``는 주 -> 국면, 보류 주는 ``WITHHELD``."""

    name: str
    frame: pd.DataFrame

    @property
    def weeks(self) -> list[str]:
        return [str(week) for week in self.frame.index]

    @property
    def phase(self) -> pd.Series:
        return self.frame["phase"]

    def counts(self) -> dict[str, int]:
        return {name: int((self.frame["phase"] == name).sum()) for name in (*PHASES, WITHHELD)}


def _normalise(raw: pd.Series) -> pd.Series:
    values = raw.fillna("").astype(str)
    return values.where(values.isin(PHASES), WITHHELD)


def load_revised(path: str = REVISED_PATH) -> Labelling:
    """최종 수정치 경로. 1994년부터라 국면 블록이 훨씬 많다."""

    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.Index([str(week) for week in frame.index], name="week")
    out = pd.DataFrame(index=frame.index)
    out["phase"] = _normalise(frame["official_phase"])
    out["raw_phase"] = _normalise(frame["raw_phase"])
    out["level"] = frame["activity_level"].astype(float)
    out["momentum"] = frame["activity_momentum"].astype(float)
    out["confirming_domains"] = frame["confirming_domains"].astype(int)
    out["concentration"] = frame["concentration"].astype(float)
    out["negative_level_domains"] = frame["negative_level_domains"].astype(int)
    out["negative_momentum_domains"] = frame["negative_momentum_domains"].astype(int)
    return Labelling("revised", out)


def load_real_time(path: str = REAL_TIME_PATH) -> Labelling:
    """ALFRED 시점 재구성 경로. 창은 2013-06-14부터로 짧다."""

    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.Index([str(week) for week in frame.index], name="week")
    out = pd.DataFrame(index=frame.index)
    withheld = frame["phase_status"].astype(str).eq("withheld")
    phase = _normalise(frame["official_phase"])
    out["phase"] = phase.mask(withheld, WITHHELD)
    out["raw_phase"] = _normalise(frame["raw_phase"])
    out["level"] = frame["activity_level"].astype(float)
    out["momentum"] = frame["activity_momentum"].astype(float)
    out["confirming_domains"] = frame["confirming_domains"].astype(int)
    out["concentration"] = frame["concentration"].astype(float)
    out["negative_level_domains"] = frame["negative_level_domains"].astype(int)
    out["negative_momentum_domains"] = frame["negative_momentum_domains"].astype(int)
    out["separation"] = frame["phase_separation"].astype(float)
    out["status"] = frame["phase_status"].astype(str)
    return Labelling("real_time", out)


def overlap(first: Labelling, second: Labelling) -> list[str]:
    """두 라벨링이 공통으로 덮는 주. 지연 비용은 여기서만 잰다.

    창이 다른 채로 비교하면 지연이 아니라 표본 차이를 재게 된다.
    """

    shared = set(first.weeks) & set(second.weeks)
    return sorted(shared)


def episodes(phase: pd.Series) -> dict[str, int]:
    """국면마다 **연속 구간이 몇 개**인가.

    주 수는 표본 크기를 부풀린다. 18주짜리 침체가 한 덩어리면 독립 관측은 18이 아니라 1이다.
    검정 결과 옆에 반드시 이 수를 같이 놓는다.
    """

    counts = {name: 0 for name in (*PHASES, WITHHELD)}
    previous = ""
    for value in [str(item) for item in phase.tolist()]:
        if value != previous and value in counts:
            counts[value] += 1
        previous = value
    return counts
