import math
from app.core.config import settings

def calculate_power_kw(current_a: float) -> float:
    """Calculate Power (kW) from Current (A)."""
    kw = (math.sqrt(3) * settings.voltage * current_a * settings.power_factor * settings.efficiency) / 1000
    return kw

def calculate_energy_kwh(power_kw: float) -> float:
    """
    Calculate Energy (kWh) from Power (kW) for a 1-minute interval.
    Formula: kWh = kW / 60
    """
    return power_kw / 60

def calculate_co2_kg(energy_kwh: float) -> float:
    """
    Calculate CO2 (kg) from Energy (kWh).
    Formula: CO2 = kWh * 0.233
    """
    return energy_kwh * settings.grid_emission_factor
