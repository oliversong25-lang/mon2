"""선택 절차를 p와 함께 적는다.

지속 길이 8개와 중립대 배수 6개를 쓸어 최고를 골랐다. 그 다음 확장 역사에서 p=0.0458이
나왔다. 그 숫자를 그냥 "50년 역사에서 5% 유의"라고 적으면 **격자를 쓸었다는 사실이
사라진다.** 트랙 20에서 "가치 대리변수를 몇 개 시도했는지 세서 보정하라"고 한 것과 같은
상황이다.

## 완전한 p-해킹은 아니다 — 방어를 그대로 적는다

1. 자연 실험이 **게이트 종류를 고르기 전에** "지속이 우선"을 가리켰다. 지속을 흔든 것은
   자료를 본 뒤의 착상이 아니다.
2. 평탄역 선택 기준(`PLATEAU_TOLERANCE`, `MINIMUM_BLOCKS`)이 격자를 돌리기 전에 있었다.
3. 17주는 **B에서** 골랐고 C는 그 뒤에 돌렸다. C의 p가 17주를 고르게 한 것이 아니다.

그래도 방어가 보정을 대신하지는 않는다. 0.0458은 문턱 바로 아래라 어떤 보정을 해도
넘어간다. 그 사실을 감추지 않는다.

## 두 가지 보정을 함께 낸다

``bonferroni``  명목 p x 격자 크기. 가장 보수적이고 가장 단순하다.
``max_statistic``
    이동마다 격자 전체를 다시 훑어 **가장 작은 p**를 뽑아 만든 분포(min-P). 격자 안의
    설정들이 서로 강하게 상관돼 있으므로 본페로니보다 덜 보수적이고, 이 상황에 맞는
    보정이다. 같은 이동 오프셋을 모든 설정에 쓰기 때문에 설정 간 상관이 귀무분포에
    그대로 보존된다.

    원 통계량의 최댓값을 쓰지 않는 이유는 함수 주석에 적어 두었다 — 설정마다 후퇴기
    주수가 달라 척도가 다르고, 그대로 견주면 보정이 명목 p보다 **작게** 나온다.
"""

from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

from ..phase_returns.significance import MINIMUM_SHIFT
from ..slowdown_boundary.natural import PHASES

#: 어느 국면의 통계량을 최댓값 보정의 대상으로 삼는가. 고른 것은 후퇴기 판별력이다.
TARGET_PHASE: Final[str] = "slowdown"


def _phase_statistic(masks: np.ndarray, filled: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """`metrics.discrimination`과 같은 통계량. 여기서는 이동별로 꺼내 써야 해서 분리한다."""

    with np.errstate(invalid="ignore", divide="ignore"):
        overall = np.where(valid.sum(axis=0) > 0, filled.sum(axis=0) / valid.sum(axis=0), np.nan)
        total = masks @ filled
        count = masks @ valid
        means = np.where(count > 0, total / np.where(count == 0, 1.0, count), np.nan)
    deviation = (means - overall) ** 2
    return np.array(
        [float(np.nanmean(row)) if np.isfinite(row).any() else float("nan") for row in deviation]
    )


def shift_draws(phase: pd.Series, relative: pd.DataFrame) -> dict[str, Any]:
    """한 설정의 관측값과 이동 귀무분포 전체.

    `metrics.discrimination`은 p만 돌려준다. 최댓값 보정을 하려면 **분포 자체**가 필요하고,
    설정마다 같은 오프셋을 써야 상관이 보존된다.
    """

    aligned = phase.reindex(relative.index).to_numpy()
    values = relative.to_numpy(dtype=float)
    valid = np.isfinite(values).astype(float)
    filled = np.nan_to_num(values)
    masks = np.vstack([(aligned == name) for name in PHASES]).astype(float)
    weeks = masks.shape[1]
    row = PHASES.index(TARGET_PHASE)

    offsets = [k for k in range(weeks) if MINIMUM_SHIFT <= k <= weeks - MINIMUM_SHIFT]
    observed = _phase_statistic(masks, filled, valid)[row]
    draws = np.array(
        [_phase_statistic(np.roll(masks, offset, axis=1), filled, valid)[row] for offset in offsets]
    )
    median = float(np.nanmedian(draws)) if np.isfinite(draws).any() else float("nan")
    return {
        "observed": float(observed),
        "draws": draws,
        "offsets": len(offsets),
        "ratio_to_chance": (
            round(float(observed / median), 3) if np.isfinite(median) and median > 0 else None
        ),
        "nominal_p": round(float((int((draws >= observed).sum()) + 1) / (len(offsets) + 1)), 4),
    }


def _p_of(draws: np.ndarray, values: np.ndarray) -> np.ndarray:
    """이 설정 자신의 귀무분포 안에서 각 값이 갖는 p. 관측과 이동값에 같은 정의를 쓴다."""

    count = len(draws)
    order = np.sort(draws)
    # order 안에서 value 이상인 개수. searchsorted가 O(n log n)으로 끝낸다.
    at_least = count - np.searchsorted(order, values, side="left")
    return (at_least + 1.0) / (count + 1.0)


def max_statistic(entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """격자 전체를 훑었을 때의 족보 p — **min-P** 방식.

    원 통계량을 그대로 최댓값 삼으면 안 된다. 설정마다 후퇴기 주수가 다르고, 주수가
    적은 설정일수록 평균이 흔들려 원 통계량이 구조적으로 커진다. 그러면 척도가 다른
    값을 견주는 셈이라 보정이 오히려 명목 p보다 작게 나올 수 있다.

    그래서 각 설정을 **자기 귀무분포 안의 p**로 먼저 옮겨 척도를 없애고, 이동마다
    격자 전체의 최솟값을 다시 뽑는다. 같은 오프셋을 모든 설정에 쓰므로 설정 간 상관은
    그대로 보존된다.
    """

    names = sorted(entries)
    draws = {name: np.asarray(entries[name]["draws"], dtype=float) for name in names}
    observed_p = np.array(
        [float(_p_of(draws[name], np.array([entries[name]["observed"]]))[0]) for name in names]
    )
    null_p = np.vstack([_p_of(draws[name], draws[name]) for name in names])
    null_min = null_p.min(axis=0)

    best = int(np.argmin(observed_p))
    peak = float(observed_p[best])
    extreme = int(np.sum(null_min <= peak))
    return {
        "grid_size": len(names),
        "argmin_p_gate": names[best],
        "smallest_nominal_p_in_grid": round(peak, 4),
        "family_wise_p": round(float((extreme + 1) / (len(null_min) + 1)), 4),
        "shift_draws": int(len(null_min)),
        "method": "min-P (격자 전체를 자기 귀무분포의 p로 옮긴 뒤 이동마다 최솟값)",
        "note": (
            "격자 안의 설정들은 서로 강하게 상관돼 있다. 같은 이동 오프셋을 모든 설정에 "
            "쓰므로 그 상관이 귀무분포에 보존되고, 본페로니보다 덜 보수적이면서 "
            "격자를 쓸었다는 사실을 반영한다."
        ),
    }


def bonferroni(nominal_p: float, grid_size: int) -> dict[str, Any]:
    """가장 보수적인 보정. 상관을 전혀 인정하지 않으므로 상한으로 읽는다."""

    adjusted = min(1.0, nominal_p * grid_size)
    return {
        "nominal_p": nominal_p,
        "grid_size": grid_size,
        "bonferroni_p": round(adjusted, 4),
        "survives_at_five_percent": bool(adjusted <= 0.05),
        "note": (
            "설정 간 상관을 전혀 인정하지 않는 상한이다. 이 격자의 설정들은 실제로 크게 "
            "겹치므로 이 값은 지나치게 보수적이며, 최댓값 보정과 함께 읽어야 한다."
        ),
    }


def read(
    nominal: dict[str, Any], family: dict[str, Any], bonf: dict[str, Any], chosen: str
) -> dict[str, Any]:
    """세 숫자를 하나의 문장으로. 결론이 아니라 **표현**을 정하는 것이다."""

    survives = bool(family["family_wise_p"] <= 0.05)
    # 이동 분포 자체는 산출물에 싣지 않는다 — 수천 개짜리 배열이고, 남길 것은 요약이다.
    reported = {key: value for key, value in nominal.items() if key != "draws"}
    return {
        "chosen_gate": chosen,
        "nominal": reported,
        "family_wise": family,
        "bonferroni": bonf,
        "survives_correction": survives,
        "defences": [
            "자연 실험이 격자를 돌리기 전에 '지속이 우선'을 가리켰다 — 지속을 흔든 것은 "
            "자료를 본 뒤의 착상이 아니다.",
            "평탄역 판정 기준과 최소 블록 수는 격자를 돌리기 전에 코드에 있었다.",
            "17주는 B(동결 창)에서 골랐고 C(확장 역사)는 그 뒤에 돌렸다 — C의 p가 17주를 "
            "고르게 한 것이 아니다.",
        ],
        "statement": (
            f"확장 역사의 p={nominal['nominal_p']}는 **지속 길이 8개와 중립대 배수 "
            f"{bonf['grid_size'] - 8}개를 쓸어 고른 최댓값의 명목 p**다. 같은 격자를 "
            f"이동 귀무분포에서도 쓸어 최솟값 p를 다시 뽑으면(min-P) 족보 전체 p는 "
            f"{family['family_wise_p']}이고, 본페로니 상한은 {bonf['bonferroni_p']}다."
            + (
                " 최댓값 보정 뒤에도 5% 아래에 남는다."
                if survives
                else " **보정하면 5% 문턱을 넘지 못한다 — '50년 역사에서 5% 유의'라고 "
                "단정해서는 안 되고, 방향이 강하다는 것까지가 이 자료가 받쳐 주는 "
                "말이다.**"
            )
        ),
    }
