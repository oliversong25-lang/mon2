"""공식 FRED 최신 수정치 CSV 수집기."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .availability import validate_observations
from .cache import CacheStore

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_USER_AGENT = "curl/8.16.0 (mon2-business-cycle/0.1; official FRED CSV validation)"


@dataclass(frozen=True)
class SeriesFetchRecord:
    indicator_id: str
    status: str
    source: str
    fetched_at: str
    observation_start: str | None
    observation_end: str | None
    observation_count: int
    attempts: int
    error: str | None = None


class FredCollectionError(RuntimeError):
    """일부 성공분을 캐시에 보존한 뒤 실패 지표를 한꺼번에 알린다."""

    def __init__(self, failures: list[SeriesFetchRecord], records: list[SeriesFetchRecord]) -> None:
        self.failures = failures
        self.records = records
        details = "; ".join(f"{item.indicator_id}: {item.error}" for item in failures)
        super().__init__(f"FRED 수집 실패 지표 {len(failures)}개, 사용 가능한 캐시 없음: {details}")


class FredCollector:
    """재시도와 지표별 캐시로 한 번의 지연이 전체 수집을 잃게 하지 않는다."""

    def __init__(
        self,
        cache_dir: Path,
        timeout: float | tuple[float, float] = (10.0, 60.0),
        retries: int = 3,
        retry_backoff: float = 1.0,
        cache_max_age_hours: float = 24.0,
    ) -> None:
        self.cache = CacheStore(cache_dir)
        self.timeout = timeout
        self.retries = max(1, retries)
        self.retry_backoff = max(0.0, retry_backoff)
        self.cache_max_age_hours = max(0.0, cache_max_age_hours)
        self.last_report: list[SeriesFetchRecord] = []
        self._attempt_counts: dict[str, int] = {}

    def _download(self, indicator_id: str, start: str) -> str:
        errors: list[str] = []
        for attempt in range(1, self.retries + 1):
            try:
                response = requests.get(
                    FRED_CSV_URL,
                    params={"id": indicator_id, "cosd": start},
                    timeout=self.timeout,
                    # FRED의 Akamai 경로가 Python 기본 식별자에 응답을 지연시키므로,
                    # 정상 처리되는 curl 호환 식별자 뒤에 프로젝트 용도를 명시한다.
                    headers={"User-Agent": FRED_USER_AGENT},
                )
                response.raise_for_status()
                if indicator_id not in response.text.splitlines()[0]:
                    raise ValueError(f"FRED 응답 안에 {indicator_id}가 없습니다")
                self._attempt_counts[indicator_id] = attempt
                return response.text
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"시도 {attempt}: {type(exc).__name__}: {exc}")
                if attempt < self.retries:
                    time.sleep(self.retry_backoff * (2 ** (attempt - 1)))
        raise RuntimeError(" | ".join(errors))

    def _cache_is_fresh(self, indicator_id: str, start: str) -> bool:
        if self.cache.read(indicator_id) is None:
            return False
        metadata = self.cache.read_metadata(indicator_id)
        if metadata is None or str(metadata.get("requested_start", "")) > start:
            return False
        raw_fetched_at = metadata.get("fetched_at")
        if not isinstance(raw_fetched_at, str):
            return False
        fetched_at = pd.Timestamp(raw_fetched_at)
        if pd.isna(fetched_at):
            return False
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.tz_localize("UTC")
        age_hours = float((pd.Timestamp.now(tz="UTC") - fetched_at).total_seconds() / 3600.0)
        return age_hours <= self.cache_max_age_hours

    @staticmethod
    def _series_record(
        indicator_id: str,
        content: str,
        *,
        status: str,
        source: str,
        fetched_at: str,
        attempts: int,
        error: str | None = None,
    ) -> tuple[pd.DataFrame, SeriesFetchRecord]:
        raw = pd.read_csv(StringIO(content))
        value_column = indicator_id if indicator_id in raw.columns else raw.columns[-1]
        periods = pd.to_datetime(raw.iloc[:, 0], errors="coerce").dropna()
        frame = pd.DataFrame(
            {
                "indicator_id": indicator_id,
                "observation_period": raw.iloc[:, 0],
                "value": raw[value_column],
                "release_date": pd.NaT,
                "vintage_date": pd.NaT,
                "fetched_at": fetched_at,
                "source": source,
                "revision_status": "latest_revision",
                "freshness_score": 1.0,
            }
        )
        record = SeriesFetchRecord(
            indicator_id=indicator_id,
            status=status,
            source=source,
            fetched_at=fetched_at,
            observation_start=None if periods.empty else periods.min().date().isoformat(),
            observation_end=None if periods.empty else periods.max().date().isoformat(),
            observation_count=int(pd.to_numeric(raw[value_column], errors="coerce").notna().sum()),
            attempts=attempts,
            error=error,
        )
        return frame, record

    def fetch(self, indicator_ids: Iterable[str], start: str) -> tuple[pd.DataFrame, list[str]]:
        """모든 지표를 시도하고 성공분을 보존한 뒤 실패 목록을 한꺼번에 알린다."""

        frames: list[pd.DataFrame] = []
        records: list[SeriesFetchRecord] = []
        failures: list[SeriesFetchRecord] = []
        warnings: list[str] = []
        fetched_at = datetime.now(UTC).isoformat()
        for indicator_id in indicator_ids:
            content: str | None = None
            source = "FRED"
            status = "downloaded"
            attempts = self.retries
            error: str | None = None
            if self._cache_is_fresh(indicator_id, start):
                content = self.cache.read(indicator_id)
                source = "FRED_cache"
                status = "fresh_cache"
                attempts = 0
            else:
                try:
                    content = self._download(indicator_id, start)
                    attempts = self._attempt_counts.get(indicator_id, self.retries)
                    self.cache.write(indicator_id, content)
                except (requests.RequestException, RuntimeError, ValueError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    content = self.cache.read(indicator_id)
                    if content is None:
                        failure = SeriesFetchRecord(
                            indicator_id=indicator_id,
                            status="failed",
                            source="FRED",
                            fetched_at=fetched_at,
                            observation_start=None,
                            observation_end=None,
                            observation_count=0,
                            attempts=self.retries,
                            error=error,
                        )
                        failures.append(failure)
                        records.append(failure)
                        continue
                    source = "FRED_cache"
                    status = "stale_cache_fallback"
                    warnings.append(f"{indicator_id}: 수집 실패로 기존 캐시 사용 ({error})")
            if content is None:
                continue
            frame, record = self._series_record(
                indicator_id,
                content,
                status=status,
                source=source,
                fetched_at=fetched_at,
                attempts=attempts,
                error=error,
            )
            frames.append(frame)
            records.append(record)
            if status == "downloaded":
                self.cache.write_metadata(
                    indicator_id,
                    {**asdict(record), "url": FRED_CSV_URL, "requested_start": start},
                )
        self.last_report = records
        if failures:
            raise FredCollectionError(failures, records)
        if not frames:
            raise RuntimeError("FRED에서 사용할 수 있는 지표를 하나도 확보하지 못했습니다")
        return validate_observations(pd.concat(frames, ignore_index=True)), warnings

    def report_payload(self) -> list[dict[str, Any]]:
        """수집 출처·시각·기간·실패를 JSON 직렬화 가능한 구조로 반환한다."""

        return [asdict(record) for record in self.last_report]
