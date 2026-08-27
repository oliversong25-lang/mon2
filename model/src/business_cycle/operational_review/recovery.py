"""§5·§6. 회복 인식 지연 감사.

앞선 감사는 침체 **진입**에 집중했다. 남아 있던 운영 위험은 그 반대쪽이다 — 저점을
지나고도 침체 판정에 얼마나 오래 머무는가. 투자 프레임워크의 거시 입력으로서는
늦은 진입만큼이나 늦은 회복 인식이 해롭다.

지연을 세 층으로 갈라 잰다. 그래야 모델 증거의 지연과 상태 필터의 지연이 섞이지
않는다.

``raw``              관측 점수의 승자. 모델 증거 자체.
``filtered_winner``  소프트 필터를 통과한 승자. 확인 규칙 이전.
``official``         확인 규칙까지 통과한 공식 국면.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

#: NBER 정점과 저점. 회고적 라벨이며 모델이 그 시점에 알 수 있던 정보가 아니다.
PEAKS: Final[dict[str, str]] = {
    "recession_2001": "2001-03-01",
    "gfc_2009": "2007-12-01",
    "recession_2020": "2020-02-01",
}

TROUGHS: Final[dict[str, str]] = {
    "recession_2001": "2001-11-30",
    "gfc_2009": "2009-06-30",
    "recession_2020": "2020-04-30",
}

#: 저점 이후 감사 지평. 에피소드마다 같은 길이를 봐야 비교가 성립한다. 이 경계가
#: 없으면 2001년 저점의 "저점 이후 침체 지속"에 2003년 오탐과 금융위기가 통째로
#: 딸려 들어온다 — 실제로 그렇게 잘못 셌고, 그래서 경계를 명시한다.
POST_TROUGH_HORIZON_WEEKS: Final[int] = 52

#: 개발구간에 속한 에피소드. 비교 기준은 여기서만 나온다. 2020년은 쓰지 않는다.
DEVELOPMENT_EPISODES: Final[tuple[str, ...]] = ("recession_2001", "gfc_2009")

LAYERS: Final[tuple[str, ...]] = ("raw", "filtered_winner", "official")


def _first_on_or_after(mask: pd.Series, index: pd.DatetimeIndex, start: pd.Timestamp) -> str | None:
    hits = index[mask.to_numpy(dtype=bool) & (index >= start)]
    return str(pd.Timestamp(hits[0]).date()) if len(hits) else None


def _first_run(
    mask: pd.Series, index: pd.DatetimeIndex, start: pd.Timestamp, length: int
) -> str | None:
    """연속 ``length`` 주가 처음 성립하는 시작 주."""

    values = mask.to_numpy(dtype=bool)
    positions = list(range(len(values)))
    run = 0
    for position in positions:
        if index[position] < start:
            run = run + 1 if values[position] else 0
            continue
        run = run + 1 if values[position] else 0
        if run >= length:
            return str(pd.Timestamp(index[position - length + 1]).date())
    return None


def _weeks(date: str | None, reference: pd.Timestamp) -> int | None:
    return None if date is None else int((pd.Timestamp(date) - reference).days // 7)


def episode_audit(
    path: pd.DataFrame,
    trough: pd.Timestamp,
    label: str,
    sample_role: str,
    peak: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """한 에피소드의 회복 인식.

    두 창을 분명히 나눈다. **침체 안**은 [정점, 저점]이고, 저점 이후 관찰은 저점부터
    ``POST_TROUGH_HORIZON_WEEKS``까지다. 경계를 두지 않으면 다음 침체와 그 사이의
    오탐이 이 에피소드의 성적으로 딸려 들어온다.
    """

    index = pd.DatetimeIndex(path.index)
    if not len(index) or index[-1] < trough:
        return {"episode": label, "sample_role": sample_role, "covered": False}
    horizon = trough + pd.Timedelta(weeks=POST_TROUGH_HORIZON_WEEKS)

    columns = {
        "raw": path["raw_phase"].astype(str),
        "filtered_winner": path["filtered_winner"].astype(str)
        if "filtered_winner" in path.columns
        else path["official_phase"].astype(str),
        "official": path["official_phase"].astype(str),
    }
    result: dict[str, Any] = {
        "episode": label,
        "sample_role": sample_role,
        "covered": True,
        "nber_peak": str(peak.date()) if peak is not None else None,
        "nber_trough": str(trough.date()),
        "post_trough_horizon_weeks": POST_TROUGH_HORIZON_WEEKS,
        "post_trough_window_end": str(min(horizon, index[-1]).date()),
    }

    for layer in LAYERS:
        series = columns[layer]
        recovery = series.eq("recovery")
        first = _first_on_or_after(recovery, index, trough)
        run4 = _first_run(recovery, index, trough, 4)
        result[f"{layer}_first_recovery"] = first
        result[f"{layer}_first_recovery_lag_weeks"] = _weeks(first, trough)
        result[f"{layer}_first_four_week_recovery"] = run4
        result[f"{layer}_first_four_week_recovery_lag_weeks"] = _weeks(run4, trough)

    official = columns["official"]
    contraction = official.eq("contraction")
    window = pd.Series((index >= trough) & (index <= horizon), index=index)
    post = contraction & window
    result["post_trough_contraction_weeks"] = int(post.sum())

    longest = streak = 0
    for value in post:
        streak = streak + 1 if bool(value) else 0
        longest = max(longest, streak)
    result["longest_post_trough_contraction_run"] = longest
    # 이 에피소드의 마지막 공식 침체. 표본 전체의 마지막이 아니라 감사 지평 안이다.
    episode_window = pd.Series(
        (index >= (peak if peak is not None else index[0])) & (index <= horizon), index=index
    )
    last = index[(contraction & episode_window).to_numpy(dtype=bool)]
    result["last_official_contraction"] = str(pd.Timestamp(last[-1]).date()) if len(last) else None
    result["last_official_contraction_lag_weeks"] = _weeks(
        result["last_official_contraction"], trough
    )

    if "transition_watch" in path.columns:
        watch = path["transition_watch"].astype(str).eq("toward_recovery")
        first_watch = _first_on_or_after(watch, index, trough)
        result["first_recovery_transition_watch"] = first_watch
        result["first_recovery_transition_watch_lag_weeks"] = _weeks(first_watch, trough)

    # 공식 회복 이후 4·8·13주 안에 다시 침체로 돌아갔는가. 빠른 탈출이 진동을 만들면
    # 개선이 아니다.
    first_official = result["official_first_recovery"]
    for weeks_ahead in (4, 8, 13):
        key = f"return_to_contraction_within_{weeks_ahead}_weeks"
        if first_official is None:
            result[key] = None
            continue
        begin = pd.Timestamp(first_official)
        ahead = pd.Series(
            (index > begin) & (index <= begin + pd.Timedelta(weeks=weeks_ahead)), index=index
        )
        result[key] = int((contraction & ahead).sum())

    # 침체 **중** 조기 회복. NBER 정점과 저점 사이에서만 본다. 이 경계가 없으면
    # 앞선 침체 뒤의 정당한 회복이 이 에피소드의 조기 이탈로 잘못 잡힌다.
    if peak is None:
        result["premature_four_week_recovery_inside_the_recession"] = None
    else:
        inside = pd.Series((index >= peak) & (index <= trough), index=index)
        result["premature_four_week_recovery_inside_the_recession"] = _first_run(
            columns["official"].eq("recovery") & inside, index, peak, 4
        )

    moment = pd.Timestamp(first_official) if first_official else None
    if moment is not None and moment in path.index:
        for column, name in (
            ("evidence_quality_high", "evidence_quality_high_at_recovery"),
            ("phase_separation", "phase_separation_at_recovery"),
            ("confirmation_pending", "confirmation_pending_at_recovery"),
            ("negative_level_domains", "negative_level_domains_at_recovery"),
            ("positive_momentum_domains", "positive_momentum_domains_at_recovery"),
            ("confirming_domains", "confirming_domains_at_recovery"),
            ("concentration", "concentration_at_recovery"),
            ("information_lag_weeks", "information_lag_weeks_at_recovery"),
            ("weeks_since_any_new_observation", "panel_silence_at_recovery"),
        ):
            if column in path.columns:
                raw_value = str(path.at[moment, column])
                try:
                    result[name] = float(raw_value)
                except ValueError:
                    result[name] = raw_value
    return result


def decompose_delay(episode: dict[str, Any]) -> dict[str, Any]:
    """§6. 증거 지연과 필터·확인 지연을 분리한다."""

    raw = episode.get("raw_first_recovery_lag_weeks")
    filtered = episode.get("filtered_winner_first_recovery_lag_weeks")
    official = episode.get("official_first_recovery_lag_weeks")
    return {
        "episode": episode["episode"],
        "sample_role": episode.get("sample_role"),
        "raw_evidence_lag_weeks": raw,
        "filter_lag_weeks": None if raw is None or filtered is None else filtered - raw,
        "confirmation_lag_weeks": None
        if filtered is None or official is None
        else official - filtered,
        "total_official_lag_weeks": official,
    }
