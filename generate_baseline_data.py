import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def generate_baseline(mill_id, machines):
    """Generate 7 days of "solid" baseline data."""
    start_time = datetime(2026, 1, 20)
    data = []
    
    for machine in machines:
        # Define normal operating current for each machine
        base_a = 12.0 if "BK" in machine else 18.0
        if machine == "X": base_a = 15.0
        
        for day in range(7):
            for minute in range(1440):
                ts = start_time + timedelta(days=day, minutes=minute)
                # Add some noise
                curr = base_a + np.random.normal(0, 0.5)
                # Simulate some idle time (OFF)
                state = "RUNNING"
                if minute % 60 < 2: # 2 mins OFF every hour
                    curr = 0.0
                    state = "OFF"
                
                data.append({
                    "timestamp": ts.isoformat() + "Z",
                    "mill_id": mill_id,
                    "machine_id": machine,
                    "current_A": round(max(0, curr), 2),
                    "motor_state": state
                })
    
    df = pd.DataFrame(data)
    df.to_csv("baseline_solid.csv", index=False)
    print("Generated baseline_solid.csv")

if __name__ == "__main__":
    mill_id = "B"
    machines = ["1BK1", "1BK2", "3BK_C", "3BK_F", "4BK_C", "4BK_F", "5BK", "AF1", "AF2", "X"]
    generate_baseline(mill_id, machines)
