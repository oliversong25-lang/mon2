from __future__ import annotations

import numpy as np

from business_cycle.models.confidence import data_confidence, detail_confidence
from business_cycle.models.phase import phase_definitions


def test_near_origin_has_lower_detail_confidence(settings):
    phases = phase_definitions(settings.transitions["phases"])
    probabilities = np.array([0.4, 0.3, 0.1, *([0.02] * 9)])
    probabilities /= probabilities.sum()
    config = settings.model["confidence"]
    near = detail_confidence(probabilities, 0, 285, 0.05, 0.8, 1.0, phases, config)
    far = detail_confidence(probabilities, 0, 285, 2.0, 0.8, 1.0, phases, config)
    assert near < far


def test_small_top_two_gap_has_lower_detail_confidence(settings):
    phases = phase_definitions(settings.transitions["phases"])
    config = settings.model["confidence"]
    close = np.array([0.34, 0.33, *([0.033] * 10)])
    close /= close.sum()
    clear = np.array([0.60, 0.12, *([0.028] * 10)])
    clear /= clear.sum()
    assert detail_confidence(close, 0, 285, 1, 0.8, 1, phases, config) < detail_confidence(
        clear, 0, 285, 1, 0.8, 1, phases, config
    )


def test_missing_core_data_lowers_data_confidence(settings):
    config = settings.model["confidence"]
    full, _ = data_confidence(0.95, 1.0, None, 0.9, config)
    missing, detail = data_confidence(0.70, 0.57, None, 0.7, config)
    assert missing < full
    assert detail["revision_stability"] is None
    assert detail["revision_assumption"] == config["conservative_revision_default"]
