import os
import pandas as pd
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import AsyncSessionLocal

# Setup TestClient
client = TestClient(app)

def test_run_demo():
    print("--- Starting FactorySenseAI Analysis Demo ---")
    
    # 1. Register User
    print("\n[1] Registering test user...")
    resp = client.post("/api/v1/auth/register", json={
        "email": "demo@factorysense.ai",
        "password": "securepassword",
        "mill_id": "B"
    })
    if resp.status_code == 200:
        api_key = resp.json()["api_key"]
        print(f"Success! API Key: {api_key}")
    else:
        # If already exists, we might need an alternative way or just ignore
        print(f"Registration note: {resp.json().get('detail', 'User might already exist')}")
        # For simplicity, let's assume login if register fails
        resp = client.post("/api/v1/auth/login", json={
            "email": "demo@factorysense.ai",
            "password": "securepassword",
            "mill_id": "B"
        })
        api_key = resp.json()["api_key"]
        print(f"Logged in. API Key: {api_key}")

    headers = {"x-api-key": api_key}

    # 2. Upload Baseline
    print("\n[2] Uploading baseline_solid.csv...")
    with open("baseline_solid.csv", "rb") as f:
        resp = client.post("/api/v1/baseline/upload", files={"file": ("baseline_solid.csv", f, "text/csv")}, headers=headers)
    print(f"Response: {resp.status_code}")
    print(resp.json()["message"])

    # 3. Upload 24h Data
    print("\n[3] Uploading mill_data_24h.csv...")
    with open("mill_data_24h.csv", "rb") as f:
        resp = client.post("/api/v1/upload", files={"file": ("mill_data_24h.csv", f, "text/csv")}, headers=headers)
    print(f"Response: {resp.status_code}")
    print(f"Processed {resp.json().get('records_processed', 0)} records.")

    # 4. Get Summary
    print("\n[4] Fetching Summary for Mill B...")
    resp = client.get("/api/v1/mill/B/summary", headers=headers)
    summary = resp.json()
    
    print("\n--- ANALYSIS RESULTS ---")
    print(f"Mill ID: {summary['mill_id']}")
    print(f"Total Energy: {summary['summary_metrics']['total_energy_kwh']:.1f} kWh")
    print(f"Total Excess CO2: {summary['summary_metrics']['total_excess_co2_kg']:.1f} kg")
    print(f"Avoidable Cost: ${summary['summary_metrics']['avoidable_cost_usd']:.2f}")
    
    print("\nMachine Breakdown:")
    for m in summary['machines']:
        print(f"- {m['machine_id']} ({m['name']}): Health {m['health_score']} | Running: {m['run_hours']}h | Risk: {m['bearing_risk']}")
        if m['insights']:
            print(f"  Insights: {', '.join(m['insights'])}")

if __name__ == "__main__":
    run_demo()
