"""YAML 설정 로딩과 모델 경로 관리."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    """검증된 세 설정 문서를 한 객체로 보관한다."""

    indicators: dict[str, Any]
    model: dict[str, Any]
    transitions: dict[str, Any]
    root: Path


def default_root() -> Path:
    """설치 방식과 무관한 model 디렉터리를 반환한다."""

    return Path(__file__).resolve().parents[2]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"설정 파일이 없습니다: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"설정 루트는 객체여야 합니다: {path}")
    return loaded


def load_settings(config_dir: Path | None = None) -> Settings:
    """기본 또는 지정한 디렉터리의 설정을 읽고 필수 제약을 검사한다."""

    root = default_root()
    directory = config_dir or root / "configs"
    indicators = _read_yaml(directory / "indicators.yaml")
    model = _read_yaml(directory / "model.yaml")
    transitions = _read_yaml(directory / "transitions.yaml")
    phases = transitions.get("phases", [])
    if len(phases) != 12:
        raise ValueError("국면 설정은 정확히 12개여야 합니다")
    weights = [float(v["weight"]) for v in indicators.get("indicators", {}).values()]
    if not weights or abs(sum(weights) - 1.0) > 1e-9:
        raise ValueError("경기 본체 초기 가중치 합은 1이어야 합니다")
    return Settings(indicators, model, transitions, root)
