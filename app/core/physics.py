import math
from app.core.config import settings

def calculate_power_kw(current_a: float) -> float:
    """Calculate Power (kW) from Current (A)."""
    kw = (math.sqrt(3) * settings.voltage * current_a * settings.power_factor * settings.efficiency) / 1000
    return kw

def calculate_energy_kwh(power_kw: float, duration_hours: float = 1.0 / 60.0) -> float:
    """
    Calculate Energy (kWh) from Power (kW) and duration in hours.
    Formula: kWh = kW * duration_hours
    Defaults to 1-minute interval (1/60 hours) if duration is not provided.
    """
    return power_kw * duration_hours

def calculate_co2_kg(energy_kwh: float) -> float:
    """
    Calculate CO2 (kg) from Energy (kWh).
    Formula: CO2 = kWh * 0.233
    """
    return energy_kwh * settings.grid_emission_factor
