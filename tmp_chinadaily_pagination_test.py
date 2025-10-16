from src.adapters.http_chinadaily import list_items

items = list_items(limit=50, pages=10, existing_ids=set())
print("chinadaily items:", len(items))
for i, it in enumerate(items[:10]):
    print(f"{i+1:02d}. {it.title[:60]} | {it.url} | {it.publish_time_iso}")
