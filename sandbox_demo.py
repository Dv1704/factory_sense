import requests
import time
import sys

BASE_URL = "http://localhost:8000/api/v1"

def print_step(msg):
    print(f"\n[{time.strftime('%H:%M:%S')}] {msg}")
    print("-" * 50)

def run_demo():
    print_step("Starting End-to-End Pilot Demonstration")

    # 1. Register User (Admin)
    print_step("Registering Admin User...")
    resp = requests.post(f"{BASE_URL}/auth/register", data={"username": "demo@factory.com", "password": "demo_password", "role": "ADMIN"})
    if resp.status_code == 200:
        print("Success!")
    else:
        print(f"Failed or already exists: {resp.text}")

    # 2. Login
    print_step("Logging in...")
    resp = requests.post(f"{BASE_URL}/auth/login", data={"username": "demo@factory.com", "password": "demo_password"})
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        sys.exit(1)
    
    token_data = resp.json()
    token = token_data.get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    print(f"Got Access Token: {token[:20]}...")

    # 3. Create a Mill
    print_step("Provisioning a Mill via Admin API...")
    resp = requests.post(f"{BASE_URL}/admin/mills", json={"user_id": 1, "mill_id": "DEMO_MILL_1"}, headers=headers)
    
    if resp.status_code == 200:
        mill_data = resp.json()
        api_key = mill_data.get("api_key")
        print(f"Mill provisioned! API Key: {api_key}")
    else:
        print(f"Mill creation failed: {resp.text}")
        # Fetch existing mill
        resp = requests.get(f"{BASE_URL}/admin/mills", headers=headers)
        mills = resp.json()
        if len(mills) > 0:
            api_key = mills[0]["api_key"]
            print(f"Falling back to existing mill API Key: {api_key}")
        else:
            sys.exit(1)

    mill_headers = {"x-api-key": api_key}

    # 4. Upload Baseline
    print_step("Uploading Baseline Data...")
    baseline_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
    for i in range(100):
        baseline_csv += f"2026-03-01T10:{i%60}:00Z,DEMO_MILL_1,M1,{10.0 + (i%5)*0.1},RUNNING\n"
    
    files = {"file": ("baseline.csv", baseline_csv, "text/csv")}
    resp = requests.post(f"{BASE_URL}/baseline/upload", headers=mill_headers, files=files)
    print(f"Baseline Upload: {resp.status_code} - {resp.json()}")

    # 5. Upload Operational Data
    print_step("Uploading Operational Data (Simulation)...")
    op_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
    for i in range(60):
        # Slightly higher current to simulate load shift
        op_csv += f"2026-03-02T10:{i%60}:00Z,DEMO_MILL_1,M1,{12.5 + (i%5)*0.1},RUNNING\n"
    
    files = {"file": ("op_data.csv", op_csv, "text/csv")}
    resp = requests.post(f"{BASE_URL}/upload", headers=mill_headers, files=files)
    print(f"Operational Upload: {resp.status_code} - {resp.json()}")

    # 6. Fetch Dashboard Summary
    print_step("Fetching Dashboard Summary...")
    resp = requests.get(f"{BASE_URL}/dashboard/summary", headers=mill_headers)
    print(f"Summary: {resp.json()}")

    # 7. Fetch Alerts
    print_step("Checking for Generated Alerts...")
    resp = requests.get(f"{BASE_URL}/alerts", headers=mill_headers)
    print(f"Alerts: {resp.json()}")

    print_step("Demonstration Complete!")

if __name__ == "__main__":
    run_demo()
