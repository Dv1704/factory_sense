import pytest
import pandas as pd
import numpy as np
from app.core import analysis

def test_calculate_baseline_stats():
    data = {
        'motor_state': ['RUNNING', 'RUNNING', 'OFF', 'RUNNING'],
        'current_A': [10.0, 12.0, 0.0, 11.0]
    }
    df = pd.DataFrame(data)
    mu, sigma, p95 = analysis.calculate_baseline_stats(df)
    
    assert mu == 11.0
    assert sigma == pytest.approx(1.0)
    assert p95 == pytest.approx(11.9) # 95th percentile of [10, 11, 12]

def test_calculate_health_score_v2_normal():
    score, details = analysis.calculate_health_score_v2(
        mean_curr=11.0, max_curr=12.5,
        baseline_mu=11.0, baseline_sigma=1.0, baseline_p95=13.0
    )
    assert score == 100.0
    assert details["load_penalty"] == 0.0
    assert details["peak_penalty"] == 0.0

def test_calculate_health_score_v2_load_shift():
    # mu + 2*sigma = 10 + 2*1 = 12. Today mean 13 > 12.
    score, details = analysis.calculate_health_score_v2(
        mean_curr=13.0, max_curr=14.0,
        baseline_mu=10.0, baseline_sigma=1.0, baseline_p95=15.0
    )
    assert score == 80.0
    assert details["load_penalty"] == 20.0

def test_calculate_health_score_v2_peak_stress():
    # max 16 > p95 15.
    score, details = analysis.calculate_health_score_v2(
        mean_curr=10.0, max_curr=16.0,
        baseline_mu=10.0, baseline_sigma=1.0, baseline_p95=15.0
    )
    assert score == 85.0
    assert details["peak_penalty"] == 15.0

def test_calculate_health_score_v2_drift():
    score, details = analysis.calculate_health_score_v2(
        mean_curr=10.0, max_curr=12.0,
        baseline_mu=10.0, baseline_sigma=1.0, baseline_p95=15.0,
        is_drifting=True
    )
    assert score == 75.0
    assert details["drift_penalty"] == 25.0

def test_calculate_health_score_v2_combined():
    score, details = analysis.calculate_health_score_v2(
        mean_curr=13.0, max_curr=16.0,
        baseline_mu=10.0, baseline_sigma=1.0, baseline_p95=15.0,
        is_drifting=True
    )
    # 100 - (20 + 15 + 25) = 40
    assert score == 40.0
