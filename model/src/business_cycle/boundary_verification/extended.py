"""확장 역사 — 기저 계열이 허락하는 데까지 뒤로 간다.

동결 v1.1은 1994-07부터다. 그 시작을 정한 것은 **표준화 예열 창**이고, 캐시가 1985년
부터만 담고 있어서 그보다 앞으로 갈 수 없었다.

공개 FRED CSV로 일곱 계열의 전체 역사를 받으면 **1976-07**까지 간다. NBER 침체가
3회에서 **6회**로 늘고, 그것이 C의 표본 제약을 푸는 유일한 방법이다.

## 실시간 충실도를 포기한다

여기서 묻는 것은 "분류가 타당한가"이지 "그때 알 수 있었는가"가 아니다. 그래서 최종
수정치를 그대로 쓰고, ALFRED 경로와 **분명히 갈라 둔다** — 트랙 17·18이 한 것과 같다.

## 두 가지를 반드시 함께 적어야 한다

1. 겹치는 구간에서 동결 v1.1과 100% 일치하지 않는다. 표준화 창이 더 긴 계열을 보기
   때문이며, 확장 실행은 v1.1의 **상위집합이 아니라 같은 모델에 더 긴 입력**이다.
2. 소비 도메인 구성이 1992년에 바뀐다. 그 전에는 CMRMTSPL 하나, 그 뒤로는 RRSFS가
   더해진다. 이것도 확장 실행이 균질하지 않다는 뜻이다.
"""

from __future__ import annotations

import os
import urllib.request
from typing import Any, Final

import pandas as pd

from ..config import Settings, load_baseline
from ..four_phase.alfred_audit import AS_OF
from ..four_phase.engine import FourPhaseConfig, PreparedInputs, load_config, prepare

FRED_CSV: Final[str] = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

#: 동결 모델이 쓰는 일곱 계열. 여기서 계열을 **바꾸지 않는다** — 바꾸면 다른 모델이다.
SERIES: Final[tuple[str, ...]] = (
    "PAYEMS",
    "W875RX1",
    "INDPRO",
    "CMRMTSPL",
    "RRSFS",
    "ICSA",
    "CCSA",
)

CACHE_DIR: Final[str] = "data/cache/extended"

#: 소비 도메인 구성이 바뀌는 시점. 보고서에 그대로 싣는다.
CONSUMPTION_SPLIT: Final[str] = "1992-01"


def download(cache_dir: str = CACHE_DIR) -> None:
    """전체 역사를 내려받아 **별도** 캐시에 둔다.

    동결 캐시를 덮어쓰면 v1.1 재현이 깨진다. 그래서 다른 폴더다.
    """

    os.makedirs(cache_dir, exist_ok=True)
    for series in SERIES:
        payload = urllib.request.urlopen(FRED_CSV + series, timeout=60).read()
        with open(os.path.join(cache_dir, f"{series}.csv"), "wb") as handle:
            handle.write(payload)


def observations(cache_dir: str = CACHE_DIR) -> pd.DataFrame:
    """엔진이 받는 관측 프레임 모양 그대로 만든다."""

    frames: list[pd.DataFrame] = []
    for series in SERIES:
        frame = pd.read_csv(os.path.join(cache_dir, f"{series}.csv"))
        frame.columns = ["observation_period", "value"]
        frame["observation_period"] = pd.to_datetime(frame["observation_period"])
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frame = frame.dropna()
        frame["indicator_id"] = series
        frames.append(frame)
    core = pd.concat(frames, ignore_index=True)
    # 최신 빈티지 경로다. 발표일은 쓰지 않으며 동결 경로도 같은 자리를 비워 둔다.
    core["release_date"] = pd.NaT
    core["vintage_date"] = pd.NaT
    core["fetched_at"] = "extended_history"
    core["source"] = "FRED_public"
    core["revision_status"] = "latest_revision"
    core["freshness_score"] = 1.0
    return core


def coverage(cache_dir: str = CACHE_DIR) -> list[dict[str, Any]]:
    """계열별로 어디까지 뒤로 가는지. 무엇이 시작을 막는지 보이게 한다."""

    rows: list[dict[str, Any]] = []
    for series in SERIES:
        frame = pd.read_csv(os.path.join(cache_dir, f"{series}.csv"))
        frame.columns = ["observation_period", "value"]
        rows.append(
            {
                "series": series,
                "first": str(frame["observation_period"].iloc[0]),
                "last": str(frame["observation_period"].iloc[-1]),
                "observations": int(len(frame)),
            }
        )
    return rows


def build(settings: Settings, cache_dir: str = CACHE_DIR) -> tuple[PreparedInputs, FourPhaseConfig]:
    """확장 역사 준비 입력. 설정과 임계값은 동결 v1.1 그대로다."""

    config = load_config(settings)
    core = observations(str(settings.root / cache_dir))
    prepared = prepare(core, load_baseline("candidate_h_breadth_gate", settings), AS_OF, config)
    return prepared, config


def overlap_with_frozen(extended: pd.Series, frozen: pd.Series) -> dict[str, Any]:
    """겹치는 구간에서 얼마나 일치하는가. 100%가 아니라는 것을 감추지 않는다."""

    common = [week for week in extended.index if week in set(frozen.index)]
    agree = sum(1 for week in common if str(extended[week]) == str(frozen[week]))
    return {
        "frozen_weeks": int(len(frozen)),
        "overlapping_weeks": len(common),
        "weeks_agreeing": agree,
        "agreement": round(agree / len(common), 4) if common else None,
        "identical": bool(agree == len(common)),
        "why_not_identical": (
            "표준화 창이 더 긴 계열을 보므로 같은 주의 표준화 값이 달라진다. 확장 실행은 "
            "v1.1의 상위집합이 아니라 **같은 모델에 더 긴 입력**을 준 것이다."
        ),
    }
