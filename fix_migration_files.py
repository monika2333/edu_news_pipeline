import os

migrations_dir = "./database/migrations"

if not os.path.exists(migrations_dir):
    print(f"Error: Directory {migrations_dir} not found.")
    exit(1)

count = 0
for filename in os.listdir(migrations_dir):
    if not filename.endswith(".sql"):
        continue
        
    filepath = os.path.join(migrations_dir, filename)
    
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    new_content = content
    changed = False

    if "-- migrate:up" not in content:
        new_content = "-- migrate:up\n" + new_content
        changed = True
    
    if "-- migrate:down" not in content:
        # Ensure there is a newline before appending if not empty
        if new_content and not new_content.endswith("\n"):
             new_content += "\n"
        new_content = new_content + "\n-- migrate:down\n"
        changed = True
        
    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
             f.write(new_content)
        print(f"Fixed {filename}")
        count += 1
    else:
        print(f"Skipping {filename} (already complete)")

print(f"Total files fixed: {count}")
