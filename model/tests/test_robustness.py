from __future__ import annotations

import numpy as np
import pandas as pd

from business_cycle.models.composite import CompositeFactorModel
from business_cycle.models.confidence import data_confidence
from business_cycle.validation.robustness import _settings_transition_value


def test_weight_caps_survive_missing_indicator_renormalization(settings) -> None:
    index = pd.date_range("2020-01-03", periods=12, freq="W-FRI")
    columns = list(settings.indicators["indicators"])
    events = pd.DataFrame(np.nan, index=index, columns=columns)
    events.loc[index[0], columns] = 1.0
    events.loc[index[6], "PAYEMS"] = 2.0
    events.loc[index[8], "ICSA"] = -1.0
    estimate = CompositeFactorModel(
        settings.indicators["indicators"], settings.indicators["constraints"]
    ).fit_filter(events)
    weights = estimate.metadata["effective_weights"]
    assert isinstance(weights, dict)
    for weekly_weights in weights.values():
        assert isinstance(weekly_weights, dict)
        assert max(weekly_weights.values()) <= 0.200000001
        domains: dict[str, float] = {}
        for indicator, weight in weekly_weights.items():
            domain = settings.indicators["indicators"][indicator]["domain"]
            domains[domain] = domains.get(domain, 0.0) + float(weight)
        assert max(domains.values()) <= 0.300000001


def test_data_confidence_falls_when_availability_falls(settings) -> None:
    config = settings.model["confidence"]
    complete, _ = data_confidence(0.8, 1.0, None, 0.6, config)
    missing, _ = data_confidence(0.8, 0.5, None, 0.6, config)
    assert missing < complete


def test_transition_sensitivity_keeps_probability_sum(settings) -> None:
    changed = _settings_transition_value(settings, "stay", 0.72)
    probabilities = changed.transitions["transition"]
    assert np.isclose(sum(float(value) for value in probabilities.values()), 1.0)
    assert probabilities["stay"] == 0.72
