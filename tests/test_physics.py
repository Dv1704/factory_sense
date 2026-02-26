import pytest
from app.core import physics
from app.core.config import settings

def test_calculate_power_kw():
    # User example: 18.7 A
    # kW = (sqrt(3) * 400 * 18.7 * 0.85 * 0.90) / 1000
    current = 18.7
    import math
    expected = (math.sqrt(3) * 400 * current * 0.85 * 0.90) / 1000
    assert abs(physics.calculate_power_kw(current) - expected) < 0.0001

def test_calculate_energy_kwh():
    kw = 60.0
    # kWh = 60 / 60 = 1.0
    assert physics.calculate_energy_kwh(kw) == 1.0

def test_calculate_co2_kg():
    kwh = 100.0
    # CO2 = 100 * 0.233 = 23.3
    assert abs(physics.calculate_co2_kg(kwh) - 23.3) < 0.0001
