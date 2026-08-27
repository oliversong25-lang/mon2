"""§13의 엄격 ALFRED 재구성.

2013-06-14부터 시작하는 이유는 이 모델의 판단이 아니라 자료의 사실이다 — 일곱 핵심
지표의 ``fred/series/vintagedates`` 첫 값 중 가장 늦은 날이 그날이다. 그 이전에는
어떤 계열이 진짜 빈티지를 갖지 않아 "실시간"이라고 부를 수 없다.

빈티지는 이미 받아 둔 로컬 캐시에서만 읽는다. 네트워크도, API 키도 쓰지 않는다.
캐시에 그 계열이 없으면 조용히 최신값으로 메우지 않고 멈춘다.

여기서 나온 결과로 모델을 고치지 않는다. 엄격 ALFRED는 §12의 필수 게이트를 전부
통과한 뒤에만 돌리며, 통과 이후의 관측 기록이다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ..config import Settings, load_baseline
from ..current_state.domains import DOMAINS
from ..data.alfred import observations_as_of
from .engine import FourPhaseConfig, prepare, score
from .freshness import Eligibility, evaluate

#: 일곱 핵심지표가 모두 진짜 빈티지를 갖는 첫 주.
STRICT_START: Final[pd.Timestamp] = pd.Timestamp("2013-06-14")
STRICT_START_RULE: Final[str] = "일곱 핵심지표의 fred/series/vintagedates 첫 값 중 가장 늦은 날"


def cached_frames(settings: Settings) -> dict[str, pd.DataFrame]:
    """로컬 캐시에 이미 있는 빈티지 관측만 읽는다. 네트워크도 키도 쓰지 않는다."""

    directory = settings.root / "data" / "cache" / "alfred"
    frames: dict[str, pd.DataFrame] = {}
    for series_id in settings.indicators["indicators"]:
        path = directory / f"{series_id}.csv"
        if not path.exists():
            raise FileNotFoundError(
                f"{series_id}의 빈티지 캐시가 없습니다: {path}. "
                "최신값으로 메우면 실시간이 아니므로 멈춥니다."
            )
        frame = pd.read_csv(path)
        for column in ("date", "realtime_start", "realtime_end"):
            frame[column] = pd.to_datetime(frame[column])
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        frames[series_id] = frame.dropna(subset=["value"]).sort_values(["date", "realtime_start"])
    return frames


def cache_audit(settings: Settings, end: pd.Timestamp) -> dict[str, Any]:
    """§11. 실행 전에 캐시가 실제로 그 구간을 덮는지 확인한다.

    덮지 못하는 주가 있으면 최신값으로 메우지 않고 그 주를 `withheld`로 남긴다.
    최신 수정치로 메우는 순간 그것은 더 이상 실시간이 아니다.
    """

    weeks = pd.date_range(STRICT_START, end, freq="W-FRI")
    directory = settings.root / "data" / "cache" / "alfred"
    series: dict[str, Any] = {}
    uncovered: dict[str, list[str]] = {}
    for series_id in settings.indicators["indicators"]:
        path = directory / f"{series_id}.csv"
        if not path.exists():
            series[series_id] = {"cached": False}
            uncovered[series_id] = [str(week.date()) for week in weeks]
            continue
        frame = pd.read_csv(path)
        starts = pd.to_datetime(frame["realtime_start"])
        ends = pd.to_datetime(frame["realtime_end"])
        first = starts.min()
        missing = [str(week.date()) for week in weeks if week < first]
        # 미래 빈티지 오염: as-of 이후에 시작한 판본이 그 시점에 보이면 안 된다.
        # slice_vintage가 realtime 구간으로 거르므로 구조적으로 불가능하지만,
        # 캐시 자체에 마지막 as-of를 넘는 realtime_start가 몇 건인지는 남겨 둔다.
        series[series_id] = {
            "cached": True,
            "rows": int(len(frame)),
            "first_realtime_start": str(first.date()),
            "last_realtime_start": str(starts.max().date()),
            "open_ended_vintages": int((ends >= pd.Timestamp("2099-01-01")).sum()),
            "rows_after_window_end": int((starts > end).sum()),
            "covers_window": bool(first <= STRICT_START),
        }
        if missing:
            uncovered[series_id] = missing
    duplicates = int(len(weeks) - len(set(weeks)))

    # 새 판본 활동. 캐시에 값이 있다는 것과 그 주에 새 판본이 도착했다는 것은 다른
    # 사실이므로 따로 센다. 2025년 가을에 일곱 계열 전부가 몇 주 동안 조용했다.
    frames = {
        series_id: pd.read_csv(directory / f"{series_id}.csv")
        for series_id in settings.indicators["indicators"]
        if (directory / f"{series_id}.csv").exists()
    }
    for frame in frames.values():
        frame["realtime_start"] = pd.to_datetime(frame["realtime_start"])
    activity: list[dict[str, Any]] = []
    seen: dict[str, pd.Timestamp | None] = dict.fromkeys(frames, None)
    silent_run = 0
    longest_silence = 0
    silent_weeks: list[str] = []
    for week in weeks:
        arrived_now: list[str] = []
        for series_id, frame in frames.items():
            visible = frame.loc[frame["realtime_start"] <= week, "realtime_start"]
            if not len(visible):
                continue
            latest = pd.Timestamp(visible.max())
            previous = seen[series_id]
            if previous is None or latest > previous:
                arrived_now.append(series_id)
            seen[series_id] = latest
        if arrived_now:
            silent_run = 0
        else:
            silent_run += 1
            longest_silence = max(longest_silence, silent_run)
            silent_weeks.append(str(week.date()))
        activity.append({"as_of": str(week.date()), "new_vintages": len(arrived_now)})

    return {
        "expected_as_of_dates": int(len(weeks)),
        "actual_as_of_dates": int(len(set(weeks))),
        "missing_as_of_dates": [],
        "duplicate_as_of_dates": duplicates,
        "first_week": str(weeks[0].date()),
        "last_week": str(weeks[-1].date()),
        # 세 가지를 절대 합치지 않는다. 캐시에 있다는 것이 곧 쓸 만큼 새롭다는 뜻은 아니다.
        "cache_coverage": {
            "series": series,
            "series_without_full_coverage": sorted(uncovered),
            "all_series_cover_window": not uncovered,
        },
        "new_vintage_activity": {
            "weeks_with_no_new_vintage_in_any_series": len(silent_weeks),
            "longest_all_series_publication_pause_weeks": longest_silence,
            "pause_weeks": silent_weeks,
        },
        "weeks_to_withhold": sorted({week for values in uncovered.values() for week in values}),
        "network_used": False,
        "api_key_used": False,
        "latest_vintage_substitution": False,
        "backward_fill_used": False,
        "inferred_releases_used": False,
        "provenance": "data/cache/alfred/*.csv — ALFRED 값만. URL도 키도 저장하지 않는다.",
    }


@dataclass(frozen=True)
class StrictWeek:
    as_of: pd.Timestamp
    official_phase: str
    raw_phase: str
    phase_status: str
    recession_alert: str
    alert_character: str
    activity_level: float
    activity_momentum: float
    confirming_domains: int
    withheld: bool
    future_observations: int
    eligibility: Eligibility | None


def _week(
    settings: Settings,
    baseline: Settings,
    frames: dict[str, pd.DataFrame],
    config: FourPhaseConfig,
    vintage: pd.Timestamp,
) -> StrictWeek:
    """한 빈티지 시점의 실행. 그 시점에 공개돼 있던 값만 쓴다."""

    observations = observations_as_of(frames, vintage, settings.indicators["indicators"])
    future = int((observations["release_date"] > vintage).sum())
    prepared = prepare(observations, baseline, vintage, config)
    if len(prepared.index) == 0:
        return StrictWeek(
            vintage,
            "",
            "",
            "withheld",
            "none",
            "absent",
            float("nan"),
            float("nan"),
            0,
            True,
            future,
            None,
        )

    # 신선도는 **as-of 시점 기준**으로 잰다. 발표가 멈추면 마지막 모델링 주가 그 자리에
    # 멈춰 서고, 그 주에서 잰 나이는 영원히 정상으로 보인다. 2025년 가을에 정확히 그
    # 일이 있었다 — 일곱 계열 전부가 7주 동안 새 판본을 내지 않았는데, 캐시에는 값이
    # 그대로 있어서 러너가 7주 묵은 판정을 현재 판정으로 보고했다.
    eligibility = evaluate(
        vintage,
        prepared.index,
        prepared.weeks_since_release,
        prepared.arrived,
        config.freshness,
    )
    run = score(prepared, config)
    week = run.official_phase.index[-1]
    withheld = eligibility.withheld
    return StrictWeek(
        as_of=vintage,
        # 판정 보류일 때는 공식 국면을 내지 않는다. 원시 측정값은 그대로 남긴다 —
        # 상태 판정이 경제 측정 자체를 바꾸지는 않는다.
        official_phase="" if withheld else str(run.official_phase.loc[week]),
        raw_phase=str(run.raw_phase.loc[week]),
        phase_status=eligibility.status,
        recession_alert=str(run.alert_level.loc[week]),
        alert_character=str(run.alert_character.loc[week]),
        activity_level=float(run.activity_level.loc[week]),
        activity_momentum=float(run.activity_momentum.loc[week]),
        confirming_domains=int(run.confirming_domains.loc[week]),
        withheld=withheld,
        future_observations=future,
        eligibility=eligibility,
    )


def run_strict(
    settings: Settings,
    config: FourPhaseConfig,
    baseline_name: str,
    end: pd.Timestamp,
    checkpoint: Path | None = None,
    progress_every: int = 25,
    withhold: set[str] | None = None,
) -> pd.DataFrame:
    """2013-06-14부터 주 단위로 실시간 경로를 만든다. 중단되면 이어서 돌린다."""

    frames = cached_frames(settings)
    baseline = load_baseline(baseline_name, settings)
    weeks = pd.date_range(STRICT_START, end, freq="W-FRI")
    blocked = withhold or set()
    done: dict[str, dict[str, Any]] = {}
    if checkpoint is not None and checkpoint.exists():
        previous = pd.read_csv(checkpoint)
        done = {str(row["as_of"]): dict(row) for _, row in previous.iterrows()}

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    handle = None
    if checkpoint is not None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        handle = checkpoint.open("a", encoding="utf-8", newline="")
    try:
        for position, vintage in enumerate(weeks, start=1):
            key = str(vintage.date())
            if key in done:
                rows.append(done[key])
                continue
            if key in blocked:
                # 필요한 빈티지가 없다. 최신값으로 메우지 않고 판정을 보류한다.
                result = StrictWeek(
                    vintage,
                    "",
                    "",
                    "withheld",
                    "none",
                    "absent",
                    float("nan"),
                    float("nan"),
                    0,
                    True,
                    0,
                    None,
                )
            else:
                result = _week(settings, baseline, frames, config, vintage)
            eligibility = result.eligibility
            row = {
                "as_of": key,
                "official_phase": result.official_phase,
                "raw_phase": result.raw_phase,
                "phase_status": result.phase_status,
                "recession_alert": result.recession_alert,
                "recession_alert_character": result.alert_character,
                "activity_level": result.activity_level,
                "activity_momentum": result.activity_momentum,
                "confirming_domains": result.confirming_domains,
                "withheld": int(result.withheld),
                "future_observations": result.future_observations,
                "information_lag_weeks": eligibility.information_lag_weeks if eligibility else 0,
                "weeks_since_any_new_observation": (
                    eligibility.weeks_since_any_new_observation if eligibility else 0
                ),
                "stale_domains": "|".join(eligibility.stale_domains) if eligibility else "",
                "fresh_coincident_domains": (
                    eligibility.fresh_coincident_domains if eligibility else 0
                ),
                "carried_forward_domains": (
                    sum(1 for value in eligibility.domain_carried_forward.values() if value)
                    if eligibility
                    else 0
                ),
            }
            rows.append(row)
            if handle is not None:
                if handle.tell() == 0:
                    handle.write(",".join(row) + "\n")
                handle.write(",".join(str(value) for value in row.values()) + "\n")
                handle.flush()
            if position % progress_every == 0 or position == len(weeks):
                elapsed = time.monotonic() - started
                rate = elapsed / max(position, 1)
                remaining = rate * (len(weeks) - position)
                print(
                    f"  엄격 ALFRED {position}/{len(weeks)}주 · {key} · "
                    f"경과 {elapsed / 60:.1f}분 · 주당 {rate:.2f}초 · "
                    f"남은 예상 {remaining / 60:.1f}분",
                    flush=True,
                )
    finally:
        if handle is not None:
            handle.close()
    frame = pd.DataFrame(rows)
    frame["as_of"] = pd.to_datetime(frame["as_of"])
    return frame.set_index("as_of").sort_index()


def summarise(path: pd.DataFrame, recession: pd.Series, digest: str) -> dict[str, Any]:
    """§13이 요구한 항목. 이 결과로 모델을 고치지 않는다."""

    from . import validation as V

    truth = recession.reindex(path.index).fillna(False).astype(bool)
    official = path["official_phase"].astype(str)
    contraction = official.eq("contraction")
    status = path["phase_status"].astype(str)
    late_2019 = (path.index >= pd.Timestamp("2019-07-01")) & (
        path.index <= pd.Timestamp("2019-12-31")
    )
    pandemic = pd.Timestamp("2020-02-14")
    timing = V.signal_timing(
        official, path["recession_alert"].astype(str), path["raw_phase"].astype(str), pandemic
    )
    before = path.index < pandemic
    after = path.index > pd.Timestamp("2020-06-30")
    eligible = status.ne("withheld")
    return {
        "window": [str(path.index[0].date()), str(path.index[-1].date())],
        "weeks": int(len(path)),
        "eligible_weeks": int(eligible.sum()),
        "strict_start_rule": STRICT_START_RULE,
        "source": "로컬 빈티지 캐시. 네트워크·API 키 사용 없음.",
        "pandemic_timing": timing,
        "official_recession_weeks_as_contraction": int((contraction & truth).sum()),
        "recession_weeks_withheld": int((~eligible & truth).sum()),
        "official_recession_weeks": int(truth.sum()),
        "false_positives_before_recession": int((contraction & ~truth & before).sum()),
        "false_positives_after_recession": int((contraction & ~truth & after).sum()),
        "late_2019_official_contraction_weeks": int((contraction & late_2019).sum()),
        "false_positive_episodes_before_recession": len(
            V.episodes(pd.Series((contraction & ~truth & before).to_numpy(dtype=bool)))
        ),
        "false_positive_episodes_after_recession": len(
            V.episodes(pd.Series((contraction & ~truth & after).to_numpy(dtype=bool)))
        ),
        "phase_eligibility": {
            "official_weeks": int(status.eq("official").sum()),
            "preliminary_weeks": int(status.eq("preliminary").sum()),
            "withheld_weeks": int(status.eq("withheld").sum()),
        },
        "weeks_with_any_stale_domain": int(path["stale_domains"].astype(str).ne("").sum()),
        # 월간 자료 위에 주간 판정을 얹으면 발표 사이 주에는 값이 이월되는 것이 정상이다.
        # 그래서 "하나라도 이월"은 거의 모든 주에 참이라 신호가 없다. 의미 있는 것은
        # **주간 청구까지 포함해 전부** 이월된 주다 — 그 주에는 새 정보가 하나도 없다.
        "weeks_with_every_domain_carried_forward": int(
            path["carried_forward_domains"].astype(int).ge(len(DOMAINS)).sum()
        ),
        "median_domains_carried_forward": float(
            path["carried_forward_domains"].astype(int).median()
        ),
        "maximum_information_lag_weeks": int(path["information_lag_weeks"].astype(int).max()),
        "longest_panel_silence_weeks": int(
            path["weeks_since_any_new_observation"].astype(int).max()
        ),
        "withheld_weeks": int(path["withheld"].astype(int).sum()),
        "future_observation_violations": int(path["future_observations"].astype(int).sum()),
        "alert_character_counts": {
            key: int(value)
            for key, value in path["recession_alert_character"].value_counts().items()
        },
        "frozen_config_sha256": digest,
    }


def write(output: Path, payload: dict[str, Any]) -> None:
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
