import sys
import os

print(f"Python executable: {sys.executable}")
print(f"CWD: {os.getcwd()}")
print(f"Path: {sys.path}")

try:
    print("Attempting to import backend.main...")
    import backend.main
    print("Import successful!")
except Exception as e:
    print(f"Import failed: {e}")
    import traceback
    traceback.print_exc()
