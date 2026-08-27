from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from business_cycle.data.fred import FredCollectionError, FredCollector


def test_fred_failure_uses_existing_cache_with_warning(tmp_path: Path, monkeypatch):
    collector = FredCollector(tmp_path)
    collector.cache.write("PAYEMS", "DATE,PAYEMS\n2020-01-01,100\n")

    def fail(_indicator_id: str, _start: str) -> str:
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(collector, "_download", fail)
    frame, warnings = collector.fetch(["PAYEMS"], "2020-01-01")
    assert frame.iloc[0]["value"] == 100
    assert warnings and "기존 캐시 사용" in warnings[0]


def test_fred_failure_without_cache_is_explicit(tmp_path: Path, monkeypatch):
    collector = FredCollector(tmp_path)

    def fail(_indicator_id: str, _start: str) -> str:
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(collector, "_download", fail)
    try:
        collector.fetch(["PAYEMS"], "2020-01-01")
    except FredCollectionError as exc:
        assert "사용 가능한 캐시 없음" in str(exc)
    else:
        raise AssertionError("캐시 없는 네트워크 실패가 성공으로 처리됨")


def test_fred_partial_success_is_cached_before_failure(tmp_path: Path, monkeypatch):
    collector = FredCollector(tmp_path, retries=1)

    def partial(indicator_id: str, _start: str) -> str:
        if indicator_id == "PAYEMS":
            return "DATE,PAYEMS\n2020-01-01,100\n"
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(collector, "_download", partial)
    try:
        collector.fetch(["PAYEMS", "INDPRO"], "2020-01-01")
    except FredCollectionError as exc:
        assert [item.indicator_id for item in exc.failures] == ["INDPRO"]
    else:
        raise AssertionError("일부 실패가 성공으로 처리됨")
    assert collector.cache.read("PAYEMS") is not None
    assert collector.cache.read_metadata("PAYEMS") is not None


def test_fred_fresh_cache_avoids_redownload(tmp_path: Path, monkeypatch):
    collector = FredCollector(tmp_path)
    collector.cache.write("PAYEMS", "DATE,PAYEMS\n2020-01-01,100\n")
    collector.cache.write_metadata(
        "PAYEMS",
        {
            "requested_start": "2020-01-01",
            "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(),
        },
    )

    def should_not_run(_indicator_id: str, _start: str) -> str:
        raise AssertionError("신선한 캐시인데 재다운로드함")

    monkeypatch.setattr(collector, "_download", should_not_run)
    frame, warnings = collector.fetch(["PAYEMS"], "2020-01-01")
    assert warnings == []
    assert frame.iloc[0]["source"] == "FRED_cache"
    assert collector.last_report[0].status == "fresh_cache"


def test_fred_download_retries_then_records_attempt(tmp_path: Path, monkeypatch):
    collector = FredCollector(tmp_path, retries=2, retry_backoff=0)
    calls = 0

    class Response:
        text = "DATE,PAYEMS\n2020-01-01,100\n"

        @staticmethod
        def raise_for_status() -> None:
            return None

    def request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise requests.ReadTimeout("slow")
        return Response()

    monkeypatch.setattr("business_cycle.data.fred.requests.get", request)
    content = collector._download("PAYEMS", "2020-01-01")
    assert "PAYEMS" in content
    assert calls == 2
    assert collector._attempt_counts["PAYEMS"] == 2
