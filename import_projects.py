import sys
import os
import pandas as pd
from datetime import datetime

# Set encoding to utf-8 for console output to avoid Windows encoding issues
sys.stdout.reconfigure(encoding='utf-8')

# from database import SessionLocal, engine
# from models import Project, Client, Contact, User
from backend.main import SessionLocal, engine, get_db, Project, Client, Contact, User
from sqlalchemy import select



def import_data():
    # Read excel, skipping first row (title)
    df = pd.read_excel("xm2025.xlsx", header=1)
    
    # Strip whitespace from column names
    df.columns = df.columns.astype(str).str.strip()
    
    print("Detected columns:", df.columns.tolist(), flush=True)
    
    db = next(get_db())
    
    count_new = 0
    count_skip = 0
    total_rows = len(df)
    print(f"Total rows found: {total_rows}", flush=True)
    
    for index, row in df.iterrows():
        # Clean data
        code = str(row.get('项目编号', '')).strip()
        name = str(row.get('项目名称', '')).strip()
        client_name = str(row.get('客户名称', '')).strip()
        contact_name = str(row.get('联系人', '')).strip()
        mobile = str(row.get('电话', '')).strip()
        
        if pd.isna(name) or not name or name.lower() == 'nan':
            # print(f"Skipping empty row {index + 1}/{total_rows}", flush=True)
            continue

        # Skip repeated header rows
        if name == '项目名称':
            continue
            
        if pd.isna(code) or code.lower() == 'nan':
            code = None
        else:
            # Check if code has .0 (e.g. 2501001.0) due to float conversion
            if code.endswith('.0'):
                code = code[:-2]

        print(f"[{index + 1}/{total_rows}] Processing: [{code}] {name}", flush=True)
        
        try:
            # 1. Handle Client
            client = None
            if client_name and client_name != 'nan':
                client = db.scalar(select(Client).where(Client.name == client_name))
                if not client:
                    print(f"  Creating new client: {client_name}", flush=True)
                    client = Client(name=client_name, status=1)
                    db.add(client)
                    db.flush() # get id
            
            # 2. Handle Contact (if created new client or just want to add)
            if client and contact_name and contact_name != 'nan':
                # Check if contact exists
                existing_contact = None
                if client.contacts:
                    for c in client.contacts:
                        if c.name == contact_name:
                            existing_contact = c
                            break
                
                if not existing_contact:
                    print(f"  Adding contact {contact_name} to {client_name}", flush=True)
                    new_contact = Contact(
                        client_id=client.id,
                        name=contact_name,
                        mobile=mobile if mobile != 'nan' else '',
                        is_primary=True # Assume first imported is primary
                    )
                    db.add(new_contact)

            # 3. Create Project
            # Check if project exists by code (if provided) or name
            existing_project = None
            if code:
                existing_project = db.scalar(select(Project).where(Project.code == code))
            
            if not existing_project:
                existing_project = db.scalar(select(Project).where(Project.name == name))

            if existing_project:
                print(f"  Project {name} already exists, checking for updates.", flush=True)
                count_skip += 1
                
                # Update code if missing
                if code and not existing_project.code:
                    existing_project.code = code
                    db.add(existing_project)
                
                # Update contact person
                if contact_name and contact_name != 'nan':
                    if existing_project.contact_person != contact_name:
                        existing_project.contact_person = contact_name
                        db.add(existing_project)
                        print(f"    Updated contact person to: {contact_name}", flush=True)

                continue
                
            # Create new
            new_project = Project(
                name=name,
                code=code,
                client_id=client.id if client else None,
                client_name=client.name if client else client_name,
                contact_person=contact_name if contact_name and contact_name != 'nan' else None,
                status="进行中", # Default to active
                created_at=datetime.utcnow(),
                status_changed_at=datetime.utcnow()
            )
            db.add(new_project)
            count_new += 1
            print(f"  Created project: {name}", flush=True)
            
        except Exception as e:
            print(f"Error processing row {index + 1}: {e}", flush=True)
            db.rollback()

    try:
        db.commit()
    except Exception as e:
        print(f"Error committing changes: {e}", flush=True)
        db.rollback()
        
    print(f"\nImport finished. Added: {count_new}, Skipped: {count_skip}", flush=True)

if __name__ == "__main__":
    import_data()
