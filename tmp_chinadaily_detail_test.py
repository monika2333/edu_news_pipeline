from src.adapters.http_chinadaily import list_items, fetch_detail

items = list_items(limit=5, pages=3, existing_ids=set())
print("sample items:", len(items))
for i, it in enumerate(items, start=1):
    d = fetch_detail(it.url)
    content = d.get('content_markdown') or ''
    print(f"{i}. title={d.get('title')!r} len={len(content)} pub={d.get('publish_time_iso')} url={d.get('url')}")
