import httpx
import time
import secrets
from datetime import datetime, timedelta

BASE_URL = "http://144.91.111.151:8000/api/v1"
PILOT_MILL_ID = f"PILOT_{secrets.token_hex(3).upper()}"

def run_step(msg):
    print(f"\n>>> {msg}")

def wait_for_task(task_id, headers):
    while True:
        resp = httpx.get(f"{BASE_URL}/task/{task_id}", headers=headers)
        data = resp.json()
        status = data.get("status")
        if status == "COMPLETED":
            return data
        if status == "FAILED":
            raise Exception(f"Task failed: {data.get('message')}")
        time.sleep(1)

def simulate():
    # 1. Registration
    run_step(f"Registering Pilot Mill: {PILOT_MILL_ID}")
    email = f"pilot_{secrets.token_hex(4)}@factory.com"
    resp = httpx.post(f"{BASE_URL}/auth/register", json={
        "email": email,
        "password": "PilotPassword123!",
        "mill_id": PILOT_MILL_ID
    })
    resp.raise_for_status()
    user_data = resp.json()
    api_key = user_data["api_key"]
    headers = {"X-API-KEY": api_key}
    print(f"Registered! API Key: {api_key}")

    # 2. Day 0: Baseline Upload
    run_step("Day 0: Initial Baseline Upload")
    baseline_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
    base_time = datetime.now() - timedelta(days=7)
    for i in range(100):
        ts = (base_time - timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        baseline_csv += f"{ts},{PILOT_MILL_ID},M1,12.0,RUNNING\n"
    
    resp = httpx.post(f"{BASE_URL}/baseline/upload", files={"file": ("baseline.csv", baseline_csv)}, headers=headers)
    resp.raise_for_status()
    wait_for_task(resp.json()["task_id"], headers)
    print("Baseline processed.")

    # 3. Days 1-3: Normal Operation (Stable 12A)
    for day in range(1, 4):
        run_step(f"Day {day}: Normal Operation")
        op_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
        day_time = base_time + timedelta(days=day)
        for i in range(20):
            ts = (day_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
            op_csv += f"{ts},{PILOT_MILL_ID},M1,12.1,RUNNING\n"
        
        resp = httpx.post(f"{BASE_URL}/upload", files={"file": (f"day_{day}.csv", op_csv)}, headers=headers)
        resp.raise_for_status()
        wait_for_task(resp.json()["task_id"], headers)
    
    # 4. Day 4: Load Shift Alert (18A)
    run_step("Day 4: Simuring Load Shift (Triggering Alert)")
    op_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
    day_time = base_time + timedelta(days=4)
    for i in range(20):
        ts = (day_time + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        op_csv += f"{ts},{PILOT_MILL_ID},M1,18.0,RUNNING\n"
    
    resp = httpx.post(f"{BASE_URL}/upload", files={"file": ("day_4.csv", op_csv)}, headers=headers)
    resp.raise_for_status()
    wait_for_task(resp.json()["task_id"], headers)
    
    alerts = httpx.get(f"{BASE_URL}/alerts/", headers=headers).json()
    print(f"Alerts generated: {len(alerts)}")

    # 5. Days 5-7: Incremental Drift (12.5 -> 13.5 -> 14.5)
    for i, day in enumerate(range(5, 8)):
        current = 12.5 + i
        run_step(f"Day {day}: Incremental Drift ({current}A)")
        op_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
        day_time = base_time + timedelta(days=day)
        for j in range(20):
            ts = (day_time + timedelta(minutes=j)).strftime("%Y-%m-%d %H:%M:%S")
            op_csv += f"{ts},{PILOT_MILL_ID},M1,{current},RUNNING\n"
        
        resp = httpx.post(f"{BASE_URL}/upload", files={"file": (f"day_{day}.csv", op_csv)}, headers=headers)
        resp.raise_for_status()
        wait_for_task(resp.json()["task_id"], headers)

    # 6. Final Status Check
    run_step("End of 7-Day Simulation: Final Health Assessment")
    summary = httpx.get(f"{BASE_URL}/mill/{PILOT_MILL_ID}/summary", headers=headers).json()
    machine = summary["machines"][0]
    print(f"Machine: {machine['machine_id']}")
    print(f"Current Health Score: {machine['health_score']}")
    print(f"Status Category: {machine['health_category']}")
    print(f"Insights: {machine['insights']}")
    
    if machine['health_score'] < 100:
        print("\nSUCCESS: Simulation successfully triggered health decay.")
    else:
        print("\nFAILURE: Health score remained at 100.")

if __name__ == "__main__":
    simulate()
