"""경기 수준에서 비교 가능한 모멘텀 좌표를 만든다."""

from __future__ import annotations

import numpy as np
import pandas as pd

EXPANDING = "expanding_mean_std"
ROLLING = "rolling_mean_std"
NONE = "none"
SCALE_ONLY = "scale_only"

#: 주 단위 창을 달력 연수로 환산할 때 쓰는 상수. 365.2425 / 7.
WEEKS_PER_YEAR = 52.1775

#: 척도를 0으로 볼 임계값.
NEGLIGIBLE_SCALE = 1e-12


def weeks_for_years(years: float) -> int:
    """달력 연수를 주 관측 수로 바꾼다. 창은 관측 수가 아니라 달력 길이로 정한다."""

    return max(1, int(round(WEEKS_PER_YEAR * float(years))))


def _statistics(
    series: pd.Series, method: str, window: int, min_periods: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """현재 관측을 제외한 중심·척도와 그 출처를 반환한다.

    척도가 0이거나 무시할 만큼 작으면 문서화한 순서로 인과적 대체를 쓴다.
    표준편차 → 사분위범위/1.349 → 현재값을 제외한 expanding 표준편차. 어느 것도
    양수가 아니면 그 주는 결측으로 남긴다. 임의의 하한을 두지 않는다.
    """

    history = series.shift(1)
    if method in {ROLLING, SCALE_ONLY}:
        rolling = history.rolling(window, min_periods=min_periods)
        mean, std = rolling.mean(), rolling.std(ddof=1)
        iqr = (rolling.quantile(0.75) - rolling.quantile(0.25)) / 1.349
    elif method == EXPANDING:
        expanding = history.expanding(min_periods=min_periods)
        mean, std = expanding.mean(), expanding.std(ddof=1)
        iqr = (expanding.quantile(0.75) - expanding.quantile(0.25)) / 1.349
    else:
        raise ValueError(f"지원하지 않는 좌표 표준화 방식: {method}")

    # 합성요인은 평균 0의 표준화 신호를 가중평균한 값이므로 무조건부 평균이 0이다.
    # 중심을 자료에서 다시 추정하면 그 추정치가 시작 시점을 기억한다. scale_only는
    # 중심을 이론값 0으로 고정하고 척도만 인과적으로 추정한다.
    center = pd.Series(0.0, index=series.index) if method == SCALE_ONLY else mean
    expanding_std = history.expanding(min_periods=min_periods).std(ddof=1)

    usable_std = std.where(std > NEGLIGIBLE_SCALE)
    usable_iqr = iqr.where(iqr > NEGLIGIBLE_SCALE)
    usable_expanding = expanding_std.where(expanding_std > NEGLIGIBLE_SCALE)
    scale = usable_std.fillna(usable_iqr).fillna(usable_expanding)
    source = pd.Series("none", index=series.index, dtype=object)
    source = source.mask(usable_expanding.notna(), "expanding_std")
    source = source.mask(usable_iqr.notna(), "iqr")
    source = source.mask(usable_std.notna(), "std")
    # scale_only는 중심이 상수 0이라 center로는 미성숙 구간을 알 수 없다. 척도로 판단한다.
    source = source.mask(center.isna() | scale.isna(), "insufficient_history")
    return center, scale, source


def coordinate_details(
    factor: pd.Series,
    weeks: int = 4,
    min_periods: int = 26,
    slope: pd.Series | None = None,
    *,
    method: str = EXPANDING,
    window_years: float = 10.0,
    minimum_history_years: float | None = None,
) -> pd.DataFrame:
    """좌표와 그 좌표가 어떻게 만들어졌는지를 함께 반환한다.

    합성요인은 이미 표준화된 지표 신호로 만들어졌다. 여기서 한 번 더 긴 창으로
    표준화하면 성숙 요구가 두 겹으로 쌓인다. 그래서 창 길이와 최소 이력을 따로
    설정할 수 있게 열어 두고, 실제로 쓴 창을 기록한다.

    ``method='none'``은 재표준화 없이 합성요인을 그대로 Y로 쓴다. 이 경우 Y 게이트와
    반지름 관련 임계값은 원래 의미를 잃는다. 구조 진단용이다.
    """

    window = weeks_for_years(window_years)
    required = (
        min_periods if minimum_history_years is None else weeks_for_years(minimum_history_years)
    )
    required = max(1, int(required))

    unscaled_y = factor
    unscaled_x = factor - factor.shift(weeks)

    if method == NONE:
        scaled_y, scaled_x = unscaled_y, unscaled_x
        center = pd.Series(0.0, index=factor.index)
        scale = pd.Series(1.0, index=factor.index)
        source = pd.Series("not_standardized", index=factor.index, dtype=object)
        observations = pd.Series(np.nan, index=factor.index)
    else:
        y_center, y_scale, y_source = _statistics(unscaled_y, method, window, required)
        x_center, x_scale, _ = _statistics(unscaled_x, method, window, required)
        scaled_y = ((unscaled_y - y_center) / y_scale).replace([np.inf, -np.inf], np.nan)
        scaled_x = ((unscaled_x - x_center) / x_scale).replace([np.inf, -np.inf], np.nan)
        center, scale, source = y_center, y_scale, y_source
        history = unscaled_y.shift(1)
        observations = (
            history.expanding(min_periods=required).count()
            if method == EXPANDING
            else history.rolling(window, min_periods=required).count()
        )

    if slope is not None and method != NONE:
        slope_center, slope_scale, _ = _statistics(
            slope.reindex(factor.index), method, window, required
        )
        scaled_slope = (slope.reindex(factor.index) - slope_center) / slope_scale
        scaled_x = pd.concat([scaled_x.rename("delta"), scaled_slope.rename("slope")], axis=1).mean(
            axis=1
        )

    positions = np.arange(len(factor))
    index_values = pd.Series(factor.index)
    # expanding은 창이 없다. 시작점이 곧 자료의 시작이므로 그렇게 기록한다.
    # rolling과 같은 계산을 쓰면 창이 10년인 것처럼 보이는 거짓 기록이 남는다.
    starts = (
        np.zeros_like(positions)
        if method == EXPANDING
        else np.clip(positions - window, 0, max(len(factor) - 1, 0))
    )
    ends = np.clip(positions - 1, 0, max(len(factor) - 1, 0))
    window_start = index_values.reindex(starts).to_numpy()
    window_end = index_values.reindex(ends).to_numpy()
    duration = pd.Series(
        (pd.to_datetime(window_end) - pd.to_datetime(window_start)).days / 365.2425,
        index=factor.index,
    )

    angle = np.degrees(np.arctan2(scaled_y, scaled_x)) % 360.0
    radius = np.sqrt(scaled_x.pow(2) + scaled_y.pow(2))
    return pd.DataFrame(
        {
            "x": scaled_x,
            "y": scaled_y,
            "angle": angle,
            "radius": radius,
            "unscaled_x": unscaled_x,
            "unscaled_y": unscaled_y,
            "coordinate_center": center,
            "coordinate_scale": scale,
            "scale_fallback_used": source,
            "window_start": [window_start[p] if p >= 1 else None for p in positions],
            "window_end": [window_end[p] if p >= 1 else None for p in positions],
            "window_duration_years": duration.where(positions >= 1),
            "window_observation_count": observations,
        },
        index=factor.index,
    )


def coordinates(
    factor: pd.Series,
    weeks: int = 4,
    min_periods: int = 26,
    slope: pd.Series | None = None,
    **kwargs: object,
) -> pd.DataFrame:
    """기존 호출 호환용: 상태모형이 쓰는 네 열만 반환한다."""

    details = coordinate_details(factor, weeks, min_periods, slope, **kwargs)  # type: ignore[arg-type]
    return details[["x", "y", "angle", "radius"]]
