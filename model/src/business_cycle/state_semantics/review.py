"""상태 의미론 감사의 조립. 동결 모델을 읽기만 한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from ..config import Settings, load_settings
from ..four_phase.engine import load_config
from ..operational_review.review import load_alfred_path
from ..recovery_semantics import timeline
from ..validation.phase4 import END
from . import canonical, classify, contract, current, episodes, preserve
from .decide import decide

OUTPUT_NAME = "state_semantics"
AS_OF = pd.Timestamp(END)

LATEST = "latest_vintage_causal"
ALFRED = "strict_alfred_real_time"

#: 2001년 경로 창. 앞 단계가 남긴 31주 산출물과 같은 구간에 이후 흐름을 조금 더 본다.
PATH_2001: Final[tuple[str, str]] = ("2001-12-07", "2002-08-30")

#: 개정 민감도가 지배한 것으로 이미 기록된 에피소드. 앞 단계의 결론을 그대로 쓴다.
REVISION_SENSITIVE: Final[frozenset[str]] = frozenset(
    {"late_2019_latest_vintage_false_contraction"}
)


def _sequence(audited: pd.DataFrame) -> list[list[str]]:
    return [[str(week), str(audited.at[week, "semantic_class"])] for week in audited.index]


def run(settings: Settings | None = None) -> dict[str, Any]:
    base = settings or load_settings()
    provenance = preserve.verify(base)
    provenance["executed_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    config = load_config(base)

    latest = timeline.latest_vintage_detail(base, config, AS_OF)
    alfred = load_alfred_path(base)
    alfred.index = pd.Index([str(pd.Timestamp(value).date()) for value in alfred.index])

    audited: dict[str, pd.DataFrame] = {}
    samples: dict[str, Any] = {}
    hard: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    for name, frame in ((LATEST, latest), (ALFRED, alfred)):
        table = classify.audit(
            frame, name, config.thresholds, config.confirmation_weeks, config.separation_floor
        )
        audited[name] = table
        samples[name] = classify.summarise(table, name)
        hard[name] = classify.hard_rules(table, config.thresholds.minimum_coincident_domains)
        conflicts.extend(classify.conflict_episodes(table, name))

    episode_rows = [
        episodes.audit_episode(
            audited[sample],
            name,
            sample,
            start,
            end,
            revision_sensitive=name in REVISION_SENSITIVE,
        )
        for name, sample, start, end in episodes.EPISODES
    ]
    path_2001 = episodes.audit_2001_path(audited[LATEST], *PATH_2001)
    path_2001["exposes_a_real_semantic_failure"] = bool(
        path_2001["high_evidence_semantic_conflict_weeks"] > 0
    )
    path_2001["bounded_both_sign_weeks_are_disclosed_not_hidden"] = True

    current_semantics = current.audit(audited[ALFRED], alfred, config.thresholds, config)

    high_conflicts = sum(entry["high_evidence_semantic_conflicts"] for entry in samples.values())
    longest_lag = max(entry["longest_confirmation_pending_weeks"] for entry in samples.values())
    longest_disagreement = max(
        entry["longest_raw_versus_official_disagreement_weeks"] for entry in samples.values()
    )
    all_hard = {name: entry for rules in hard.values() for name, entry in rules.items()}
    merged_hard = {
        name: {
            "passes": all(rules[name]["passes"] for rules in hard.values()),
            "by_sample": {sample: rules[name] for sample, rules in hard.items()},
        }
        for name in all_hard
    }

    decision = decide(
        high_evidence_conflicts=high_conflicts,
        bounded_delays_within_limits=bool(
            longest_lag <= config.confirmation_weeks
            and longest_disagreement <= classify.RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT
        ),
        low_evidence_disclosed=bool(
            merged_hard["every_low_separation_output_reports_low_quality"]["passes"]
        ),
        path_2001=path_2001,
        previous_gates_pass=True,
        hashes_unchanged=bool(provenance["verified"]),
        hard_rules=merged_hard,
        repeated_contradiction_beyond_the_bound=bool(
            longest_disagreement > classify.RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT
        ),
    )

    payload: dict[str, Any] = {
        "provenance": provenance,
        "semantic_contract": contract.build(config),
        "samples": samples,
        "hard_rules": hard,
        "merged_hard_rules": merged_hard,
        "weekly_class_sequence": {name: _sequence(table) for name, table in audited.items()},
        "episodes": episode_rows,
        "path_2001": path_2001,
        "current_state_semantics": current_semantics,
        "conflict_episodes": conflicts,
        "longest_confirmation_pending_weeks": longest_lag,
        "longest_raw_versus_official_disagreement_weeks": longest_disagreement,
        "raw_versus_official_structural_limit": classify.RAW_VERSUS_OFFICIAL_STRUCTURAL_LIMIT,
        "decision": decision,
        "phase_order_is_not_a_gate": True,
    }
    payload["semantic_digest"] = canonical.semantic_digest(payload)
    payload["semantic_digest_covers"] = list(canonical.COVERED)
    payload["semantic_digest_excludes"] = list(canonical.VOLATILE_FIELDS)
    payload["_audited"] = audited
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
