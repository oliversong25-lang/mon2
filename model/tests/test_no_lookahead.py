from __future__ import annotations

import pandas as pd

from business_cycle.pipeline import run_pipeline


def test_future_extreme_values_do_not_change_past_nowcast(settings, synthetic_data):
    cutoff = pd.Timestamp("2018-12-31")
    changed = synthetic_data.copy()
    future = pd.to_datetime(changed["release_date"]) > cutoff
    changed.loc[future, "value"] = changed.loc[future, "value"] * 10_000
    original_run = run_pipeline(synthetic_data, settings, cutoff)
    changed_run = run_pipeline(changed, settings, cutoff)
    pd.testing.assert_frame_equal(original_run.history, changed_run.history)
    assert original_run.result.to_dict() == changed_run.result.to_dict()
