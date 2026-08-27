"""사람과 프로그램이 읽을 수 있는 결과 파일을 함께 생성한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models.confidence import score_label
from ..pipeline import PipelineRun


def validate_result(payload: dict[str, Any]) -> None:
    """필수 출력 구조와 확률 합을 검사한다."""

    required = {
        "as_of_date",
        "model_version",
        "status",
        "current_phase",
        "movement",
        "confidence",
        "coordinates",
        "phase_probabilities",
        "runner_up",
        "data_quality",
        "forecast_13w",
        "warnings",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"결과 JSON 필수 키 누락: {sorted(missing)}")
    probabilities = payload["phase_probabilities"]
    if len(probabilities) != 12:
        raise ValueError("국면 확률은 12개여야 합니다")
    total = sum(float(item["probability"]) for item in probabilities)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"국면 확률 합이 1이 아닙니다: {total}")


def markdown_summary(payload: dict[str, Any]) -> str:
    """요구된 순서로 현재 판정 요약을 만든다."""

    confidence = payload["confidence"]
    runner = payload["runner_up"]
    support = payload.get("supporting_indicators") or []
    conflict = payload.get("conflicting_indicators") or []
    lines = [
        f"# 미국 경기국면 판정 — {payload['as_of_date']}",
        "",
        f"현재 경기국면: {payload['current_phase']['label_ko']}",
        f"지난주 대비: {payload['movement']['from_previous_week']}",
        f"대국면 확실성: {confidence['broad']:.1f}/100 — {score_label(confidence['broad'])}",
        f"세부국면 확실성: {confidence['detail']:.1f}/100 — {score_label(confidence['detail'])}",
        f"데이터 신뢰도: {confidence['data']:.1f}/100 — {score_label(confidence['data'])}",
        "",
        f"2순위 후보: {runner['label_ko']}",
        f"확률 차이: {runner['gap_percentage_points']:.1f}%p",
        f"현재 이동 방향: {payload['movement']['direction']}",
        "",
        "## 판정을 지지하는 지표",
        *(f"- {item['indicator_id']}: {item['contribution']:+.4f}" for item in support),
        *(["- 뚜렷한 양의 기여 없음"] if not support else []),
        "",
        "## 판정에 반대하는 지표",
        *(f"- {item['indicator_id']}: {item['contribution']:+.4f}" for item in conflict),
        *(["- 뚜렷한 음의 기여 없음"] if not conflict else []),
        "",
        "## 데이터와 모델의 주의사항",
        *(f"- {warning}" for warning in payload.get("warnings", [])),
        f"- 향후 13주 확률 상태: {payload['forecast_13w']['status']}",
    ]
    return "\n".join(lines) + "\n"


def write_reports(run: PipelineRun, output_dir: Path, prefix: str = "nowcast") -> dict[str, Path]:
    """JSON·CSV·Markdown 세 파일을 원자적으로 작성한다."""

    output_dir.mkdir(parents=True, exist_ok=True)
    payload = run.result.to_dict()
    validate_result(payload)
    paths = {
        "json": output_dir / f"{prefix}.json",
        "csv": output_dir / f"{prefix}.csv",
        "markdown": output_dir / f"{prefix}.md",
    }
    contents = {
        "json": json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        "csv": run.history.reset_index(names="date").to_csv(index=False),
        "markdown": markdown_summary(payload),
    }
    for key, path in paths.items():
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(contents[key], encoding="utf-8", newline="\n")
        temporary.replace(path)
    return paths
