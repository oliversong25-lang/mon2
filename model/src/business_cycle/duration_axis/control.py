"""금리·변동성 대조 — 메커니즘이 정한 대조.

제안된 것이 할인율 메커니즘이므로 대조도 금리와 변동성이어야 한다. 트랙 25가 정착시킨
쌍을 같은 방법으로 만든다.

## 종속변수를 여기서 이름 붙인다

사전 명세는 이 단계의 지평선(3개월)과 문턱(p<=0.05)은 못박았지만 **종속변수를 정확히
이름 붙이지 않았다.** 그것은 내 사전 명세의 빈틈이다. 자연스러운 선택은 하나뿐이라
— 가장 긴 통에서 가장 짧은 통을 뺀 전방 3개월 수익 — 그것을 쓰되, 미리 못박지 않았다는
사실을 보고서에 적고 이 단계 전체를 **기록 전용**으로 둔다.

어차피 천장 관문이 막혔으므로 이 단계는 판정하지 않는다. 그래도 숫자는 남긴다 — 트랙
23이 순환매 숫자를 결론 없이 남긴 것과 같다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..variance_control.control import compare as _compare

#: 월간 격자에서의 되돌아보기. 규칙이 이름 붙인 숫자를 이 격자의 기간으로 읽는다.
LOOKBACKS: Final[tuple[int, ...]] = (4, 13, 26)


def realised_variance(monthly: pd.Series, lookback: int) -> pd.Series:
    """t월까지 ``lookback``개월의 평균 제곱수익. t월을 포함한다 — 가격은 지연이 없다."""

    squared = pd.Series(monthly.to_numpy(dtype=float) ** 2, index=monthly.index)
    return squared.rolling(lookback, min_periods=lookback).mean()


def build_control(
    market: pd.Series, spread: pd.Series, lookback: int, keep_spread: bool = True
) -> pd.DataFrame:
    """실현분산과 제곱근을 함께, 그리고 기간 스프레드. 트랙 25와 같은 모양이다."""

    variance = realised_variance(market, lookback)
    frame = pd.DataFrame(
        {
            f"realised_variance_{lookback}m": variance,
            f"realised_volatility_{lookback}m": np.sqrt(variance.clip(lower=0.0)),
        }
    )
    if keep_spread:
        frame["term_spread"] = spread.astype(float)
    return frame


def long_minus_short(axis: pd.DataFrame, horizon: int) -> pd.Series:
    """가장 긴 통 - 가장 짧은 통의 전방 h개월 수익.

    축의 열이 긴 쪽에서 짧은 쪽으로 정렬돼 있으므로 첫 열에서 마지막 열을 뺀다.
    """

    columns = list(axis.columns)
    spread = axis[columns[0]] - axis[columns[-1]]
    compounded = pd.Series(np.log1p(spread.to_numpy(dtype=float)), index=axis.index)
    rolled = compounded.rolling(horizon).sum().shift(-horizon)
    return pd.Series(np.expm1(rolled.to_numpy()), index=axis.index, name="long_minus_short")


def lookback_table(
    phase: pd.Series,
    market: pd.Series,
    spread: pd.Series,
    target: pd.Series,
    stride: int,
) -> list[dict[str, Any]]:
    """되돌아보기마다 대조 단독 설명력. 규칙이 가장 강한 것을 고르게 한다."""

    rows: list[dict[str, Any]] = []
    for lookback in LOOKBACKS:
        control = build_control(market, spread, lookback)
        read = _compare(phase, control, target, f"lookback {lookback}m", stride=stride)
        rows.append(
            {
                "lookback_weeks": lookback,
                "control_only_r_squared": read.get("control_only_r_squared"),
                "phase_only_r_squared": read.get("phase_only_r_squared"),
                "both_r_squared": read.get("both_r_squared"),
                "incremental_r_squared_of_phase": read.get("incremental_r_squared_of_phase"),
                "null_p": read.get("null_p"),
                "periods": read.get("weeks"),
            }
        )
    return rows


def compare(
    phase: pd.Series, control: pd.DataFrame, target: pd.Series, label: str, stride: int
) -> dict[str, Any]:
    """트랙 25의 비교를 그대로 부른다. 다시 짜면 구현 차이가 섞인다."""

    return _compare(phase, control, target, label, stride=stride)


def choose_lookback(table: list[dict[str, Any]]) -> dict[str, Any]:
    """사전 명세의 선택 규칙을 구현한다 — 단독 설명력이 가장 강한 창.

    규칙 자체는 `prespec.CONTROL_SELECTION`에 결과보다 먼저 커밋돼 있다. 구현을 여기 두는
    이유는, 결과가 나온 뒤에 규칙 파일에 함수를 더하면 그 파일이 사전 명세가 아니게 되기
    때문이다.
    """

    usable = [row for row in table if row.get("control_only_r_squared") is not None]
    if not usable:
        return {"chosen": LOOKBACKS[0], "candidates": table}
    best = max(usable, key=lambda row: float(row["control_only_r_squared"]))
    weakest = min(usable, key=lambda row: float(row["control_only_r_squared"]))
    return {
        "chosen": int(best["lookback_weeks"]),
        "chosen_control_only_r_squared": best["control_only_r_squared"],
        "weakest": int(weakest["lookback_weeks"]),
        "weakest_control_only_r_squared": weakest["control_only_r_squared"],
        "candidates": table,
    }
