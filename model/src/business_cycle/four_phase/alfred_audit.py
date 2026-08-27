"""동결된 **기각 모델**의 엄격 ALFRED 감사. 채택 절차가 아니다.

v1.1은 후반 2019 게이트에서 실패해 기각됐고, 이 감사는 그 판정을 바꾸지 않는다.
답하려는 질문은 하나뿐이다 — **2019년 11월의 거짓 침체 호출이 그 시점에 실제로
있었던 정보로도 나왔는가, 아니면 나중의 수정치가 만든 것인가.**

2019년 11월의 침체 호출을 2020년 팬데믹 침체의 성공적 예측으로 셈하지 않는다.
그것은 사후 재라벨링이다. 2020년 침체를 몰고 온 것은 그 뒤에 온 외생 충격이다.

모델 로직·설정·점수·전이·확인·경보·신선도 문턱을 하나도 건드리지 않는다. 이 모듈은
기록만 늘린다.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ..config import Settings, load_baseline, load_settings
from ..current_state.domains import DOMAINS
from ..data.alfred import observations_as_of, slice_vintage
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import alfred as AL
from . import contract as C
from . import freshness as FRESH
from . import validation as V
from .engine import STOPPED_CONFIG_NAME, FourPhaseConfig, load_config, prepare, score

OUTPUT_NAME = "alfred_audit"
FROZEN_BASELINE = "candidate_h_breadth_gate"
AS_OF = pd.Timestamp(END)

#: 감사 대상 창. 2019년 후반 거짓 침체와 2020년 침체를 모두 덮는다.
LATE_2019 = (pd.Timestamp("2019-07-01"), pd.Timestamp("2020-04-30"))
COMPARISON = (pd.Timestamp("2019-07-01"), pd.Timestamp("2020-06-30"))
NBER_2020 = (pd.Timestamp("2020-02-01"), pd.Timestamp("2020-04-30"))

#: 실행 전에 반드시 일치해야 하는 지문. 하나라도 어긋나면 멈춘다.
EXPECTED: Final[dict[str, str]] = {
    "config": "e052a4f41ca2d01431bab32e6df8bbd383ea9a2dab09982a6675e789bcc3265a",
    "stopped_config": "892fbbfb4b9f72f1611097354298380b14875e955a6f3b0e36a47376c2b53027",
    "selection_rule": "71647ea8e81951229d67f1556bc504aa0f38877ca713c069b8b68a20a88cbcd0",
    "candidate_h": "c367e2a0f8e907b6f927191f03379bab5ea5eace6b671454c4b63e44d4b2bb21",
    "candidate_i": "765e2ee65b70a185159faa928c2df9c734c19e583dc8655ae47c80ec3d056993",
    "candidate_j": "a0d875268f1d720a29659f96695e74391db7fd9a3a0213b8c8970e6399a6098f",
}


class ProvenanceMismatch(RuntimeError):
    """동결 대상이 기대와 다르다. 감사를 시작하지 않는다."""


def verify_provenance(settings: Settings) -> dict[str, Any]:
    """§2. 실행 전에 무엇을 감사하는지 못박는다."""

    from . import frontier as FR

    config = load_config(settings)
    stopped = load_config(settings, STOPPED_CONFIG_NAME)
    root = settings.root
    measured = {
        "config": config.sha256,
        "stopped_config": stopped.sha256,
        "selection_rule": FR.selection_rule_digest(),
        "candidate_h": json.loads(
            (root / "outputs/robustness_validation/phase6/validation_summary.json").read_text(
                encoding="utf-8"
            )
        )["frozen_hash"],
        "candidate_i": (root / "outputs/current_state/frozen_candidate_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0],
        "candidate_j": (root / "outputs/candidate_j/frozen_candidate_config.sha256")
        .read_text(encoding="utf-8")
        .split()[0],
    }
    mismatched = {k: (measured[k], v) for k, v in EXPECTED.items() if measured[k] != v}
    if mismatched:
        raise ProvenanceMismatch(f"동결 지문이 어긋났습니다: {mismatched}")
    artifact = root / "outputs/four_phase_v1_1/development_frontier.json"
    document = json.loads(artifact.read_text(encoding="utf-8"))
    return {
        "verified": True,
        **measured,
        "source_commit": FR.source_commit(),
        "frontier_artifact_sha256": document.get("frontier_csv_sha256"),
        "frontier_generated_at_utc": document.get("generated_at_utc"),
        "model_status": "rejected",
        "audit_purpose": (
            "기각된 동결 모델의 실시간 감사. 채택 절차가 아니며 기각 판정을 바꾸지 않는다."
        ),
    }


@dataclass(frozen=True)
class AuditInputs:
    settings: Settings
    config: FourPhaseConfig
    baseline: Settings
    frames: dict[str, pd.DataFrame]


def load_audit_inputs(settings: Settings | None = None) -> AuditInputs:
    base = settings or load_settings()
    verify_provenance(base)
    return AuditInputs(
        settings=base,
        config=load_config(base),
        baseline=load_baseline("candidate_h_breadth_gate", base),
        frames=AL.cached_frames(base),
    )


def audit_week(inputs: AuditInputs, vintage: pd.Timestamp) -> dict[str, Any]:
    """한 as-of 시점의 전체 기록. 생산 경로와 같은 함수만 부른다."""

    settings, config = inputs.settings, inputs.config
    observations = observations_as_of(inputs.frames, vintage, settings.indicators["indicators"])
    future = int((observations["release_date"] > vintage).sum())
    prepared = prepare(observations, inputs.baseline, vintage, config)
    row: dict[str, Any] = {"as_of": str(vintage.date()), "future_observations": future}
    if len(prepared.index) == 0:
        row.update({"phase_status": "withheld", "official_phase": "", "raw_phase": ""})
        return row

    eligibility = FRESH.evaluate(
        vintage, prepared.index, prepared.weeks_since_release, prepared.arrived, config.freshness
    )
    run = score(prepared, config)
    week = run.official_phase.index[-1]
    withheld = eligibility.withheld

    scores = {name: float(str(run.filtered_scores.at[week, name])) for name in C.PHASES}
    ordered = sorted(scores.values(), reverse=True)
    level_row = run.level_scaled.loc[week]
    magnitude = level_row.abs()
    total = float(magnitude.sum())
    dominant = str(magnitude.idxmax()) if total > 0 else ""

    # 도메인별 최신 관측일과 새 판본 도착 여부는 빈티지에서 직접 읽는다.
    latest_observation: dict[str, str] = {}
    for series_id, frame in inputs.frames.items():
        visible = slice_vintage(frame, vintage)
        if not visible.empty:
            latest_observation[series_id] = str(pd.Timestamp(visible["date"].max()).date())

    row.update(
        {
            "last_modelled_week": str(pd.Timestamp(str(week)).date()),
            "raw_phase": str(run.raw_phase.loc[week]),
            "official_phase": "" if withheld else str(run.official_phase.loc[week]),
            "phase_status": eligibility.status,
            "activity_level": float(str(run.activity_level.loc[week])),
            "activity_momentum": float(str(run.activity_momentum.loc[week])),
            "negative_level_domains": int(str(run.negative_level_domains.loc[week])),
            "negative_momentum_domains": int(str(run.negative_momentum_domains.loc[week])),
            "positive_momentum_domains": int(str(run.positive_momentum_domains.loc[week])),
            "confirming_domains": int(str(run.confirming_domains.loc[week])),
            "concentration": float(str(run.concentration.loc[week])),
            "dominant_domain": dominant,
            "labor_stress_level": float(str(run.level_scaled.at[week, "labor_stress"])),
            "labor_stress_momentum": float(str(run.momentum_scaled.at[week, "labor_stress"])),
            "recession_alert": str(run.alert_level.loc[week]),
            "recession_alert_character": str(run.alert_character.loc[week]),
            "evidence_quality_high": bool(run.evidence_quality_high.loc[week]),
            "phase_separation": ordered[0] - ordered[1],
            "confirmation_pending": int(str(run.confirmation_pending.loc[week])),
            "filtered_winner": str(run.filtered_winner.loc[week]),
            "contraction_evidence": float(
                str(run.contraction_detail.at[week, "contraction_evidence"])
            ),
            "alert_evidence": float(str(run.contraction_detail.at[week, "alert_evidence"])),
            "information_lag_weeks": eligibility.information_lag_weeks,
            "weeks_since_any_new_observation": eligibility.weeks_since_any_new_observation,
            "fresh_coincident_domains": eligibility.fresh_coincident_domains,
            "stale_domains": "|".join(eligibility.stale_domains),
            "carried_forward_domains": sum(
                1 for value in eligibility.domain_carried_forward.values() if value
            ),
            "withheld": int(withheld),
        }
    )
    for name in C.PHASES:
        row[f"raw_{name}"] = float(str(run.raw_scores.at[week, name]))
        row[f"filtered_{name}"] = scores[name]
    for domain in DOMAINS:
        row[f"age_{domain}"] = float(str(run.weeks_since_release.at[week, domain]))
        row[f"arrived_{domain}"] = int(bool(run.arrived.at[week, domain]))
    for series_id, observed in latest_observation.items():
        row[f"latest_observation_{series_id}"] = observed
    return row


def run_audit(
    inputs: AuditInputs,
    output: Path,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    progress_every: int = 25,
) -> pd.DataFrame:
    """688주 실시간 경로. 캐시만 쓰고, 중단되면 이어서 돌린다."""

    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "weekly_path.checkpoint.csv"
    weeks = pd.date_range(start or AL.STRICT_START, end or AS_OF, freq="W-FRI")
    done: dict[str, dict[str, Any]] = {}
    if checkpoint.exists():
        previous = pd.read_csv(checkpoint)
        done = {str(row["as_of"]): dict(row) for _, row in previous.iterrows()}
        print(f"  체크포인트에서 {len(done)}주를 이어받습니다", flush=True)

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    handle = checkpoint.open("a", encoding="utf-8", newline="")
    header_written = checkpoint.stat().st_size > 0
    try:
        for position, vintage in enumerate(weeks, start=1):
            key = str(vintage.date())
            if key in done:
                rows.append(done[key])
                continue
            row = audit_week(inputs, vintage)
            rows.append(row)
            if not header_written:
                handle.write(",".join(str(name) for name in row) + "\n")
                header_written = True
            handle.write(",".join(str(value) for value in row.values()) + "\n")
            handle.flush()
            if position % progress_every == 0 or position == len(weeks):
                elapsed = time.monotonic() - started
                rate = elapsed / max(position - len(done), 1)
                remaining = rate * (len(weeks) - position)
                print(
                    f"  감사 {position}/{len(weeks)}주 · {key} · 경과 {elapsed / 60:.1f}분 · "
                    f"주당 {rate:.2f}초 · 남은 예상 {remaining / 60:.1f}분",
                    flush=True,
                )
    finally:
        handle.close()
    frame = pd.DataFrame(rows)
    frame["as_of"] = pd.to_datetime(frame["as_of"])
    return frame.set_index("as_of").sort_index()


def episodes_outside_recessions(path: pd.DataFrame, recession: pd.Series) -> list[dict[str, Any]]:
    """NBER 침체 밖의 공식 침체 구간. 주간 오탐률 안에 묻지 않는다."""

    truth = recession.reindex(path.index).fillna(False).astype(bool)
    predicted = path["official_phase"].astype(str).eq("contraction")
    weeks = list(path.index)
    spans = V.episodes(pd.Series((predicted & ~truth).to_numpy(dtype=bool)))
    truth_spans = V.episodes(truth)
    out: list[dict[str, Any]] = []
    for start, end in spans:
        duration = end - start
        moment = weeks[min(range(start, end), key=lambda i: float(path["activity_level"].iloc[i]))]
        distances = [min(abs(start - a), abs(end - b)) for a, b in truth_spans]
        out.append(
            {
                "start_date": str(pd.Timestamp(weeks[start]).date()),
                "end_date": str(pd.Timestamp(weeks[end - 1]).date()),
                "duration_weeks": duration,
                "kind": (
                    "four_week_confirmed"
                    if duration >= 4
                    else ("short_preliminary_signal" if duration > 1 else "isolated_week")
                ),
                "four_week_confirmed": duration >= 4,
                "negative_level_domains": int(path["negative_level_domains"].loc[moment]),
                "negative_momentum_domains": int(path["negative_momentum_domains"].loc[moment]),
                "confirming_domains": int(path["confirming_domains"].loc[moment]),
                "concentration": round(float(path["concentration"].loc[moment]), 4),
                "dominant_domain": str(path["dominant_domain"].loc[moment]),
                "recession_alert": str(path["recession_alert"].loc[moment]),
                "recession_alert_character": str(path["recession_alert_character"].loc[moment]),
                "weeks_to_nearest_nber_recession": min(distances) if distances else None,
            }
        )
    return out


def latest_vintage_path(settings: Settings, config: FourPhaseConfig) -> pd.DataFrame:
    """비교용 최신 수정치 경로. 같은 동결 설정으로 만든다."""

    core, source = load_core_observations(settings)
    prepared = prepare(core, load_baseline("candidate_h_breadth_gate", settings), AS_OF, config)
    run = score(prepared, config)
    recession = _official_recession_flags(source, prepared.index)
    frame = pd.DataFrame(
        {
            "raw_phase": run.raw_phase,
            "official_phase": run.official_phase,
            "activity_level": run.activity_level,
            "activity_momentum": run.activity_momentum,
            "negative_level_domains": run.negative_level_domains,
            "negative_momentum_domains": run.negative_momentum_domains,
            "confirming_domains": run.confirming_domains,
            "concentration": run.concentration,
            "recession_alert": run.alert_level,
            "usrec": recession.astype(int),
        }
    )
    for name in C.PHASES:
        frame[f"raw_{name}"] = run.raw_scores[name]
        frame[f"filtered_{name}"] = run.filtered_scores[name]
    return frame


def classify_disagreement(row: pd.Series) -> str:
    """불일치 하나하나의 주된 원인. 하나의 뭉뚱그린 숫자로 합치지 않는다."""

    if str(row["alfred_status"]) == "withheld":
        return "freshness_eligibility"
    if int(row["information_lag_weeks"]) > 0:
        return "missing_current_observation"
    if str(row["alfred_raw"]) != str(row["latest_raw"]):
        if (
            abs(float(row["level_difference"])) > 0.05
            or abs(float(row["momentum_difference"])) > 0.05
        ):
            return "data_revision"
        return "score_boundary_sensitivity"
    # 원시 국면은 같은데 공식 국면이 다르면 필터·확인 규칙의 경로 의존성이다.
    return "filter_path_dependence"


def compare(
    path: pd.DataFrame, latest: pd.DataFrame, window: tuple[pd.Timestamp, pd.Timestamp]
) -> pd.DataFrame:
    """§6. 최신 수정치와 실시간을 주별로 맞대어 본다."""

    index = path.index[(path.index >= window[0]) & (path.index <= window[1])]
    rows: list[dict[str, Any]] = []
    for moment in index:
        # 그 as-of 시점에 모델이 실제로 본 마지막 주를 최신 수정치에서 찾아 맞댄다.
        modelled = pd.Timestamp(str(path["last_modelled_week"].loc[moment]))
        if modelled not in latest.index:
            continue
        reference = latest.loc[modelled]
        row = {
            "as_of": str(pd.Timestamp(moment).date()),
            "last_modelled_week": str(modelled.date()),
            "information_lag_weeks": int(path["information_lag_weeks"].loc[moment]),
            "alfred_raw": str(path["raw_phase"].loc[moment]),
            "latest_raw": str(reference["raw_phase"]),
            "alfred_official": str(path["official_phase"].loc[moment]),
            "latest_official": str(reference["official_phase"]),
            "alfred_status": str(path["phase_status"].loc[moment]),
            "alfred_alert": str(path["recession_alert"].loc[moment]),
            "latest_alert": str(reference["recession_alert"]),
            "level_difference": float(path["activity_level"].loc[moment])
            - float(str(reference["activity_level"])),
            "momentum_difference": float(path["activity_momentum"].loc[moment])
            - float(str(reference["activity_momentum"])),
            "breadth_difference": int(path["negative_level_domains"].loc[moment])
            - int(str(reference["negative_level_domains"])),
            "concentration_difference": float(path["concentration"].loc[moment])
            - float(str(reference["concentration"])),
            "usrec": int(str(reference["usrec"])),
        }
        for name in C.PHASES:
            row[f"raw_{name}_difference"] = float(path[f"raw_{name}"].loc[moment]) - float(
                str(reference[f"raw_{name}"])
            )
        row["raw_agrees"] = row["alfred_raw"] == row["latest_raw"]
        row["official_agrees"] = row["alfred_official"] == row["latest_official"]
        row["alert_agrees"] = row["alfred_alert"] == row["latest_alert"]
        row["disagreement_cause"] = (
            "" if row["official_agrees"] else classify_disagreement(pd.Series(row))
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _first(mask: pd.Series, index: pd.DatetimeIndex) -> str | None:
    hits = index[mask.to_numpy(dtype=bool)]
    return str(pd.Timestamp(hits[0]).date()) if len(hits) else None


def _weeks_from(date: str | None, start: pd.Timestamp) -> int | None:
    return None if date is None else int((pd.Timestamp(date) - start).days // 7)


def summarise(
    path: pd.DataFrame, recession: pd.Series, provenance: dict[str, Any]
) -> dict[str, Any]:
    """감사 요약. 채택 판정이 아니다."""

    truth = recession.reindex(path.index).fillna(False).astype(bool)
    official = path["official_phase"].astype(str)
    raw = path["raw_phase"].astype(str)
    status = path["phase_status"].astype(str)
    contraction = official.eq("contraction")
    index = pd.DatetimeIndex(path.index)

    start_2020 = pd.Timestamp("2020-02-07")
    in_2020 = (index >= NBER_2020[0]) & (index <= NBER_2020[1])
    before_2020 = index < NBER_2020[0]
    after_2020 = index > NBER_2020[1]
    late_2019 = (index >= pd.Timestamp("2019-07-01")) & (index <= pd.Timestamp("2019-12-31"))

    confirmed = (
        contraction
        & contraction.shift(1, fill_value=False)
        & contraction.shift(2, fill_value=False)
        & contraction.shift(3, fill_value=False)
    )

    return {
        "heading": "Frozen rejected-model ALFRED audit",
        "model_status": "rejected",
        "provenance": provenance,
        "window": [str(index[0].date()), str(index[-1].date())],
        "weeks": int(len(path)),
        "phase_eligibility": {
            "official_weeks": int(status.eq("official").sum()),
            "preliminary_weeks": int(status.eq("preliminary").sum()),
            "withheld_weeks": int(status.eq("withheld").sum()),
        },
        "future_observation_violations": int(path["future_observations"].astype(int).sum()),
        "maximum_information_lag_weeks": int(path["information_lag_weeks"].astype(int).max()),
        "longest_panel_silence_weeks": int(
            path["weeks_since_any_new_observation"].astype(int).max()
        ),
        "late_2019": {
            "official_contraction_weeks": int((contraction & late_2019).sum()),
            "four_week_confirmed_weeks": int((confirmed & late_2019).sum()),
            "first_official_contraction": _first(contraction & late_2019, index),
            "first_raw_contraction": _first(raw.eq("contraction") & late_2019, index),
            "first_alert": _first(
                path["recession_alert"].astype(str).isin(("elevated", "high")) & late_2019, index
            ),
        },
        "recession_2020": {
            "nber_start_used": str(start_2020.date()),
            "first_alert": _first(path["recession_alert"].astype(str).ne("none"), index),
            "first_high_alert": _first(path["recession_alert"].astype(str).eq("high"), index),
            "first_raw_contraction": _first(raw.eq("contraction") & (index >= start_2020), index),
            "first_official_contraction": _first(contraction & (index >= start_2020), index),
            "first_four_week_confirmed": _first(confirmed & (index >= start_2020), index),
            "official_recession_weeks": int((truth).sum()),
            "recession_weeks_as_contraction": int((contraction & truth).sum()),
            "recession_weeks_withheld": int((status.eq("withheld") & truth).sum()),
            "first_recovery_signal": _first(
                official.eq("recovery") & (index >= NBER_2020[0]), index
            ),
            "phase_at_trough": str(official.loc[index[in_2020][-1]]) if in_2020.any() else None,
        },
        "false_contraction": {
            "before_the_recession_weeks": int((contraction & ~truth & before_2020).sum()),
            "after_the_recession_weeks": int((contraction & ~truth & after_2020).sum()),
        },
        "breadth": {
            "official_contraction_weeks": int(contraction.sum()),
            "with_fewer_than_two_confirming_domains": int(
                (contraction & path["confirming_domains"].astype(int).lt(2)).sum()
            ),
            "concentrated_alert_became_official_contraction": int(
                (
                    contraction
                    & path["recession_alert_character"].astype(str).eq("severe_but_concentrated")
                ).sum()
            ),
        },
        "cache": {
            "network_used": False,
            "api_key_used": False,
            "latest_vintage_substitution": False,
            "backward_fill_used": False,
            "future_vintage_used": False,
        },
    }


def revision_impact(comparison: pd.DataFrame) -> dict[str, Any]:
    """§9. 수정치가 무엇을 바꿨는지."""

    if comparison.empty:
        return {"weeks": 0}
    causes: dict[str, int] = {}
    for value in comparison.loc[~comparison["official_agrees"], "disagreement_cause"]:
        causes[str(value)] = causes.get(str(value), 0) + 1
    disagree = ~comparison["official_agrees"]
    longest = streak = 0
    for value in disagree:
        streak = streak + 1 if bool(value) else 0
        longest = max(longest, streak)
    year = pd.to_datetime(comparison["as_of"]).dt.year
    return {
        "weeks": int(len(comparison)),
        "raw_phase_agreement": round(float(comparison["raw_agrees"].mean()), 6),
        "official_phase_agreement": round(float(comparison["official_agrees"].mean()), 6),
        "contraction_agreement": round(
            float(
                (
                    comparison["alfred_official"].eq("contraction")
                    == comparison["latest_official"].eq("contraction")
                ).mean()
            ),
            6,
        ),
        "alert_agreement": round(float(comparison["alert_agrees"].mean()), 6),
        "weeks_where_revisions_changed_the_official_phase": int(disagree.sum()),
        "weeks_where_revisions_changed_the_alert": int((~comparison["alert_agrees"]).sum()),
        "longest_disagreement_run": longest,
        "disagreements_in_2019": int((disagree & year.eq(2019)).sum()),
        "disagreements_in_2020": int((disagree & year.eq(2020)).sum()),
        "disagreement_causes": causes,
        "mean_level_difference": round(float(comparison["level_difference"].mean()), 6),
        "mean_momentum_difference": round(float(comparison["momentum_difference"].mean()), 6),
    }


def classify_outcome(summary: dict[str, Any]) -> dict[str, Any]:
    """§11. 결과를 네 갈래 중 정확히 하나로 분류한다."""

    late = summary["late_2019"]
    twenty = summary["recession_2020"]
    confirmed_false = int(late["four_week_confirmed_weeks"]) > 0
    first_official = twenty["first_official_contraction"]
    weeks_late = _weeks_from(first_official, pd.Timestamp(twenty["nber_start_used"]))
    detects_2020 = (
        weeks_late is not None
        and weeks_late <= 10
        and int(twenty["recession_weeks_as_contraction"]) >= 1
    )
    withheld_in_window = int(twenty["recession_weeks_withheld"]) > 0

    if withheld_in_window:
        letter, text = "C", "판정 보류 주가 침체 구간에 걸쳐 실시간 판단을 확정할 수 없다"
    elif confirmed_false:
        letter, text = (
            "A",
            "실시간에서도 후반 2019 거짓 침체가 4주 이상 확인됐다. "
            "수정치 탓이 아니라 운영상의 문제다",
        )
    elif not detects_2020:
        letter, text = "D", "후반 2019는 사라졌으나 2020년 실시간 탐지가 요건을 못 맞춘다"
    else:
        letter, text = (
            "B",
            "실시간에는 확인된 후반 2019 거짓 침체가 없고 2020년은 요건 안에서 탐지된다",
        )
    return {
        "classification": letter,
        "statement": text,
        "confirmed_late_2019_false_contraction_in_real_time": confirmed_false,
        "weeks_to_first_official_contraction_2020": weeks_late,
        "detects_2020_within_requirements": detects_2020,
    }


def _write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    settings = load_settings()
    inputs = load_audit_inputs(settings)
    output = settings.root / "outputs" / "four_phase_v1_1" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    provenance = verify_provenance(settings)
    _write(output / "provenance.json", provenance)
    print("동결 지문 확인 완료. 감사를 시작합니다.", flush=True)

    cache = AL.cache_audit(settings, AS_OF)
    _write(output / "cache_audit.json", cache)

    path = run_audit(inputs, output)
    path.to_csv(output / "weekly_path.csv")

    core, source = load_core_observations(settings)
    recession = _official_recession_flags(source, pd.DatetimeIndex(path.index))
    summary = summarise(path, recession, provenance)
    summary["cache_audit"] = {k: v for k, v in cache.items() if k != "weeks_to_withhold"}

    latest = latest_vintage_path(settings, inputs.config)
    comparison = compare(path, latest, COMPARISON)
    comparison.to_csv(output / "latest_versus_realtime.csv", index=False)
    summary["revision_impact"] = revision_impact(comparison)

    episodes = episodes_outside_recessions(path, recession)
    _write(output / "false_contraction_episodes.json", episodes)
    summary["false_contraction_episodes"] = {
        "count": len(episodes),
        "four_week_confirmed": sum(1 for item in episodes if item["four_week_confirmed"]),
        "isolated_weeks": sum(1 for item in episodes if item["kind"] == "isolated_week"),
        "short_preliminary": sum(
            1 for item in episodes if item["kind"] == "short_preliminary_signal"
        ),
    }

    window = path.index[(path.index >= LATE_2019[0]) & (path.index <= LATE_2019[1])]
    path.loc[window].to_csv(output / "late_2019_weekly_path.csv")
    summary["outcome"] = classify_outcome(summary)
    summary["adoption_unchanged"] = "rejected"
    _write(output / "audit_summary.json", summary)

    print(json.dumps(summary["outcome"], ensure_ascii=False, indent=2))
    print(f"산출물: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
