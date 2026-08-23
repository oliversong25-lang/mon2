"""§3·§6. 월간 전환점을 주간 출력과 맞대는 규약, 그리고 지연 구간대.

NBER 정점·저점은 **월** 날짜다. 그 달 안 어느 주가 전환점인지 말해 주지 않는다.
앞 단계는 저점 월의 마지막 날을 저점 "주"처럼 다뤘고, 그래서 2009년 6월 5일에 시작한
회복을 조기 이탈로 셌다. 그 판정은 규약이 만든 것이지 자료가 만든 것이 아니다.

그래서 여기서는 구간 검열(interval-censored) 규약을 1차로 쓴다.

``pre_trough_recovery``   저점 월 첫날보다 **앞선** 회복. 이것만이 진짜 조기 이탈이다.
``within_turning_month``  저점 월 안. 전환 주를 특정할 수 없으므로 조기라고 단정하지
                          않는다. 늦었다고도 하지 않는다.
``post_trough_delay``     저점 월 마지막 날보다 **뒤진** 회복. 지연은 월말부터 잰다.

저점 월 안에서 모델에 유리한 날을 골라잡지 않는다. 그 달 전체가 하나의 구간이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import pandas as pd

#: NBER 전환점 월. 회고적 라벨이며 그 시점 정보가 아니다.
PEAK_MONTHS: Final[dict[str, str]] = {
    "recession_2001": "2001-03",
    "gfc_2009": "2007-12",
    "recession_2020": "2020-02",
}

TROUGH_MONTHS: Final[dict[str, str]] = {
    "recession_2001": "2001-11",
    "gfc_2009": "2009-06",
    "recession_2020": "2020-04",
}

POSITIONS: Final[tuple[str, ...]] = (
    "pre_trough_recovery",
    "within_turning_month",
    "post_trough_delay",
)

#: §6의 사전 선언 구간대. 결과를 본 뒤에 바꾸지 않는다. 통계적으로 추정한 문턱이
#: 아니라 월간 자료를 쓰는 투자 사이클 모델의 운영 정책이다.
GREEN_MAXIMUM_WEEKS: Final[int] = 8
AMBER_MAXIMUM_WEEKS: Final[int] = 13

BANDS: Final[tuple[str, ...]] = ("green", "amber", "red")

BAND_MEANING: Final[dict[str, str]] = {
    "green": "운영상 적시다.",
    "amber": "한계를 공시한 잠정 사용에 한해 쓸 수 있다.",
    "red": "운영 기각이다.",
}


@dataclass(frozen=True)
class TurningMonth:
    """저점 월 하나를 구간으로 다룬다. 그 달 안의 특정 날을 고르지 않는다."""

    episode: str
    month: str

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.month + "-01")

    @property
    def end(self) -> pd.Timestamp:
        return self.start + pd.offsets.MonthEnd(0)

    def position(self, date: str | pd.Timestamp | None) -> str | None:
        """회복 시작일이 구간의 앞인지 안인지 뒤인지."""

        if date is None:
            return None
        moment = pd.Timestamp(date)
        if moment < self.start:
            return "pre_trough_recovery"
        if moment <= self.end:
            return "within_turning_month"
        return "post_trough_delay"

    def calendar_latency_weeks(self, date: str | pd.Timestamp | None) -> int | None:
        """월말부터 잰 달력 지연. 월 안이나 그 앞이면 0이다.

        음수를 만들지 않는 이유는 §3이다. 저점 월 안의 회복은 "빠른" 것이 아니라
        전환 주를 특정할 수 없다는 뜻이다. 지연을 음수로 적으면 없는 정확도를 주장하게
        된다.
        """

        if date is None:
            return None
        moment = pd.Timestamp(date)
        if moment <= self.end:
            return 0
        return int((moment - self.end).days // 7)

    def as_dict(self) -> dict[str, Any]:
        return {
            "episode": self.episode,
            "nber_trough_month": self.month,
            "turning_month_interval": [str(self.start.date()), str(self.end.date())],
        }


def turning_month(episode: str) -> TurningMonth:
    return TurningMonth(episode=episode, month=TROUGH_MONTHS[episode])


def peak_month_start(episode: str) -> pd.Timestamp:
    return pd.Timestamp(PEAK_MONTHS[episode] + "-01")


def band(weeks: int | None) -> str | None:
    """§6의 구간대. 경계는 정확히 8과 13이다."""

    if weeks is None:
        return None
    if weeks <= GREEN_MAXIMUM_WEEKS:
        return "green"
    if weeks <= AMBER_MAXIMUM_WEEKS:
        return "amber"
    return "red"


def usrec_secondary_comparison(usrec: pd.Series, trough: TurningMonth) -> dict[str, Any]:
    """저장소가 쓰는 주간 USREC 매핑. **2차 비교**로만 적는다.

    USREC은 월 전체에 같은 이진 라벨을 준다. 그 라벨은 그 달의 정확한 주간 전환점을
    지목하지 않으므로, 여기서 나온 마지막 침체 주를 저점 주처럼 쓰지 않는다.
    """

    index = pd.DatetimeIndex(usrec.index)
    inside = index[(index >= trough.start) & (index <= trough.end)]
    flags = usrec.reindex(inside).fillna(0).astype(int)
    after = index[index > trough.end]
    return {
        "turning_month_weeks": [str(pd.Timestamp(d).date()) for d in inside],
        "usrec_inside_turning_month": [int(v) for v in flags],
        "usrec_is_uniform_inside_the_month": bool(len(set(int(v) for v in flags)) <= 1),
        "first_week_after_month_with_usrec_zero": (
            str(pd.Timestamp(after[0]).date())
            if len(after) and int(usrec.reindex(after).fillna(0).astype(int).iloc[0]) == 0
            else None
        ),
        "role": "secondary_comparison_only",
        "note": (
            "USREC의 월 단위 이진 라벨은 그 달의 주간 전환점을 지목하지 않는다. "
            "1차 규약은 구간 검열이다."
        ),
    }
