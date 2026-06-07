import sqlite3
import os

def migrate():
    # Use data/app.db relative to this script
    db_path = os.path.join('data', 'app.db')
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    print(f"Connecting to database at {db_path}...")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column exists
        cursor.execute("PRAGMA table_info(projects)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if 'contact_person' not in columns:
            print("Adding contact_person column to projects table...")
            cursor.execute("ALTER TABLE projects ADD COLUMN contact_person VARCHAR(100) DEFAULT ''")
            conn.commit()
            print("Migration successful.")
        else:
            print("Column contact_person already exists.")
            
    except Exception as e:
        print(f"Migration failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
