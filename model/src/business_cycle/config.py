"""YAML 설정 로딩과 모델 경로 관리."""

from __future__ import annotations

from dataclasses import dataclass, replace
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


def _baseline_model(name: str, document: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """선언적 baseline 문서를 파이프라인이 읽는 평면 키로 옮긴다."""

    model = {
        key: value
        for key, value in base.items()
        if key
        not in {
            "trend_span_weeks",
            "trend_horizon_years",
            "standardization_method",
            "standardization_horizon_years",
            "standardization_min_history_years",
            "robust_clip",
            "maturity",
            "coordinate_standardization_method",
            "coordinate_standardization_horizon_years",
            "coordinate_standardization_min_history_years",
            "coordinate_full_history_years",
            "contraction_breadth_gate",
            "systemic_shock_override",
        }
    }
    trend = document.get("trend", {})
    if ("horizon_years" in trend) == ("span_weeks" in trend):
        raise ValueError(f"[{name}] trend는 horizon_years 또는 span_weeks 중 하나여야 합니다")
    if "horizon_years" in trend:
        model["trend_horizon_years"] = float(trend["horizon_years"])
    else:
        model["trend_span_weeks"] = int(trend["span_weeks"])

    standardization = document.get("standardization", {})
    # 현재 관측을 기준분포에 넣는 설정은 미래정보 누출이다. 조용히 무시하지 않고 거부한다.
    if bool(standardization.get("include_current_observation", False)):
        raise ValueError(f"[{name}] include_current_observation은 true일 수 없습니다 (누출)")
    method = str(standardization.get("method", "expanding"))
    robust_enabled = bool(document.get("robust", {}).get("enabled", False))
    if method == "expanding":
        if robust_enabled:
            raise ValueError(f"[{name}] expanding 표준화에는 robust 제한을 붙이지 않습니다")
        model["standardization_method"] = "expanding_mean_std"
    elif method == "rolling":
        model["standardization_method"] = (
            "rolling_median_mad" if robust_enabled else "rolling_mean_std"
        )
        model["standardization_horizon_years"] = float(standardization["window_years"])
        if "minimum_history_years" in standardization:
            model["standardization_min_history_years"] = float(
                standardization["minimum_history_years"]
            )
    else:
        raise ValueError(f"[{name}] 지원하지 않는 표준화 방식: {method}")
    if robust_enabled:
        model["robust_clip"] = float(document["robust"]["clip"])

    maturity = document.get("maturity", {})
    model["maturity"] = {
        "enabled": bool(maturity.get("enabled", False)),
        "exclude_years": float(maturity.get("exclude_years", 5.0)),
        "full_weight_years": float(maturity.get("full_weight_years", 10.0)),
    }

    coordinates = document.get("coordinates", {})
    coordinate_method = str(coordinates.get("standardization", "expanding"))
    if coordinate_method not in {"expanding", "rolling", "scale_only", "none"}:
        raise ValueError(f"[{name}] 지원하지 않는 좌표 표준화 방식: {coordinate_method}")
    model["coordinate_standardization_method"] = {
        "rolling": "rolling_mean_std",
        "expanding": "expanding_mean_std",
        "scale_only": "scale_only",
        "none": "none",
    }[coordinate_method]
    model["coordinate_standardization_horizon_years"] = float(coordinates.get("window_years", 10.0))
    if "minimum_history_years" in coordinates:
        model["coordinate_standardization_min_history_years"] = float(
            coordinates["minimum_history_years"]
        )
    gate = document.get("contraction_breadth_gate")
    if gate is not None:
        model["contraction_breadth_gate"] = {
            "enabled": bool(gate.get("enabled", False)),
            "minimum_domains": float(gate["minimum_domains"]),
        }

    override = document.get("systemic_shock_override")
    if override is not None:
        if gate is None or not bool(gate.get("enabled", False)):
            raise ValueError(f"[{name}] systemic_shock_override는 폭 게이트가 있어야 합니다")
        model["systemic_shock_override"] = {
            "enabled": bool(override.get("enabled", False)),
            "minimum_core_negative_domains": int(override["minimum_core_negative_domains"]),
            "core_level": float(override["core_level"]),
            "leave_one_indicator_level": float(override["leave_one_indicator_level"]),
            "leave_one_domain_level": float(override["leave_one_domain_level"]),
            "minimum_ungated_contraction_probability": float(
                override["minimum_ungated_contraction_probability"]
            ),
            "require_dynamic_agreement": bool(override.get("require_dynamic_agreement", True)),
        }

    if "full_history_years" in coordinates:
        model["coordinate_full_history_years"] = float(coordinates["full_history_years"])
    return model


def load_baseline(name: str, settings: Settings | None = None) -> Settings:
    """`configs/baselines.yaml`의 이름 하나를 완전한 Settings로 만든다.

    legacy와 corrected를 코드 안에서 분기하지 않고 파일에서 분리하기 위한 통로다.
    """

    base = settings or load_settings()
    document = _read_yaml(base.root / "configs" / "baselines.yaml")
    if name not in document:
        raise KeyError(f"baselines.yaml에 없는 설정: {name} (가능: {sorted(document)})")
    return replace(base, model=_baseline_model(name, document[str(name)], base.model))


def available_baselines(settings: Settings | None = None) -> dict[str, str]:
    """설정 이름과 설명을 반환한다."""

    base = settings or load_settings()
    document = _read_yaml(base.root / "configs" / "baselines.yaml")
    return {str(key): str(value.get("description", "")) for key, value in document.items()}


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
