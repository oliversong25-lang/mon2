"""현재 국면과 분리된 13주 선행 레이어의 초기 인터페이스."""

from __future__ import annotations

from typing import Any

import pandas as pd


def preliminary_leading_score(signals: pd.DataFrame | None) -> dict[str, Any]:
    """보정된 학습자료가 없으므로 확률을 지어내지 않고 상태만 반환한다."""

    if signals is None or signals.empty:
        return {"status": "not_calibrated", "available_indicators": 0}
    latest = signals.ffill().iloc[-1].dropna()
    return {
        "status": "not_calibrated",
        "available_indicators": int(latest.size),
        "preliminary_direction_score": float(latest.clip(-3, 3).mean())
        if not latest.empty
        else None,
        "warning": "규칙 기반 방향 점수이며 13주 예측확률로 보정되지 않았습니다.",
    }
