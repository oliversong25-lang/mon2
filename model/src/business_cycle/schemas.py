"""파이프라인 경계에서 사용하는 단순하고 직렬화 가능한 스키마."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class PhaseDefinition:
    code: str
    label_ko: str
    broad: str
    broad_label_ko: str
    start: float
    end: float


@dataclass
class ModelResult:
    """현재 국면 판정의 프로그램용 결과."""

    as_of_date: str
    model_version: str
    status: str
    current_phase: dict[str, Any]
    movement: dict[str, Any]
    confidence: dict[str, float]
    coordinates: dict[str, float]
    phase_probabilities: list[dict[str, Any]]
    runner_up: dict[str, Any]
    supporting_indicators: list[dict[str, Any]]
    conflicting_indicators: list[dict[str, Any]]
    data_quality: dict[str, Any]
    forecast_13w: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화 가능한 사전을 반환한다."""

        return asdict(self)


def iso_date(value: date | str) -> str:
    """날짜를 ISO 문자열로 통일한다."""

    return value.isoformat() if isinstance(value, date) else str(value)
