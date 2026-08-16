from __future__ import annotations

import numpy as np

from business_cycle.models.phase import emission_probabilities, map_angle, phase_definitions


def test_all_twelve_angle_segments(settings):
    phases = phase_definitions(settings.transitions["phases"])
    expected = {
        285: "recovery_early",
        315: "recovery_mid",
        345: "recovery_late",
        15: "expansion_early",
        45: "expansion_mid",
        75: "expansion_late",
        105: "slowdown_early",
        135: "slowdown_mid",
        165: "slowdown_late",
        195: "contraction_early",
        225: "contraction_mid",
        255: "contraction_late",
    }
    assert {angle: map_angle(angle, phases).code for angle in expected} == expected


def test_zero_and_360_are_same_boundary(settings):
    phases = phase_definitions(settings.transitions["phases"])
    assert map_angle(0, phases).code == "expansion_early"
    assert map_angle(360, phases).code == "expansion_early"
    assert map_angle(-0.001, phases).code == "recovery_late"


def test_phase_probabilities_sum_to_one(settings):
    phases = phase_definitions(settings.transitions["phases"])
    probabilities = emission_probabilities(323.6, 0.52, phases, 22, 2, 0.75)
    assert len(probabilities) == 12
    assert np.isclose(probabilities.sum(), 1.0)
