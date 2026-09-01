"""완전예지 천장 — **다른 무엇보다 먼저.**

`rotation.run`의 `rotation_full_sample_ceiling`은 국면을 이미 알고, 국면별로 어느 산업이
가장 좋았는지까지 이미 아는 전략의 수익이다. 실현 가능한 어떤 전략도 이 위로 갈 수 없다.

경계를 바꾸면 어느 주가 어느 라벨을 다는지가 바뀌므로 천장 자체가 움직였을 수 있다.
그래서 v1.1과 persist17w를 **같은 자료, 같은 창, 같은 절차**로 나란히 잰다.

## 천장을 두 가지로 나눈다

``ranking_ceiling``  국면별 상위 3개를 고르는 천장. 트랙 17이 쓴 것과 같다.
``oracle_ceiling``   매주 그 주의 최고 3개를 고르는 천장. 국면과 무관한 **상한의 상한**이다.

둘을 함께 내는 이유는, 낮은 천장이 "국면이 산업을 못 가른다" 때문인지 "산업 간 차이가
애초에 작다" 때문인지 갈라야 하기 때문이다. 앞이면 국면 모델의 문제이고 뒤면 자료의
문제다. 처방이 다르다.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..phase_returns import rotation as R
from . import prespec


def _weekly_oracle(values: np.ndarray, top_k: int) -> np.ndarray:
    """매주 그 주의 최고 top_k. 국면을 쓰지 않는 상한의 상한이다."""

    weeks, count = values.shape
    weights = np.zeros((weeks, count))
    for position in range(weeks):
        ranked = np.argsort(-values[position])
        weights[position, ranked[:top_k]] = 1.0 / top_k
    return weights


def measure(phase: pd.Series, weekly: pd.DataFrame, top_k: int = R.TOP_K) -> dict[str, Any]:
    """한 라벨링의 두 천장. `rotation`의 함수를 그대로 빌려 절차를 어긋나지 않게 한다."""

    relative = R.weekly_relative(weekly)
    usable = relative.dropna(how="any")
    aligned = phase.reindex(usable.index).fillna("").astype(str).to_numpy()
    values = usable.to_numpy(dtype=float)

    ranking = R._realise(R._full_sample_weights(aligned, values, top_k), values)
    # 완전예지 신탁은 **밀지 않는다.** 그 주의 수익을 보고 그 주를 고르는 것이 정의다.
    oracle_weights = _weekly_oracle(values, top_k)
    oracle = (oracle_weights * values).sum(axis=1)

    ranking_profile = R._profile(ranking, "phase ranking ceiling (not achievable)")
    oracle_profile = R._profile(oracle, "weekly oracle ceiling (no phases at all)")
    share = (
        round(
            float(ranking_profile["annualised_relative_return"])
            / float(oracle_profile["annualised_relative_return"]),
            4,
        )
        if oracle_profile["annualised_relative_return"]
        else None
    )
    return {
        "weeks": int(len(values)),
        "top_k": top_k,
        "ranking_ceiling": ranking_profile,
        "oracle_ceiling": oracle_profile,
        "phase_share_of_the_oracle": share,
        "what_the_share_means": (
            "국면 순위 천장 ÷ 주간 신탁 천장. 산업 간 차이 중 **국면으로 조직되는 몫**이다. "
            "작으면 산업 차이는 있는데 국면이 그것을 잡지 못한다는 뜻이고, 그때는 국면 "
            "정확도를 올려도 소용이 없다."
        ),
    }


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """v1.1과 persist17w의 천장. 관문 판정은 persist17w 쪽에 건다."""

    annual = after["ranking_ceiling"]["annualised_relative_return"]
    ratio = after["ranking_ceiling"]["information_ratio"]
    gate = prespec.ceiling_gate(annual, ratio)

    moved = round(float(annual) - float(before["ranking_ceiling"]["annualised_relative_return"]), 4)
    return {
        "v1_1": before,
        "persist17w": after,
        "moved_annual": moved,
        "gate": gate,
        "reading": (
            (
                f"경계를 고치자 천장이 {moved:+.2%} 움직였다."
                if abs(moved) >= 0.005
                else "경계를 고쳐도 천장은 사실상 그대로다."
            )
            + " "
            + (
                "관문을 넘으므로 아래 단계를 볼 이유가 있다."
                if gate["passes"]
                else "**관문을 넘지 못한다.** 국면을 완전히 알고 국면별 최고 산업까지 "
                "알아도 이만큼이므로, 국면 정확도를 아무리 올려도 그 위로 갈 수 없다."
            )
        ),
    }
