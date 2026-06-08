import sqlite3
import json

db_path = r'e:\My_AI_Projects\archviz-biz-manager\data\app.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 获取默认比例方案
cur.execute("SELECT value FROM system_settings WHERE key='default_ratio_plan'")
row = cur.fetchone()
if row:
    default_ratio = row[0]
    # 更新所有比例为空的项目
    cur.execute("UPDATE projects SET ratio_scheme = ? WHERE ratio_scheme IS NULL OR ratio_scheme = ''", (default_ratio,))
    conn.commit()
    print("Historical projects backfilled with default ratio scheme.")
else:
    print("Default ratio scheme not found in system_settings.")

conn.close()
