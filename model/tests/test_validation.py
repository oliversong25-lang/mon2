from __future__ import annotations

import pandas as pd

from business_cycle.validation.real_data import (
    _objective_nber_metrics,
    _official_recession_flags,
)


def test_official_usrec_is_aligned_without_using_future_month() -> None:
    observations = pd.DataFrame(
        {
            "indicator_id": ["USREC", "USREC", "USREC"],
            "observation_period": ["2020-02-01", "2020-03-01", "2020-04-01"],
            "value": [0, 1, 1],
        }
    )
    weeks = pd.date_range("2020-02-07", "2020-04-24", freq="W-FRI")
    flags = _official_recession_flags(observations, weeks)
    assert not bool(flags.loc["2020-02-28"])
    assert bool(flags.loc["2020-03-06"])


def test_nber_metrics_do_not_claim_twelve_phase_accuracy() -> None:
    index = pd.date_range("2020-01-03", periods=12, freq="W-FRI")
    history = pd.DataFrame(
        {
            "broad_phase": ["expansion"] * 4 + ["contraction"] * 4 + ["recovery"] * 4,
        },
        index=index,
    )
    actual = pd.Series([False] * 4 + [True] * 4 + [False] * 4, index=index)
    metrics = _objective_nber_metrics(history, actual)
    assert metrics["recession_recall"] == 1.0
    assert metrics["recession_precision"] == 1.0
    assert "12개 세부국면 정확도는 계산하지 않음" in metrics["note"]
