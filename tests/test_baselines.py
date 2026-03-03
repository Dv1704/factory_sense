import pytest
from fastapi.testclient import TestClient
from datetime import date
import pandas as pd
import io

from app.main import app
from app.core.database import get_db
from app.routes.data import get_api_key_user
from app.models.user import User
from app.models.mill_data import MachineBaseline

client = TestClient(app)

@pytest.fixture
def mock_user_no_baseline():
    return User(id=1, email="new@example.com", mill_id="TEST", api_key="test_key", has_uploaded_baseline=False)

@pytest.fixture
def mock_user_with_baseline():
    return User(id=1, email="old@example.com", mill_id="TEST", api_key="test_key", has_uploaded_baseline=True)

def test_upload_without_baseline_fails(monkeypatch):
    async def override_get_user():
        return User(id=1, email="new@example.com", mill_id="TEST", api_key="test_key", has_uploaded_baseline=False)
    
    app.dependency_overrides[get_api_key_user] = override_get_user
    
    csv_content = "timestamp,mill_id,machine_id,current_A,motor_state\n2026-02-25T12:00:00Z,TEST,1BK1,10.5,RUNNING"
    files = {"file": ("data.csv", csv_content, "text/csv")}
    headers = {"x-api-key": "test_key"}
    
    # We also need to mock the DB check for existing baselines in the mill
    class MockResult:
        def scalars(self): return self
        def first(self): return None
    
    async def mock_execute(*args, **kwargs):
        return MockResult()
    
    # This is tricky because upload_csv uses 'db' dependency.
    # We can override get_db too.
    class MockDB:
        async def execute(self, *args, **kwargs): return MockResult()
        async def commit(self): pass
        async def close(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        
    async def override_get_db():
        yield MockDB()

    app.dependency_overrides[get_db] = override_get_db
    
    response = client.post("/api/v1/upload", files=files, headers=headers)
    if response.status_code != 403:
        print(response.json())
    assert response.status_code == 403
    assert "upload baseline data" in response.json()["detail"]
    
    app.dependency_overrides.clear()

def test_baseline_upload_success():
    async def override_get_user():
        return User(id=1, email="new@example.com", mill_id="TEST", api_key="test_key", has_uploaded_baseline=False)
    
    app.dependency_overrides[get_api_key_user] = override_get_user
    
    csv_content = "timestamp,mill_id,machine_id,current_A,motor_state\n2026-02-25T12:00:00Z,TEST,M1,10.5,RUNNING"
    files = {"file": ("baseline.csv", csv_content, "text/csv")}
    headers = {"x-api-key": "test_key"}
    
    class MockDB:
        def __init__(self):
            self.added = []
        async def execute(self, *args, **kwargs):
            class MockRes:
                def scalars(self): return self
                def first(self): return None
            return MockRes()
        def add(self, obj): self.added.append(obj)
        async def commit(self): pass
        async def flush(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        
    async def override_get_db():
        yield MockDB()

    app.dependency_overrides[get_db] = override_get_db
    
    response = client.post("/api/v1/baseline/upload", files=files, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert "M1" in response.json()["machines"]
    
    app.dependency_overrides.clear()

def test_baseline_crud():
    async def override_get_user():
        return User(id=1, email="user@example.com", mill_id="TEST", api_key="test_key", has_uploaded_baseline=True)
    
    app.dependency_overrides[get_api_key_user] = override_get_user
    
    headers = {"x-api-key": "test_key"}
    
    mock_baseline = MachineBaseline(
        machine_id="M1", mill_id="TEST", mean_current=10.0, std_current=1.0, p95_current=12.0
    )
    
    class MockDB:
        async def execute(self, *args, **kwargs):
            class MockRes:
                def scalars(self): return self
                def all(self): return [mock_baseline]
                def first(self): return mock_baseline
            return MockRes()
        async def commit(self): pass
        async def delete(self, obj): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        
    async def override_get_db():
        yield MockDB()

    app.dependency_overrides[get_db] = override_get_db
    
    # GET
    response = client.get("/api/v1/baseline", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["machine_id"] == "M1"
    
    # PUT
    update_data = {"mean_current": 15.0, "std_current": 2.0, "p95_current": 18.0}
    response = client.put("/api/v1/baseline/M1", json=update_data, headers=headers)
    assert response.status_code == 200
    assert mock_baseline.mean_current == 15.0
    
    # DELETE
    response = client.delete("/api/v1/baseline/M1", headers=headers)
    assert response.status_code == 200
    
    app.dependency_overrides.clear()
