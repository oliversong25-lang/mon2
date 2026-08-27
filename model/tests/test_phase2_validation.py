from __future__ import annotations

import numpy as np
import pandas as pd

from business_cycle.models.phase import emission_probabilities, phase_definitions
from business_cycle.models.transition import filter_probabilities, transition_matrix
from business_cycle.validation.phase2_metrics import (
    binary_episodes,
    causal_confirmed_signals,
    classification_metrics,
    recession_prediction,
    turning_point_metrics,
)


def test_recession_definition_excludes_slowdown() -> None:
    history = pd.DataFrame(
        {
            "broad_phase": ["slowdown", "contraction", "contraction", "recovery"],
            "phase_code": [
                "slowdown_late",
                "contraction_early",
                "contraction_mid",
                "recovery_early",
            ],
        }
    )
    assert recession_prediction(history).tolist() == [False, True, True, False]


def test_classification_formulas_use_non_recession_weeks_as_fpr_denominator() -> None:
    predicted = pd.Series([True, True, False, False])
    actual = pd.Series([True, False, True, False])
    metrics = classification_metrics(predicted, actual)
    assert metrics["recession_recall"] == 0.5
    assert metrics["recession_false_positive_rate"] == 0.5
    assert metrics["recession_precision"] == 0.5
    assert metrics["recession_specificity"] == 0.5
    assert metrics["recession_f1"] == 0.5


def test_false_positive_episodes_are_split_at_false_week() -> None:
    index = pd.date_range("2020-01-03", periods=7, freq="W-FRI")
    flags = pd.Series([False, True, True, False, True, True, True], index=index)
    episodes = binary_episodes(flags)
    assert [(start, end, weeks) for start, end, weeks in episodes] == [
        (index[1], index[2], 2),
        (index[4], index[6], 3),
    ]


def test_four_week_confirmation_is_causal_and_not_backdated() -> None:
    index = pd.date_range("2020-01-03", periods=8, freq="W-FRI")
    flags = pd.Series([False, True, True, True, True, True, False, False], index=index)
    signals = causal_confirmed_signals(flags, 4)
    assert signals.entries == [index[4]]
    assert not bool(signals.state.loc[index[3]])
    assert bool(signals.state.loc[index[4]])


def test_turning_point_exit_must_follow_the_matched_entry() -> None:
    index = pd.date_range("2020-01-03", periods=20, freq="W-FRI")
    predicted = pd.Series(
        [True] * 4 + [False] * 4 + [True] * 5 + [False] * 7,
        index=index,
    )
    actual = pd.Series([False] * 8 + [True] * 5 + [False] * 7, index=index)
    row = turning_point_metrics(predicted, actual, minimum_weeks=4)[0]
    assert row["confirmed_entry_week"] == index[11].date().isoformat()
    assert row["confirmed_exit_week"] == index[16].date().isoformat()


def test_origin_level_gate_reduces_weak_contraction_evidence(settings) -> None:
    phases = phase_definitions(settings.transitions["phases"])
    ungated = emission_probabilities(225, 0.2, phases, 22, 2, 0.75)
    gated = emission_probabilities(225, 0.2, phases, 22, 2, 0.75, -0.1, 0.75)
    contraction = [index for index, phase in enumerate(phases) if phase.broad == "contraction"]
    assert gated[contraction].sum() < ungated[contraction].sum()
    assert np.isclose(gated.sum(), 1.0)


def test_low_radius_blocks_multi_step_jump_but_large_shock_does_not(settings) -> None:
    matrix = transition_matrix(12, settings.transitions["transition"])
    first = np.full(12, 1e-9)
    first[0] = 1.0
    second = np.full(12, 1e-9)
    second[6] = 1.0
    emissions = np.vstack([first / first.sum(), second / second.sum()])
    low = filter_probabilities(emissions, matrix, np.array([0.1, 0.1]), 0.75)
    high = filter_probabilities(emissions, matrix, np.array([1.0, 1.0]), 0.75)
    assert int(np.argmax(low[1])) in {0, 1, 11}
    assert int(np.argmax(high[1])) == 6
    assert np.allclose(low.sum(axis=1), 1.0)
    assert np.allclose(high.sum(axis=1), 1.0)


def test_jump_constraint_has_no_future_lookahead(settings) -> None:
    matrix = transition_matrix(12, settings.transitions["transition"])
    rng = np.random.default_rng(42)
    emissions = rng.random((10, 12))
    emissions /= emissions.sum(axis=1, keepdims=True)
    radii = np.full(10, 0.2)
    short = filter_probabilities(emissions[:6], matrix, radii[:6], 0.75)
    changed_future = emissions.copy()
    changed_future[6:] = np.roll(changed_future[6:], 5, axis=1)
    long = filter_probabilities(changed_future, matrix, radii, 0.75)
    assert np.allclose(short, long[:6])
