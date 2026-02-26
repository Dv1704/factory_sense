import pytest
from fastapi.testclient import TestClient
from datetime import date
import pandas as pd

from app.main import app
from app.core.database import get_db
from app.routes.data import get_api_key_user
from app.models.user import User
from app.models.mill_data import MachineDailyStats, Alert, BearingRisk, AlertType

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_db_and_auth(monkeypatch):
    class MockResult:
        def __init__(self, data=None):
            self.data = data or []
        def scalars(self): return self
        def scalar(self): return self.data[0] if self.data else None
        def all(self): return self.data
        def first(self): return self.data[0] if self.data else None
        def __iter__(self): return iter(self.data)

    class MockDB:
        def __init__(self):
            self.added = []
            self.committed = False
            self.refreshed = False

        async def execute(self, query, *args, **kwargs):
            q_str = str(query).lower()
            if "users" in q_str:
                return MockResult([User(id=1, email="test@example.com", mill_id="TEST", api_key="fsa_TEST_key")])
            if "alerts" in q_str:
                if "count(" in q_str:
                    return MockResult([1])
                return MockResult([Alert(id=1, mill_id="TEST", machine_id="M1", type=AlertType.HIGH_LOAD, message="Test Alert", is_acknowledged=False)])
            if "machine_daily_stats" in q_str:
                if "count(" in q_str:
                    return MockResult([1])
                # If it's a simple query for the latest date (no join)
                if "max(" in q_str and "join" not in q_str:
                    return MockResult([date.today()])
                # Default to returning stats objects for machine lists or summary stats
                return MockResult([MachineDailyStats(
                    id=1, mill_id="TEST", machine_id="M1", date=date.today(), 
                    total_energy_kwh=100.0, total_co2_kg=23.3, bearing_risk=BearingRisk.NORMAL,
                    baseline_kwh=80.0, excess_kwh=20.0, excess_co2_kg=4.66, run_hours=8.0,
                    avg_current=15.0, max_current=20.0, health_score=85.0
                )])
            return MockResult()

        def add(self, obj): self.added.append(obj)
        async def commit(self): self.committed = True
        async def refresh(self, obj): self.refreshed = True
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    mock_db = MockDB()
    
    async def override_get_db():
        yield mock_db
        
    async def override_get_user(*args, **kwargs):
        return User(id=1, email="test@example.com", mill_id="TEST", api_key="fsa_TEST_key")
        
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_api_key_user] = override_get_user
    
    # Mock AsyncSessionLocal in app.core.database to handle background tasks
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: mock_db)
    
    yield mock_db
    
    app.dependency_overrides.clear()

def test_auth_register_success(mock_db_and_auth, monkeypatch):
    # For register, we need to return None for the first user check
    async def mock_execute_none(*args, **kwargs):
        class MockRes:
            def scalars(self): return self
            def first(self): return None
        return MockRes()
    
    monkeypatch.setattr(mock_db_and_auth, "execute", mock_execute_none)
    
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "password123", "mill_id": "TEST"}
    )
    assert response.status_code == 200
    assert "api_key" in response.json()
    assert mock_db_and_auth.committed

def test_auth_login_fail(mock_db_and_auth, monkeypatch):
    # Remove override to test real-ish logic (which still calls mock DB)
    if get_api_key_user in app.dependency_overrides:
        del app.dependency_overrides[get_api_key_user]
    
    # Mock user check to return None
    async def mock_execute_none(*args, **kwargs):
        class MockRes:
            def scalars(self): return self
            def first(self): return None
        return MockRes()
    monkeypatch.setattr(mock_db_and_auth, "execute", mock_execute_none)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "wrong@example.com", "password": "password123", "mill_id": "TEST"}
    )
    assert response.status_code == 401

def test_data_upload_valid(mock_db_and_auth):
    csv_content = "timestamp,mill_id,machine_id,current_A,motor_state\n2026-02-25T12:00:00Z,TEST,1BK1,10.5,RUNNING"
    files = {"file": ("data.csv", csv_content, "text/csv")}
    headers = {"x-api-key": "fsa_TEST_key"}
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    assert response.status_code == 200
    assert response.json()["records_processed"] == 1

def test_data_upload_empty(mock_db_and_auth):
    headers = {"x-api-key": "fsa_TEST_key"}
    files = {"file": ("empty.csv", "", "text/csv")}
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    assert response.status_code == 200
    assert "empty" in response.json()["message"]

def test_get_alerts(mock_db_and_auth):
    headers = {"x-api-key": "fsa_TEST_key"}
    response = client.get("/api/v1/alerts/", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) > 0
    assert response.json()[0]["machine_id"] == "M1"

def test_dashboard_summary(mock_db_and_auth):
    headers = {"x-api-key": "fsa_TEST_key"}
    response = client.get("/api/v1/dashboard/summary", headers=headers)
    assert response.status_code == 200
    assert "total_energy_kwh" in response.json()
    assert response.json()["machine_count"] == 1

def test_dashboard_machines(mock_db_and_auth):
    headers = {"x-api-key": "fsa_TEST_key"}
    response = client.get("/api/v1/dashboard/machines", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["machine_id"] == "M1"

def test_dashboard_machine_specs(mock_db_and_auth):
    headers = {"x-api-key": "fsa_TEST_key"}
    response = client.get("/api/v1/dashboard/machine-specs", headers=headers)
    assert response.status_code == 200
    assert "1BK1" in response.json()
    assert response.json()["1BK1"]["max_a"] == 25.0
