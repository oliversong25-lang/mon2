"""금리 통제 — 이 단계에서 가장 중요한 대조군.

가치 프리미엄이 금리·신용 여건과 함께 움직인다는 것은 알려져 있다. 기간 스프레드를
넣었더니 사라지는 국면 효과는 **국면 옷을 입은 금리 효과**이고, 그렇다면 국면 모델은
수익률 곡선 계열이 이미 주는 것 이상을 더하지 못한다는 뜻이다.

세 모형을 같은 표본에서 적합시킨다.

    spread only     기간 스프레드 수준 + 변화
    phase only      국면 더미
    both            둘 다

`both`가 `spread only`보다 얼마나 나은지가 **국면의 순수 기여**다. 그 증분을 국면
라벨 순환 이동으로 검정한다 — 겹치는 전방창 때문에 F 검정을 쓸 수 없다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.labels import PHASES
from .conditional import MINIMUM_SHIFT

#: 기준 국면. 더미에서 빠지며 절편이 이 국면의 평균이 된다.
BASE_PHASE: Final[str] = "expansion"


def _design(phase: np.ndarray, spread: np.ndarray, change: np.ndarray, kind: str) -> np.ndarray:
    rows = phase.size
    columns = [np.ones(rows)]
    if kind in ("phase", "both"):
        for name in PHASES:
            if name == BASE_PHASE:
                continue
            columns.append((phase == name).astype(float))
    if kind in ("spread", "both"):
        columns.append(spread)
        columns.append(change)
    return np.column_stack(columns)


def _fit(design: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, float]:
    coefficients, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = target - design @ coefficients
    total = target - target.mean()
    r_squared = 1.0 - float(residual @ residual) / float(total @ total)
    return coefficients, r_squared


def _names(kind: str) -> list[str]:
    names = ["intercept"]
    if kind in ("phase", "both"):
        names += [f"phase[{name}]" for name in PHASES if name != BASE_PHASE]
    if kind in ("spread", "both"):
        names += ["term_spread", "term_spread_change"]
    return names


def run(phase: pd.Series, forward: pd.Series, rates: pd.DataFrame) -> dict[str, Any]:
    """국면 효과가 금리 통제를 견디는가."""

    frame = pd.DataFrame(
        {
            "phase": phase,
            "forward": forward,
            "spread": rates["term_spread"],
            "change": rates["term_spread_change"],
        }
    ).dropna()
    frame = frame[frame["phase"].isin(PHASES)]
    if len(frame) < 60:
        return {"usable": False, "observations": int(len(frame))}

    labels_array = frame["phase"].to_numpy()
    target = frame["forward"].to_numpy(dtype=float)
    spread = frame["spread"].to_numpy(dtype=float)
    change = frame["change"].to_numpy(dtype=float)

    models: dict[str, Any] = {}
    for kind in ("spread", "phase", "both"):
        design = _design(labels_array, spread, change, kind)
        coefficients, r_squared = _fit(design, target)
        models[kind] = {
            "r_squared": round(r_squared, 5),
            "coefficients": {
                name: round(float(value), 6)
                for name, value in zip(_names(kind), coefficients, strict=True)
            },
        }

    incremental = models["both"]["r_squared"] - models["spread"]["r_squared"]

    # 국면 라벨만 순환 이동시켜 증분 결정계수의 귀무분포를 만든다. 금리 계열은 그대로
    # 두므로, 재는 것은 정확히 "국면이 금리 위에 더하는 것"이다.
    weeks = labels_array.size
    offsets = [k for k in range(weeks) if MINIMUM_SHIFT <= k <= weeks - MINIMUM_SHIFT]
    draws: list[float] = []
    for offset in offsets:
        rolled = np.roll(labels_array, offset)
        design = _design(rolled, spread, change, "both")
        _, r_squared = _fit(design, target)
        draws.append(r_squared - models["spread"]["r_squared"])
    array = np.array(draws)

    survives = bool(
        (int((array >= incremental).sum()) + 1) / (array.size + 1) <= 0.05 and incremental > 0
    )

    return {
        "usable": True,
        "observations": int(len(frame)),
        "models": models,
        "phase_coefficients_without_the_rate_control": models["phase"]["coefficients"],
        "phase_coefficients_with_the_rate_control": {
            name: value
            for name, value in models["both"]["coefficients"].items()
            if name.startswith("phase[")
        },
        "incremental_r_squared_of_phase_over_the_term_spread": round(incremental, 5),
        "incremental_r_squared_null_median": round(float(np.median(array)), 5),
        "incremental_r_squared_p_value": round(
            float((int((array >= incremental).sum()) + 1) / (array.size + 1)), 4
        ),
        "phase_adds_something_beyond_the_term_spread": survives,
    }


def coefficient_shrinkage(result: dict[str, Any]) -> dict[str, Any]:
    """통제를 넣었을 때 국면 계수가 얼마나 줄어드는가. 줄어드는 정도가 곧 금리 지분이다."""

    if not result.get("usable"):
        return {"usable": False}
    without = result["phase_coefficients_without_the_rate_control"]
    with_control = result["phase_coefficients_with_the_rate_control"]
    rows = []
    for name, value in without.items():
        if not name.startswith("phase["):
            continue
        after = float(with_control.get(name, float("nan")))
        rows.append(
            {
                "term": name,
                "without_control": round(float(value), 6),
                "with_control": round(after, 6),
                "retained_share": (
                    round(after / float(value), 3) if abs(float(value)) > 1e-12 else None
                ),
                "sign_flips": bool(float(value) * after < 0),
            }
        )
    return {"usable": True, "rows": rows}
