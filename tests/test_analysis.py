import pytest
import pandas as pd
import numpy as np
from app.core import analysis
from app.models.mill_data import BearingRisk

def test_calculate_baseline_kwh():
    # Test with standard RUNNING data
    data = {
        'motor_state': ['RUNNING', 'RUNNING', 'RUNNING', 'RUNNING', 'RUNNING', 'STOPPED'],
        'energy_kwh': [10.0, 11.0, 12.0, 13.0, 14.0, 5.0],
        'current_A': [10.0, 11.0, 12.0, 13.0, 14.0, 5.0]
    }
    df = pd.DataFrame(data)
    # Quantile 0.20 of [10, 11, 12, 13, 14] is 10.8 (interpolation)
    expected = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0]).quantile(0.20)
    assert analysis.calculate_baseline_kwh(df) == expected

    # Test empty
    assert analysis.calculate_baseline_kwh(pd.DataFrame()) == 0.0

def test_calculate_excess_metrics():
    # excess_kwh = max(0, 10 - 8) = 2
    # excess_co2 = 2 * 0.233 = 0.466
    kwh, co2 = analysis.calculate_excess_metrics(10.0, 8.0)
    assert kwh == 2.0
    assert abs(co2 - 0.466) < 0.0001

def test_assess_bearing_risk():
    # Now returns NORMAL by default as threshold is removed
    risk = analysis.assess_bearing_risk([90.0] * 1440)
    assert risk == BearingRisk.NORMAL

def test_calculate_health_score_refined():
    # 100 - (0.1 * 50) - 20 (WARNING) = 100 - 5 - 20 = 75
    score = analysis.calculate_health_score_refined(10.0, 100.0, BearingRisk.WARNING)
    assert score == 75.0

    # 100 - (0.5 * 50) - 50 (HIGH) = 100 - 25 - 50 = 25
    score = analysis.calculate_health_score_refined(50.0, 100.0, BearingRisk.HIGH)
    assert score == 25.0

    # Perfect score
    score = analysis.calculate_health_score_refined(0.0, 100.0, BearingRisk.NORMAL)
    assert score == 100.0
