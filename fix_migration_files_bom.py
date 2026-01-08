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
    
    # Read with utf-8-sig to remove BOM if present
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filename}: {e}")
        continue
    
    new_content = content
    changed = False

    # Check directives
    if "-- migrate:up" not in content:
        new_content = "-- migrate:up\n" + new_content
        changed = True
    
    if "-- migrate:down" not in content:
        if new_content and not new_content.endswith("\n"):
             new_content += "\n"
        new_content = new_content + "\n-- migrate:down\n"
        changed = True
        
    # Always write back if changed OR if we suspect BOM removal is needed
    # But checking for BOM removal programmatically after read is tricky as 'content' has it removed.
    # So we just rewrite all files to be safe? Or compare with raw read?
    # Simplest: just rewrite if we changed content. 
    # But if content didn't change (because directives exist) but BOM was present and stripped by read,
    # 'content' variable is clean. 'new_content' is clean.
    # If we don't write, the file on disk still has BOM.
    # So we should ALWAYS write back if we want to strip BOM.
    
    # Let's write back always for now to ensure BOM is gone.
    # But wait, if I write back `content` which lacks directives (if I didn't add them), I am fine?
    # No, 'content' read by utf-8-sig has directives if they were there.
    # So writing 'new_content' is always safe and ensures utf-8 (no BOM).
    
    with open(filepath, "w", encoding="utf-8") as f:
         f.write(new_content)
    
    print(f"Processed {filename}")
    count += 1

print(f"Total files processed: {count}")
