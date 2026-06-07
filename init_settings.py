import sqlite3
import os
import json

db_path = r'e:\My_AI_Projects\archviz-biz-manager\data\app.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# 初始系统设置数据
settings = [
    ('default_ratio_plan', json.dumps({"方案": 5, "建模": 12, "渲染": 5, "后期": 8}, ensure_ascii=False), '默认提成比例方案'),
    ('price_modeling', '80.00', '建模单点计件价'),
    ('price_rendering', '50.00', '渲染单张计件价'),
    ('price_post', '50.00', '后期单张计件价'),
    ('default_tax_rate', '5.00', '默认税率'),
]

for key, val, desc in settings:
    cur.execute("REPLACE INTO system_settings (key, value, description, updated_at) VALUES (?, ?, ?, datetime('now'))", (key, val, desc))

conn.commit()
conn.close()
print("System default settings initialized successfully.")
