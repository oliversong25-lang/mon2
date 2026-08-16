from __future__ import annotations

import numpy as np

from business_cycle.models.transition import filter_probabilities, transition_matrix


def test_contraction_late_wraps_to_recovery_early(settings):
    matrix = transition_matrix(12, settings.transitions["transition"])
    assert matrix[11, 0] == settings.transitions["transition"]["next"]
    assert matrix[0, 11] == settings.transitions["transition"]["previous"]


def test_transition_suppresses_unnecessary_multi_step_jump(settings):
    matrix = transition_matrix(12, settings.transitions["transition"])
    first = np.full(12, 1e-6)
    first[0] = 1.0
    second = np.full(12, 0.01)
    second[1] = 0.55
    second[6] = 0.60
    filtered = filter_probabilities(np.vstack([first / first.sum(), second / second.sum()]), matrix)
    assert filtered[1, 1] > filtered[1, 6]
    assert np.allclose(filtered.sum(axis=1), 1.0)
