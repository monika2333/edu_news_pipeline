import sys
import os

# Add project root to sys.path
sys.path.append(os.getcwd())

try:
    from src.config import get_settings
    s = get_settings()
    
    # Construct DATABASE_URL
    # Handle password escaping if necessary, but simple format helps for now
    # Dbmate expects URL encoded password if special chars present
    import urllib.parse
    password = urllib.parse.quote_plus(s.db_password) if s.db_password else ""
    user = urllib.parse.quote_plus(s.db_user) if s.db_user else ""
    
    url = f"postgres://{user}:{password}@{s.db_host}:{s.db_port}/{s.db_name}?sslmode=disable"
    
    env_file = ".env"
    
    # Check if DATABASE_URL already exists
    content = ""
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            content = f.read()
    
    if "DATABASE_URL=" not in content:
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f"\nDATABASE_URL=\"{url}\"\n")
        print("Success: Appended DATABASE_URL to .env")
    else:
        print("Info: DATABASE_URL already exists in .env")

except Exception as e:
    print(f"Error: {e}")
