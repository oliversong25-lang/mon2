"""ALFRED 빈티지 수집기: 각 시점에 실제로 공개돼 있던 자료만 재구성한다.

FRED 최신 수정치 백테스트는 "지금 아는 값"으로 과거를 판정한다. ALFRED는 각 관측이
언제 어떤 값으로 공개돼 있었는지를 realtime 구간으로 알려주므로, 특정 빈티지 시점에
실제로 볼 수 있었던 자료만 골라낼 수 있다.

인증키는 환경변수 ``FRED_API_KEY``에서만 읽는다. 캐시·로그·오류 메시지 어디에도
남기지 않으며, 예외 문자열에서도 지운다.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

API_BASE = "https://api.stlouisfed.org/fred/"

#: FRED가 "모든 realtime 구간"을 뜻하는 데 쓰는 경계값.
EARLIEST_REALTIME = "1776-07-04"
LATEST_REALTIME = "9999-12-31"

#: 요청 간 최소 간격(초). 공개 API를 몰아치지 않는다.
MIN_REQUEST_GAP_SECONDS = float(os.environ.get("ALFRED_MIN_GAP_SECONDS", "0.6"))

ENV_KEY = "FRED_API_KEY"


class MissingCredential(RuntimeError):
    """키가 없을 때의 오류. 메시지에 키 자리 표시만 남긴다."""


@dataclass(frozen=True)
class VintageCoverage:
    """한 계열의 빈티지 아카이브가 언제부터 있는지."""

    series_id: str
    first_vintage: str
    last_vintage: str
    vintage_count: int
    observation_rows: int


def _redact(text: str, key: str) -> str:
    return text.replace(key, "<redacted>") if key else text


class AlfredCollector:
    """빈티지 관측을 받아 로컬에 캐시한다. 키는 캐시에 들어가지 않는다."""

    def __init__(self, cache_dir: Path, api_key: str | None = None) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        resolved = api_key if api_key is not None else os.environ.get(ENV_KEY, "")
        if not resolved:
            raise MissingCredential(
                f"ALFRED 접근에는 환경변수 {ENV_KEY}가 필요합니다. "
                "키를 코드나 설정 파일에 적지 말고 환경변수로만 두세요."
            )
        self._key = resolved
        self._last_request = 0.0

    # ── 요청 ────────────────────────────────────────────────────────────────

    def _call(self, path: str, **params: Any) -> dict[str, Any]:
        gap = MIN_REQUEST_GAP_SECONDS - (time.monotonic() - self._last_request)
        if gap > 0:
            time.sleep(gap)
        query = urllib.parse.urlencode({**params, "api_key": self._key, "file_type": "json"})
        url = f"{API_BASE}{path}?{query}"
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                payload: dict[str, Any] = json.load(response)
        except urllib.error.HTTPError as error:
            # 오류 본문과 URL에 키가 실려 나오지 않게 지운다.
            raise RuntimeError(
                f"ALFRED 요청 실패 ({path}): HTTP {error.code} "
                f"{_redact(error.reason or '', self._key)}"
            ) from None
        except Exception as error:
            raise RuntimeError(
                f"ALFRED 요청 실패 ({path}): {_redact(str(error), self._key)}"
            ) from None
        finally:
            self._last_request = time.monotonic()
        return payload

    # ── 커버리지 ────────────────────────────────────────────────────────────

    def coverage(self, series_id: str) -> VintageCoverage:
        """빈티지 아카이브가 언제부터 있는지 확인한다.

        아카이브 시작 이전 관측도 값은 돌아오지만 그것은 이미 수정된 값이다.
        그 구간을 실시간이라고 부르면 최신 수정치 백테스트에 이름표만 바꾸는 것이다.
        """

        vintages = self._call("series/vintagedates", series_id=series_id, sort_order="asc")
        dates = list(vintages["vintage_dates"])
        rows = self._call(
            "series/observations",
            series_id=series_id,
            realtime_start=EARLIEST_REALTIME,
            realtime_end=LATEST_REALTIME,
            limit=1,
        )
        return VintageCoverage(
            series_id=series_id,
            first_vintage=str(dates[0]),
            last_vintage=str(dates[-1]),
            vintage_count=len(dates),
            observation_rows=int(rows["count"]),
        )

    # ── 관측 ────────────────────────────────────────────────────────────────

    def realtime_observations(
        self, series_id: str, observation_start: str = "1985-01-01"
    ) -> pd.DataFrame:
        """``(관측일, realtime 시작, realtime 종료, 값)`` 긴 형식으로 받는다.

        한 번 받아 두면 어떤 빈티지 시점이든 메모리에서 잘라 쓸 수 있다.
        빈티지마다 API를 다시 부르지 않는다.
        """

        path = self.cache_dir / f"{series_id}.csv"
        if path.exists():
            frame = pd.read_csv(path)
        else:
            collected: list[dict[str, Any]] = []
            offset = 0
            while True:
                payload = self._call(
                    "series/observations",
                    series_id=series_id,
                    realtime_start=EARLIEST_REALTIME,
                    realtime_end=LATEST_REALTIME,
                    observation_start=observation_start,
                    limit=100000,
                    offset=offset,
                )
                batch = payload.get("observations", [])
                collected.extend(batch)
                offset += len(batch)
                if not batch or offset >= int(payload.get("count", 0)):
                    break
            frame = pd.DataFrame(collected)
            if frame.empty:
                raise RuntimeError(f"ALFRED가 {series_id}의 관측을 돌려주지 않았습니다")
            frame = frame[["date", "realtime_start", "realtime_end", "value"]]
            # 캐시에는 자료만 남는다. URL도 키도 저장하지 않는다.
            frame.to_csv(path, index=False)
        for column in ("date", "realtime_start", "realtime_end"):
            frame[column] = pd.to_datetime(frame[column])
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        return frame.dropna(subset=["value"]).sort_values(["date", "realtime_start"])


def slice_vintage(frame: pd.DataFrame, vintage: pd.Timestamp) -> pd.DataFrame:
    """빈티지 시점에 실제로 공개돼 있던 판본만 남긴다.

    같은 관측일에 여러 판본이 있으면 realtime 구간이 그 시점을 덮는 판본 하나만 고른다.
    """

    visible = frame[(frame["realtime_start"] <= vintage) & (frame["realtime_end"] >= vintage)]
    return visible.sort_values("realtime_start").groupby("date", as_index=False).last()


def observations_as_of(
    frames: dict[str, pd.DataFrame],
    vintage: pd.Timestamp,
    indicator_settings: dict[str, Any],
) -> pd.DataFrame:
    """여러 계열을 그 빈티지 시점 기준으로 모아 파이프라인 스키마로 만든다.

    **값**은 엄격히 시점 기준이다. 그 빈티지에 판본이 없던 관측은 아예 들어오지 않고,
    나중 판본이나 최신값으로 메우지 않는다.

    **발표일**은 설정된 지연일 추정을 그대로 쓴다. ALFRED의 ``realtime_start``를 쓰면
    안 되는 이유가 있다. 아카이브 시작 이후 한 번도 수정되지 않은 관측은 realtime 시작이
    아카이브 개시일로 기록된다. 1985년 관측이 "2013년에 발표됐다"가 되어 전체 이력이
    한 주에 몰리고 원빈도 순서가 무너진다. 발표 시점을 기존과 같은 추정으로 두면
    최신 수정치 백테스트와 **바뀌는 변수가 값 하나뿐**이 되어 수정 효과를 분리할 수 있다.

    아직 발표되지 않은 최신 관측은 빈티지 슬라이스에 없으므로, 자료의 앞단에서는
    ALFRED가 실제 공개 시점을 그대로 강제한다.
    """

    parts: list[pd.DataFrame] = []
    for series_id, frame in frames.items():
        visible = slice_vintage(frame, vintage)
        if visible.empty:
            continue
        lag = int(indicator_settings.get(series_id, {}).get("release_lag_days", 0))
        parts.append(
            pd.DataFrame(
                {
                    "indicator_id": series_id,
                    "observation_period": visible["date"],
                    "value": visible["value"],
                    "release_date": visible["date"] + pd.Timedelta(days=lag),
                    "vintage_date": vintage,
                    "source": "ALFRED",
                    "revision_status": "point_in_time",
                }
            )
        )
    if not parts:
        raise ValueError(f"{vintage.date()} 시점에 공개된 관측이 없습니다")
    return pd.concat(parts, ignore_index=True)


def provenance_table(
    coverages: list[VintageCoverage],
    frames: dict[str, pd.DataFrame],
    usable_from: pd.Timestamp,
    retrieved_at: str,
) -> pd.DataFrame:
    """지표별 출처 기록. 관측 시작일과 빈티지 시작일을 섞지 않는다.

    ``earliest_vintage_date``는 ``fred/series/vintagedates`` 응답의 첫 값이며,
    관측 시작일이나 계열 시작일이 아니다. 둘을 나란히 남겨 구분이 보이게 한다.
    """

    return pd.DataFrame(
        [
            {
                "series_id": coverage.series_id,
                "earliest_observation_date": str(frames[coverage.series_id]["date"].min().date()),
                "earliest_vintage_date": coverage.first_vintage,
                "latest_vintage_date": coverage.last_vintage,
                "vintage_count": coverage.vintage_count,
                "realtime_observation_rows": coverage.observation_rows,
                "first_date_usable_in_the_frozen_model": str(usable_from.date()),
                "vintage_endpoint": "fred/series/vintagedates",
                "observation_endpoint": "fred/series/observations"
                "?realtime_start=1776-07-04&realtime_end=9999-12-31",
                "retrieved_at": retrieved_at,
            }
            for coverage in coverages
        ]
    )


def common_vintage_start(coverages: list[VintageCoverage]) -> pd.Timestamp:
    """모든 계열이 진짜 빈티지를 갖는 가장 이른 날."""

    return max(pd.Timestamp(coverage.first_vintage) for coverage in coverages)
