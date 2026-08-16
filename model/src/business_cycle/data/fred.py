"""FRED 최신 수정치 CSV 수집기.

FRED graph CSV는 API 키가 필요 없지만 발표일·빈티지를 제공하지 않는다. 향후 ALFRED
수집기를 같은 fetch 인터페이스로 추가할 수 있도록 결과는 공통 관측 스키마로 반환한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from .availability import validate_observations
from .cache import CacheStore

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


class FredCollector:
    """네트워크 실패 시 검증된 기존 캐시를 명시적 경고와 함께 사용한다."""

    def __init__(self, cache_dir: Path, timeout: float = 30.0) -> None:
        self.cache = CacheStore(cache_dir)
        self.timeout = timeout

    def _download(self, indicator_id: str, start: str) -> str:
        response = requests.get(
            FRED_CSV_URL,
            params={"id": indicator_id, "cosd": start},
            timeout=self.timeout,
            headers={"User-Agent": "mon2-business-cycle/0.1 (research model)"},
        )
        response.raise_for_status()
        if indicator_id not in response.text.splitlines()[0]:
            raise ValueError(f"FRED 응답 열에 {indicator_id}가 없습니다")
        return response.text

    def fetch(self, indicator_ids: Iterable[str], start: str) -> tuple[pd.DataFrame, list[str]]:
        """여러 FRED 계열을 내려받아 공통 스키마로 합친다."""

        frames: list[pd.DataFrame] = []
        warnings: list[str] = []
        fetched_at = datetime.now(UTC).isoformat()
        for indicator_id in indicator_ids:
            content: str | None
            try:
                content = self._download(indicator_id, start)
                self.cache.write(indicator_id, content)
            except (requests.RequestException, ValueError) as exc:
                content = self.cache.read(indicator_id)
                if content is None:
                    raise RuntimeError(
                        f"{indicator_id} 수집 실패, 사용 가능한 캐시 없음: {exc}"
                    ) from exc
                warnings.append(f"{indicator_id}: 수집 실패로 기존 캐시 사용 ({exc})")
            if content is None:
                raise RuntimeError(f"{indicator_id}: 캐시 내용이 비어 있습니다")
            raw = pd.read_csv(StringIO(content))
            value_column = indicator_id if indicator_id in raw.columns else raw.columns[-1]
            frame = pd.DataFrame(
                {
                    "indicator_id": indicator_id,
                    "observation_period": raw.iloc[:, 0],
                    "value": raw[value_column],
                    "release_date": pd.NaT,
                    "vintage_date": pd.NaT,
                    "fetched_at": fetched_at,
                    "source": "FRED",
                    "revision_status": "latest_revision",
                    "freshness_score": 1.0,
                }
            )
            frames.append(frame)
        return validate_observations(pd.concat(frames, ignore_index=True)), warnings
