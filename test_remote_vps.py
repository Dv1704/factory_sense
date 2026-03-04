import httpx
import pandas as pd
import time
import json

VPS_URL = "http://144.91.111.151:8000"

def test_remote_vps():
    print(f"--- Testing Remote VPS: {VPS_URL} ---")
    
    with httpx.Client(timeout=30.0) as client:
        # 1. Register/Login
        email = f"remote_test_{int(time.time())}@factorysense.ai"
        print(f"\n[1] Registering {email}...")
        resp = client.post(f"{VPS_URL}/api/v1/auth/register", json={
            "email": email,
            "password": "securepassword",
            "mill_id": "REMOTE_TEST"
        })
        if resp.status_code != 200:
            print(f"Registration failed: {resp.text}")
            return
        
        api_key = resp.json()["api_key"]
        headers = {"x-api-key": api_key}
        print(f"Success! API Key acquired.")

        # 2. Try regular upload (should fail)
        print("\n[2] Attempting operational data upload without baseline (should fail 403)...")
        files = {"file": ("data.csv", "timestamp,mill_id,machine_id,current_A,motor_state\n2026-03-01T12:00:00Z,REMOTE_TEST,M1,15.0,RUNNING")}
        resp = client.post(f"{VPS_URL}/api/v1/upload", files=files, headers=headers)
        print(f"Response: {resp.status_code}")
        assert resp.status_code == 403
        print("Correctly blocked!")

        # 3. Upload Baseline
        print("\n[3] Uploading baseline data...")
        # Create a small valid baseline CSV
        baseline_csv = "timestamp,mill_id,machine_id,current_A,motor_state\n"
        for i in range(10):
            baseline_csv += f"2026-01-01T00:{i:02}:00Z,REMOTE_TEST,M1,12.0,RUNNING\n"
        
        files = {"file": ("baseline.csv", baseline_csv)}
        resp = client.post(f"{VPS_URL}/api/v1/baseline/upload", files=files, headers=headers)
        print(f"Response: {resp.status_code}")
        assert resp.status_code == 200
        print(resp.json()["message"])

        # 4. Try regular upload again (should succeed)
        print("\n[4] Attempting operational data upload after baseline...")
        files = {"file": ("data.csv", "timestamp,mill_id,machine_id,current_A,motor_state\n2026-03-01T12:00:00Z,REMOTE_TEST,M1,15.0,RUNNING")}
        resp = client.post(f"{VPS_URL}/api/v1/upload", files=files, headers=headers)
        print(f"Response: {resp.status_code}")
        assert resp.status_code == 200
        print(f"Success! Processed {resp.json().get('records_processed')} records.")

        # 5. Check Summary
        print("\n[5] Fetching Summary for REMOTE_TEST...")
        resp = client.get(f"{VPS_URL}/api/v1/mill/REMOTE_TEST/summary", headers=headers)
        summary = resp.json()
        print(f"Mill ID: {summary['mill_id']}")
        m1 = summary['machines'][0]
        print(f"Machine M1: Health {m1['health_score']} | Insights: {m1['insights']}")

    print("\n[COMPLETE] Remote verification successful!")

if __name__ == "__main__":
    test_remote_vps()
