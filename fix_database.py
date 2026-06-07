import os
import sqlite3
from datetime import datetime

def fix_database():
    # 数据库路径
    db_path = 'data/app.db'
    backup_path = 'data/app.db.backup'
    
    # 确保备份存在
    if not os.path.exists(backup_path):
        print(f"Error: Backup file {backup_path} does not exist.")
        return
    
    print("Fixing database...")
    
    # 连接到数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 检查 clients 表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='clients'")
        if not cursor.fetchone():
            print("Creating new 'clients' table...")
            cursor.execute('''
                CREATE TABLE clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    client_type TEXT DEFAULT 'company',
                    tax_id TEXT,
                    address TEXT DEFAULT '',
                    notes TEXT DEFAULT '',
                    status INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        # 检查 contact_persons 表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='contact_persons'")
        if not cursor.fetchone():
            print("Creating new 'contact_persons' table...")
            cursor.execute('''
                CREATE TABLE contact_persons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    position TEXT DEFAULT '',
                    department TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
                    mobile TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    wechat TEXT DEFAULT '',
                    is_primary BOOLEAN DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE CASCADE
                )
            ''')
        
        # 提交更改
        conn.commit()
        print("Database structure has been fixed successfully!")
        
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    fix_database()
