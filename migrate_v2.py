import sqlite3
import os

db_path = r'e:\My_AI_Projects\archviz-biz-manager\data\app.db'
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found")
    exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()

try:
    # 增加税率字段
    cur.execute("ALTER TABLE projects ADD COLUMN tax_rate NUMERIC(6,2) DEFAULT 5.00")
    print("Added tax_rate column")
except sqlite3.OperationalError:
    print("tax_rate already exists")

try:
    # 增加 AI 折收因子字段
    cur.execute("ALTER TABLE projects ADD COLUMN ai_factor NUMERIC(6,2) DEFAULT 100.00")
    print("Added ai_factor column")
except sqlite3.OperationalError:
    print("ai_factor already exists")

try:
    # 增加比例方案字段
    cur.execute("ALTER TABLE projects ADD COLUMN ratio_scheme TEXT")
    print("Added ratio_scheme column")
except sqlite3.OperationalError:
    print("ratio_scheme already exists")

conn.commit()
conn.close()
print("Database migration completed.")
