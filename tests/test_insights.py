import pytest
from app.core.analysis import generate_machine_insights
from app.models.mill_data import BearingRisk

def test_generate_machine_insights_critical():
    insights = generate_machine_insights(10.0, BearingRisk.HIGH, 40.0)
    assert "Mech. Degradation" in insights
    assert "High Loss" in insights
    assert "Low Health" in insights

def test_generate_machine_insights_optimal():
    insights = generate_machine_insights(0.0, BearingRisk.NORMAL, 98.0)
    assert "Optimal" in insights

def test_generate_machine_insights_warning():
    insights = generate_machine_insights(1.0, BearingRisk.WARNING, 75.0)
    assert "Inspect Bearing" in insights
    assert "Slight Loss" in insights
