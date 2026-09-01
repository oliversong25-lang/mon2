from __future__ import annotations

from business_cycle.backtest.engine import run_backtest


def test_walk_forward_backtest_smoke(settings, synthetic_data):
    result = run_backtest(synthetic_data, settings, "2000-01-01", "2026-08-14", True)
    assert result.metadata["walk_forward"] is True
    assert result.metrics["weeks"] >= settings.model["minimum_training_weeks"]
    assert "recession_recall" in result.metrics["nber"]
    assert result.metrics["multi_step_jumps"] >= 0
    assert result.metadata["model_comparison"]["dynamic_available_weeks"] > 0
