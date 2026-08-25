"""분석 표본을 한 곳에서 정의한다.

두 질문을 섞지 않기 위해서다.

``taxonomy``   국면 분류에 의미가 있는가. 실시간 라벨이 필요 없다. 긴 역사를 쓴다.
``usability``  실무에서 쓸 수 있는가. 실시간 창에 갇혀 있고 넓힐 수 없다.

앞의 것이 실패하면 뒤의 것은 물어볼 필요가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

import pandas as pd

from .labels import PHASES, WITHHELD, Labelling, episodes

#: 코로나 구간. 이 한 에피소드가 결과를 혼자 만드는지 보려고 뺀다.
COVID_START: Final[str] = "2020-01-01"
COVID_END: Final[str] = "2020-12-31"

#: 세계 금융위기 구간. 코로나만 빼는 것이 자의적이지 않은지 대조한다.
GFC_START: Final[str] = "2008-01-01"
GFC_END: Final[str] = "2009-12-31"

#: 전이 게이트(raw:on)를 건 경로. 채터링이 줄면 판별력이 오르는지 본다.
GATED_PATH: Final[str] = "outputs/transition_gate/recommended_weekly_path.csv"


@dataclass(frozen=True)
class Sample:
    """한 표본. ``question``으로 두 질문을 갈라 둔다."""

    name: str
    question: str
    phase: pd.Series
    weeks: list[str]
    note: str = ""
    #: 주를 도려낸 표본에서는 복리 경로가 끊긴다. 순환매를 돌리지 않는다.
    contiguous: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def profile(self) -> dict[str, Any]:
        selected = self.phase.reindex(self.weeks)
        return {
            "sample": self.name,
            "question": self.question,
            "weeks": len(self.weeks),
            "first_week": self.weeks[0] if self.weeks else None,
            "last_week": self.weeks[-1] if self.weeks else None,
            "phase_weeks": {name: int((selected == name).sum()) for name in (*PHASES, WITHHELD)},
            "phase_episodes": episodes(selected),
            "contiguous": self.contiguous,
            "note": self.note,
        }


def _without(weeks: list[str], start: str, end: str) -> list[str]:
    return [week for week in weeks if not start <= week <= end]


def load_gated(path: str) -> pd.Series:
    """전이 게이트를 건 실시간 경로의 국면."""

    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.Index([str(week) for week in frame.index], name="week")
    phase = frame["gated_phase"].fillna("").astype(str)
    return phase.where(phase.isin(PHASES), WITHHELD)


def build(
    revised: Labelling, real_time: Labelling, overlap_weeks: list[str], gated: pd.Series | None
) -> list[Sample]:
    """보고서가 쓰는 표본 전체."""

    long_weeks = revised.weeks
    samples = [
        Sample(
            name="revised_long",
            question="taxonomy",
            phase=revised.phase,
            weeks=long_weeks,
            note="최종 수정치, 1994년부터. 국면 분류 자체를 묻는 기본 표본이다.",
        ),
        Sample(
            name="revised_overlap",
            question="latency",
            phase=revised.phase,
            weeks=overlap_weeks,
            note="지연 비용을 재기 위해 실시간 창에 맞춘 수정치 라벨.",
        ),
        Sample(
            name="real_time_overlap",
            question="usability",
            phase=real_time.phase,
            weeks=overlap_weeks,
            note="ALFRED 시점 재구성. 그때 알 수 있었던 것만 담는다.",
        ),
        Sample(
            name="revised_long_ex_covid",
            question="taxonomy",
            phase=revised.phase,
            weeks=_without(long_weeks, COVID_START, COVID_END),
            note="2020년을 뺀다. 한 에피소드가 결과를 혼자 만드는지 보는 검사다.",
            contiguous=False,
        ),
        Sample(
            name="revised_long_ex_gfc",
            question="taxonomy",
            phase=revised.phase,
            weeks=_without(long_weeks, GFC_START, GFC_END),
            note="2008~09년을 뺀다. 코로나만 빼는 것이 자의적이지 않은지 대조한다.",
            contiguous=False,
        ),
        Sample(
            name="revised_long_ex_both",
            question="taxonomy",
            phase=revised.phase,
            weeks=_without(_without(long_weeks, COVID_START, COVID_END), GFC_START, GFC_END),
            note="두 위기를 모두 뺀다.",
            contiguous=False,
        ),
        Sample(
            name="real_time_ex_covid",
            question="usability",
            phase=real_time.phase,
            weeks=_without(overlap_weeks, COVID_START, COVID_END),
            note="실시간 창에서 2020년을 빼면 침체 주가 하나도 남지 않는다.",
            contiguous=False,
        ),
    ]
    if gated is not None:
        samples.append(
            Sample(
                name="real_time_gated",
                question="usability",
                phase=gated,
                weeks=overlap_weeks,
                note="전이 게이트(raw:on)를 건 실시간 경로. 채터링을 줄이면 판별력이 오르는가.",
            )
        )
    return samples
