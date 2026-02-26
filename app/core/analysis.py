import pandas as pd
import numpy as np
from typing import List, Tuple
from app.models.mill_data import BearingRisk

def calculate_baseline_kwh(df: pd.DataFrame, max_a: float) -> float:
    """Compute 20th percentile of kWh values during RUNNING state, excluding overloads."""
    if df.empty or 'motor_state' not in df.columns or 'energy_kwh' not in df.columns:
        return 0.0
    
    # Authoritative exclusion logic
    running_df = df[
        (df['motor_state'] == 'RUNNING') & 
        (df['current_A'] <= max_a)
    ]
    
    if running_df.empty:
        return 0.0
    
    return running_df['energy_kwh'].quantile(0.20)

def calculate_excess_metrics(actual_kwh: float, baseline_kwh: float) -> Tuple[float, float]:
    """
    Calculate excess kWh and excess CO2.
    excess_kwh = max(0, actual_kwh - baseline_kwh)
    excess_co2 = excess_kwh * 0.233
    """
    excess_kwh = max(0.0, actual_kwh - baseline_kwh)
    excess_co2 = excess_kwh * 0.233
    return excess_kwh, excess_co2

def assess_bearing_risk(
    rolling_history_1440: List[float],
    max_a: float
) -> BearingRisk:
    """
    Simple, explainable Bearing Risk Logic.
    Rule: If 24h rolling mean current > 85% of max current -> HIGH, else NORMAL.
    """
    if not rolling_history_1440:
        return BearingRisk.NORMAL
        
    rolling_mean = sum(rolling_history_1440) / len(rolling_history_1440)
    
    if rolling_mean > 0.85 * max_a:
        return BearingRisk.HIGH
        
    return BearingRisk.NORMAL

def calculate_health_score(excess_co2_kg: float, risk: BearingRisk) -> float:
    """Placeholder for legacy health score logic."""
    return 100.0

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
