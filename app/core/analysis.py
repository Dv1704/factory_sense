import pandas as pd
import numpy as np
from typing import List, Tuple
from app.models.mill_data import BearingRisk

def calculate_baseline_kwh(df: pd.DataFrame) -> float:
    """Compute 20th percentile of kWh values during RUNNING state."""
    if df.empty or 'motor_state' not in df.columns or 'energy_kwh' not in df.columns:
        return 0.0
    
    # Simple exclusion logic: only RUNNING state
    running_df = df[df['motor_state'] == 'RUNNING']
    
    if running_df.empty:
        return 0.0
    
    return running_df['energy_kwh'].quantile(0.20)

def calculate_baseline_stats(df: pd.DataFrame) -> Tuple[float, float, float]:
    """
    Compute mu (mean), sigma (std), and p95 (95th percentile) for current_A.
    Only considers RUNNING state.
    """
    if df.empty or 'motor_state' not in df.columns or 'current_A' not in df.columns:
        return 0.0, 0.0, 0.0
    
    running_df = df[df['motor_state'] == 'RUNNING']
    if running_df.empty:
        return 0.0, 0.0, 0.0
    
    mu = running_df['current_A'].mean()
    sigma = running_df['current_A'].std()
    p95 = running_df['current_A'].quantile(0.95)
    
    return float(mu), float(sigma if not np.isnan(sigma) else 0.0), float(p95)

def calculate_health_score_v2(
    mean_curr: float, 
    max_curr: float, 
    baseline_mu: float, 
    baseline_sigma: float, 
    baseline_p95: float,
    is_drifting: bool = False
) -> Tuple[float, dict]:
    """
    Calculate health score (0-100) based on deviations from baseline.
    Health Score = 100 - (LoadPenalty + PeakPenalty + DriftPenalty)
    """
    load_penalty = 0.0
    peak_penalty = 0.0
    drift_penalty = 0.0
    
    # 1. Load Shift: mean > mu + 2*sigma
    if mean_curr > (baseline_mu + 2 * baseline_sigma) and baseline_mu > 0:
        load_penalty = 20.0
        # Scaled penalty if much higher
        if mean_curr > (baseline_mu + 4 * baseline_sigma):
            load_penalty = 40.0

    # 2. Peak Stress: max > p95
    if max_curr > baseline_p95 and baseline_p95 > 0:
        peak_penalty = 15.0
        if max_curr > (baseline_p95 * 1.2):
            peak_penalty = 30.0

    # 3. Drift Trend
    if is_drifting:
        drift_penalty = 25.0

    score = 100.0 - (load_penalty + peak_penalty + drift_penalty)
    score = max(0.0, min(100.0, score))
    
    details = {
        "load_penalty": load_penalty,
        "peak_penalty": peak_penalty,
        "drift_penalty": drift_penalty
    }
    
    return score, details

def calculate_health_score_refined(excess_co2_kg: float, total_co2_kg: float, risk: BearingRisk) -> float:
    risk_penalty = 0
    if risk == BearingRisk.WARNING:
        risk_penalty = 20
    elif risk == BearingRisk.HIGH:
        risk_penalty = 50
    
    normalized_excess = 0.0
    if total_co2_kg > 0:
        normalized_excess = excess_co2_kg / total_co2_kg
    
    # Cap normalized excess at 1.0 (though physically possible to be all excess?)
    normalized_excess = min(1.0, max(0.0, normalized_excess))
    
    score = 100 - (normalized_excess * 50) - risk_penalty
    return max(0.0, score)

def generate_machine_insights(
    excess_co2_kg: float, 
    risk: BearingRisk, 
    health_score: float
) -> List[str]:
    """
    Generate actionable insights based on machine performance metrics.
    """
    insights = []
    
    if risk == BearingRisk.HIGH:
        insights.append("Mech. Degradation")
    elif risk == BearingRisk.WARNING:
        insights.append("Inspect Bearing")
        
    if excess_co2_kg > 5.0:
        insights.append("High Loss")
    elif excess_co2_kg > 0:
        insights.append("Slight Loss")
        
    if health_score < 60:
        insights.append("Low Health")
    elif health_score > 95:
        insights.append("Optimal")
        
    if not insights:
        insights.append("Stable")
        
    return insights
