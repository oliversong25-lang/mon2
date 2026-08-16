"""원자료 캐시는 실패한 네트워크 호출이 정상 결과처럼 보이지 않게 한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CacheStore:
    """지표별 CSV 원문을 원자적으로 보관한다."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, indicator_id: str) -> Path:
        return self.root / f"{indicator_id}.csv"

    def read(self, indicator_id: str) -> str | None:
        target = self.path(indicator_id)
        return target.read_text(encoding="utf-8") if target.exists() else None

    def write(self, indicator_id: str, content: str) -> Path:
        target = self.path(indicator_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(target)
        return target

    def metadata_path(self, indicator_id: str) -> Path:
        return self.root / f"{indicator_id}.meta.json"

    def read_metadata(self, indicator_id: str) -> dict[str, Any] | None:
        target = self.metadata_path(indicator_id)
        if not target.exists():
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{indicator_id} 캐시 메타데이터가 객체가 아닙니다")
        return payload

    def write_metadata(self, indicator_id: str, metadata: dict[str, Any]) -> Path:
        target = self.metadata_path(indicator_id)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(target)
        return target
