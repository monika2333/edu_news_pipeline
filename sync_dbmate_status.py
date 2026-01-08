import os
import psycopg
from datetime import timezone
from src.config import get_settings

# Using existing config to get connection
settings = get_settings()
conn = psycopg.connect(
    host=settings.db_host,
    port=settings.db_port,
    user=settings.db_user,
    password=settings.db_password,
    dbname=settings.db_name,
    autocommit=True
)

migrations_dir = "./database/migrations"
table_name = "schema_migrations"

# 1. Ensure table exists (dbmate usually creates it, but if 'up' failed totally, maybe not)
# But dbmate up usually creates it first thing. Let's assume it exists or create it.
with conn.cursor() as cur:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            version VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

# 2. Get all migration versions from files
files = [f for f in os.listdir(migrations_dir) if f.endswith(".sql")]
versions = []
for f in files:
    # Version is the part before the first underscore
    # e.g. 20241112105800_name.sql -> 20241112105800
    parts = f.split("_")
    if parts:
        versions.append(parts[0])

versions.sort()

# 3. Insert into table (ignore duplicates)
inserted = 0
with conn.cursor() as cur:
    for v in versions:
        # Check if exists
        cur.execute(f"SELECT 1 FROM {table_name} WHERE version = %s", (v,))
        if cur.fetchone():
            continue
            
        cur.execute(f"INSERT INTO {table_name} (version) VALUES (%s)", (v,))
        inserted += 1

print(f"Synced {inserted} migrations to {table_name}.")
conn.close()
