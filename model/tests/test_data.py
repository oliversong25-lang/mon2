from __future__ import annotations

from pathlib import Path

import requests

from business_cycle.data.fred import FredCollector


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
    except RuntimeError as exc:
        assert "사용 가능한 캐시 없음" in str(exc)
    else:
        raise AssertionError("캐시 없는 네트워크 실패가 성공으로 처리됨")
