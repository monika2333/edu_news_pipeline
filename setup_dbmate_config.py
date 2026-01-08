import os

env_file = ".env"
additions = [
    '\nDBMATE_MIGRATIONS_DIR="./database/migrations"',
    '\nDBMATE_SCHEMA_FILE="./database/schema.sql"'
]

try:
    with open(env_file, "a", encoding="utf-8") as f:
        for line in additions:
            f.write(line)
    print("Success: Appended config to .env")
except Exception as e:
    print(f"Error: {e}")
