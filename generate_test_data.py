import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ---- Machine specification (Mill B) ----
machines = [
    {"machine_id": "1BK1", "kw": 30, "base_a": 15},
    {"machine_id": "1BK2", "kw": 30, "base_a": 15},
    {"machine_id": "AF1", "kw": 30, "base_a": 15},
    {"machine_id": "AF2", "kw": 30, "base_a": 15},
    {"machine_id": "3BK_C", "kw": 11, "base_a": 12},
    {"machine_id": "3BK_F", "kw": 11, "base_a": 12},
    {"machine_id": "X", "kw": 11, "base_a": 12},
    {"machine_id": "5BK", "kw": 11, "base_a": 10},
    {"machine_id": "4BK_F", "kw": 7.5, "base_a": 8},
    {"machine_id": "4BK_C", "kw": 15, "base_a": 18},
]

def generate_dataset(days, filename, resolution="1min"):
    if resolution == "1min":
        periods = 1440 * days
        freq = "1min"
    else:
        periods = days
        freq = "1D"
        
    time_index = pd.date_range(
        start="2026-02-01",
        periods=periods,
        freq=freq
    )
    
    rows = []
    day_variations = {}
    for day in range(days):
        if days > 5 and day == 5:
            day_variations[day] = 1.10
        elif days > 10 and day == 10:
            day_variations[day] = 1.12
        else:
            day_variations[day] = np.random.uniform(0.98, 1.02)

    for m in machines:
        for i, ts in enumerate(time_index):
            if resolution == "1min":
                day = i // 1440
                day_factor = day_variations[day]
                # Hourly variation
                hour_factor = 1 + 0.1 * np.sin(ts.hour * np.pi / 12)
                
                if np.random.rand() < 0.02:
                    current = 0
                    state = "OFF"
                else:
                    current = np.random.normal(m["base_a"] * day_factor * hour_factor, 0.5)
                    state = "RUNNING"
                
                rows.append({
                    "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "mill_id": "B",
                    "machine_id": m["machine_id"],
                    "current_A": round(max(current, 0), 2),
                    "motor_state": state
                })
            else:
                # Daily summary rows
                day_factor = day_variations[i]
                avg_current = m["base_a"] * day_factor
                total_kwh = avg_current * 1.732 * 400 * 0.85 * 0.9 / 1000 * 24
                total_co2 = total_kwh * 0.233
                
                rows.append({
                    "date": ts.strftime("%Y-%m-%d"),
                    "mill_id": "B",
                    "machine_id": m["machine_id"],
                    "avg_current_A": round(avg_current, 2),
                    "total_energy_kwh": round(total_kwh, 2),
                    "total_co2_kg": round(total_co2, 2),
                    "run_hours": 24.0
                })

    df = pd.DataFrame(rows)
    df.to_csv(filename, index=False)
    print(f"Generated {filename} with {len(df)} records.")

if __name__ == "__main__":
    # 24 hours of 1-minute data
    generate_dataset(1, "mill_data_24h.csv", resolution="1min")
    # 14 days of daily summary data
    generate_dataset(14, "mill_daily_summary_14d.csv", resolution="daily")
    # Also keeping the 14-day 1-minute data for alert testing
    generate_dataset(14, "mill_data_14d.csv", resolution="1min")
