import sys
import os
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from backend.main import SessionLocal, Project
from sqlalchemy import select

def fix_dates():
    db = SessionLocal()
    try:
        projects = db.scalars(select(Project).where(Project.code.is_not(None))).all()
        count = 0
        for p in projects:
            if not p.code or len(p.code) != 7:
                continue
            
            try:
                # Code format: YYMM### (e.g. 2501001)
                yymm = p.code[:4]
                yy = int(yymm[:2])
                mm = int(yymm[2:])
                
                # Assume 20xx
                year = 2000 + yy
                
                # Check if created_at matches (roughly)
                if p.created_at.year != year or p.created_at.month != mm:
                    print(f"Fixing project {p.id} [{p.code}]: {p.created_at} -> {year}-{mm}-01")
                    # Set to 1st of that month, keeping time as 09:00:00 (work start) or just 00:00:00
                    new_date = datetime(year, mm, 1, 9, 0, 0)
                    p.created_at = new_date
                    # Also update status_changed_at if it's the same invalid date
                    if p.status_changed_at and p.status_changed_at.year == p.created_at.year and p.status_changed_at.month == p.created_at.month:
                         # Keep status_changed_at logic simple: if it looks like the import date, fix it too?
                         # For now just fix created_at as that drives the validation logic
                         pass
                    count += 1
            except ValueError:
                continue
        
        db.commit()
        print(f"Fixed {count} projects.")
        
    finally:
        db.close()

if __name__ == "__main__":
    fix_dates()
