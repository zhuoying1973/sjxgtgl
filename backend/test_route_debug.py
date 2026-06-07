
import sys
import os
from fastapi.testclient import TestClient

# Add current directory to path so we can import backend.main
sys.path.append(os.getcwd())

try:
    from backend.main import app, WorkItem, get_db
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

client = TestClient(app)

def test_routes():
    print("Testing routes...")
    
    # 1. Test existing route /tasks
    response = client.get("/tasks")
    print(f"GET /tasks: {response.status_code}")
    
    # 2. Test /tasks/1 (assuming ID 1 might exist or not, but checking if it hits the route or 404)
    # Note: If it returns 404, check if it's "Not Found" (route missing) or JSON "Not Found" (task missing)
    response = client.get("/tasks/1")
    print(f"GET /tasks/1: {response.status_code}")
    print(f"Response: {response.text[:200]}")
    
    # 3. Test explicit 404 for non-existent
    response = client.get("/tasks/999999")
    print(f"GET /tasks/999999: {response.status_code}")

if __name__ == "__main__":
    test_routes()
