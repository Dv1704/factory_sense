import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_db
from app.routes.data import get_api_key_user
from app.models.user import User
from app.models.mill_data import MachineDailyStats, Alert, BearingRisk, AlertType
import pandas as pd
import io

client = TestClient(app)

@pytest.fixture
def mock_db(monkeypatch):
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
                return MockResult([User(id=1, email="test@example.com", mill_id="TEST", api_key="fsa_TEST_key", password_hash="hashed_pw")])
            return MockResult()

        def add(self, obj): self.added.append(obj)
        async def commit(self): self.committed = True
        async def refresh(self, obj): self.refreshed = True
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass

    mock_db_obj = MockDB()
    
    async def override_get_db():
        yield mock_db_obj
        
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: mock_db_obj)
    
    yield mock_db_obj
    app.dependency_overrides.clear()

def test_login_returns_api_key(mock_db, monkeypatch):
    # Mock verify_password to always return True for this test
    monkeypatch.setattr("app.routes.auth.verify_password", lambda p, h: True)
    
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "test@example.com", "password": "password123", "mill_id": "TEST"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "api_key" in data
    assert data["api_key"] == "fsa_TEST_key"

def test_upload_csv_robustness_missing_cols(mock_db):
    headers = {"x-api-key": "fsa_TEST_key"}
    # Missing 'current_A'
    csv_content = "timestamp,mill_id,machine_id,motor_state\n2026-02-25T12:00:00Z,TEST,1BK1,RUNNING"
    files = {"file": ("data.csv", csv_content, "text/csv")}
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    assert response.status_code == 400
    assert "Missing columns" in response.json()["detail"]
    assert "current_A" in response.json()["detail"]

def test_upload_csv_robustness_invalid_data(mock_db):
    headers = {"x-api-key": "fsa_TEST_key"}
    # current_A is a string 'INVALID'
    csv_content = "timestamp,mill_id,machine_id,current_A,motor_state\n2026-02-25T12:00:00Z,TEST,1BK1,INVALID,RUNNING"
    files = {"file": ("data.csv", csv_content, "text/csv")}
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    assert response.status_code == 200
    # Should have processed and defaulted INVALID to 0.0
    assert response.json()["records_processed"] == 1
    assert response.json()["machines_processed"] == 1

def test_upload_csv_robustness_mixed_case_motor_state(mock_db):
    headers = {"x-api-key": "fsa_TEST_key"}
    csv_content = "timestamp,mill_id,machine_id,current_A,motor_state\n2026-02-25T12:00:00Z,TEST,1BK1,10.0,running"
    files = {"file": ("data.csv", csv_content, "text/csv")}
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    assert response.status_code == 200
    assert response.json()["records_processed"] == 1
    assert response.json()["machines_processed"] == 1

def test_upload_csv_partial_error(mock_db, monkeypatch):
    headers = {"x-api-key": "fsa_TEST_key"}
    # Two machines
    csv_content = "timestamp,mill_id,machine_id,current_A,motor_state\n2026-02-25T12:00:00Z,TEST,M1,10.0,RUNNING\n2026-02-25T12:00:00Z,TEST,M2,10.0,RUNNING"
    files = {"file": ("data.csv", csv_content, "text/csv")}
    
    # Mock calculate_baseline_kwh to raise an exception for M1
    from app.core import analysis
    original_calc = analysis.calculate_baseline_kwh
    def mock_calc(df, max_a):
        if not df.empty and df['machine_id'].iloc[0] == 'M1':
            raise Exception("M1 Error")
        return original_calc(df, max_a)
    
    monkeypatch.setattr("app.core.analysis.calculate_baseline_kwh", mock_calc)
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["machines_processed"] == 1 # M2 should be processed, M1 failed
    assert len(data["errors"]) == 1
    assert "Error processing machine M1" in data["errors"][0]
