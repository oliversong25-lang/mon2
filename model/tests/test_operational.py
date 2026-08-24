from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

from business_cycle.config import load_settings
from business_cycle.four_phase.contract import PHASES
from business_cycle.operational import (
    BASELINE_DATE,
    ReproducibilityError,
    assert_causal,
    run,
    verify_protected_state,
    write_outputs,
)


def test_protected_model_and_decisions_are_intact() -> None:
    verified = verify_protected_state()
    assert verified["verified"] is True
    assert verified["protected_paths_clean"] is True
    assert verified["hashes"]["v1_1_config"].startswith("e052a4f4")


def test_one_run_reproduces_baseline_and_writes_three_outputs(tmp_path: Path) -> None:
    payload = run(BASELINE_DATE, cache_only=True, output_dir=tmp_path)
    assert payload["official_current_phase"] == "expansion"
    assert payload["raw_current_phase"] == "expansion"
    assert payload["evidence_quality"] == "low"
    assert payload["phase_status"] == "official"
    assert payload["future_information_violation_count"] == 0
    assert set(payload["raw_phase_scores"]) == set(PHASES)
    assert set(payload["filtered_phase_scores"]) == set(PHASES)
    assert [row["rank"] for row in payload["phase_ranking"]] == [1, 2, 3, 4]
    assert all(
        {
            "supporting_domains",
            "opposing_domains",
            "mixed_domains",
            "breadth",
            "evidence_quality",
        }
        <= set(row)
        for row in payload["phase_ranking"]
    )
    assert {path.name for path in tmp_path.iterdir()} == {"latest.json", "latest.md", "history.csv"}


def test_same_date_is_deterministic_and_history_is_append_only(tmp_path: Path) -> None:
    first = run(BASELINE_DATE, cache_only=True, output_dir=tmp_path)
    second = run(BASELINE_DATE, cache_only=True, output_dir=tmp_path)
    assert first["result_digest"] == second["result_digest"]
    with (tmp_path / "history.csv").open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 1

    changed = json.loads(json.dumps(second))
    changed["activity_level"] = 999.0
    with pytest.raises(ReproducibilityError):
        write_outputs(changed, tmp_path)


def test_explicit_as_of_rejects_future_observations() -> None:
    observations = pd.DataFrame({"release_date": ["2026-08-14", "2026-08-15"], "value": [1.0, 2.0]})
    with pytest.raises(Exception, match="미래 관측 1건"):
        assert_causal(observations, pd.Timestamp("2026-08-14"))


def test_outputs_contain_no_secret_or_action_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = "SECRET_MARKER_FOR_TEST"
    monkeypatch.setenv("FRED_API_KEY", marker)
    payload = run(BASELINE_DATE, cache_only=True, output_dir=tmp_path)
    text = json.dumps(payload).lower()
    assert marker.lower() not in text
    assert "recommendation" not in text
    assert "target_price" not in text
    assert payload["recession_alert"]["role"] == "secondary_validation_signal"


def test_configuration_root_is_unchanged() -> None:
    settings = load_settings()
    assert settings.root.name == "model"
