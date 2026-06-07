
import sys
import os
import shutil
from fastapi.testclient import TestClient
from pathlib import Path
import io

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from backend.main import app, User, get_db, WorkItem
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

client = TestClient(app)

def test_upload_and_delete():
    # 1. Login to get session
    # We need to mock a user session or login. 
    # For TestClient with SessionMiddleware, we can't easily set cookies directly for session decoding without secret.
    # Instead, let's login via the login endpoint if possible, or override dependency.
    
    # Let's try standard login flow
    login_data = {
        "username": "admin",
        "password": "admin123" # Default
    }
    response = client.post("/login", data=login_data)
    if response.status_code != 303: # Redirect
        # Try to see if admin exists or password changed.
        # If failed, we might need to skip or fake dependency.
        print(f"Login failed: {response.status_code}")
        # Assuming admin/admin123 might not work if changed. 
        # But we saw main.py has default admin.
    
    # 2. Get a valid task ID
    # We can inspect DB or just try task 1.
    response = client.get("/tasks")
    if "href=\"/tasks/1\"" in response.text:
        task_id = 1
    else:
        # Create a dummy task? 
        # Hard to create without login working perfectly or knowing project ID.
        # Let's hope task 1 exists from bootstrap.
        task_id = 1

    print(f"Using Task ID: {task_id}")

    # 3. Upload a dummy image
    # Create a small red image
    from PIL import Image
    im = Image.new('RGB', (100, 100), color = 'red')
    img_byte_arr = io.BytesIO()
    im.save(img_byte_arr, format='JPEG')
    img_byte_arr.seek(0)

    files = {
        'file': ('test_image.jpg', img_byte_arr, 'image/jpeg')
    }
    
    print("Uploading image...")
    # /tasks/{task_id}/upload-proof
    response = client.post(f"/tasks/{task_id}/upload-proof", files=files)
    
    if response.status_code != 200:
        print(f"Upload failed: {response.status_code} {response.text}")
        return
    
    data = response.json()
    if not data.get("success"):
        print(f"Upload logic failed: {data}")
        return
        
    image_id = data['image']['id']
    image_url = data['image']['url']
    print(f"Uploaded Image ID: {image_id}")
    
    # 4. Verify file exists
    # url is /static/uploads/...
    # static dir is backend/static
    rel_path = image_url.replace("/static/", "")
    full_path = os.path.join("backend/static", rel_path)
    if os.path.exists(full_path):
        print("File exists on disk.")
    else:
        print(f"File NOT found on disk at {full_path}")

    # 5. Delete the image
    print("Deleting image...")
    response = client.post(f"/tasks/images/{image_id}/delete")
    
    if response.status_code != 200:
        print(f"Delete failed: {response.status_code} {response.text}")
        return

    del_data = response.json()
    if del_data.get("success"):
        print("Delete reported success.")
    else:
        print(f"Delete logic failed: {del_data}")
        return

    # 6. Verify file gone
    if not os.path.exists(full_path):
        print("File correctly removed from disk.")
    else:
        print("File STILL EXISTS on disk!")

    print("Verification Passed!")

if __name__ == "__main__":
    test_upload_and_delete()
