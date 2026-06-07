import pandas as pd
import sys

# Set encoding to utf-8 for console output
sys.stdout.reconfigure(encoding='utf-8')

try:
    df = pd.read_excel("xm2025.xlsx")
    print("Columns:", df.columns.tolist())
    print("\nFirst 3 rows:")
    print(df.head(3).to_string())
except Exception as e:
    print(f"Error reading excel: {e}")
