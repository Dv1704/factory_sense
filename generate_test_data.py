import pandas as pd
import numpy as np

# ---- Machine specification (Mill B) ----
machines = [
    {"machine_id": "1BK1", "kw": 30, "max_a": 25},
    {"machine_id": "1BK2", "kw": 30, "max_a": 25},
    {"machine_id": "AF1", "kw": 30, "max_a": 25},
    {"machine_id": "AF2", "kw": 30, "max_a": 25},
    {"machine_id": "3BK_C", "kw": 11, "max_a": 20},
    {"machine_id": "3BK_F", "kw": 11, "max_a": 20},
    {"machine_id": "X", "kw": 11, "max_a": 20},
    {"machine_id": "5BK", "kw": 11, "max_a": 18},
    {"machine_id": "4BK_F", "kw": 7.5, "max_a": 14},
    {"machine_id": "4BK_C", "kw": 15, "max_a": 28},
]

# ---- Time index ----
time_index = pd.date_range(
    start="2026-02-01",
    end="2026-02-08",
    freq="1min"
)

rows = []

for m in machines:
    base_load = np.random.uniform(0.55, 0.7)
    degradation = 0.0003  # daily degradation rate

    for i, ts in enumerate(time_index):
        day_factor = 1 + degradation * (i / 1440)

        # Random stop
        if np.random.rand() < 0.002:
            current = 0
            state = "OFF"
        else:
            mean_current = m["max_a"] * base_load * day_factor
            current = np.random.normal(mean_current, 0.8)

            # Overload spike
            if np.random.rand() < 0.001:
                current = m["max_a"] * np.random.uniform(1.05, 1.2)

            state = "RUNNING"

        rows.append({
            "timestamp": ts,
            "mill_id": "B",
            "machine_id": m["machine_id"],
            "current_A": max(current, 0),
            "motor_state": state
        })

df = pd.DataFrame(rows)
df.to_csv("mill_B_current_data.csv", index=False)
print("Generated mill_B_current_data.csv")
