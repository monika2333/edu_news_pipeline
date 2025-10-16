# crawl-practice

This repository contains a simple crawler that fetches search results from
[jyb.cn](http://www.jyb.cn/) and extracts structured article information.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Fetch with explicit keywords
python jyb_spider.py 教育 政策 --limit 3

# Or leave the keyword list empty to pull the site's default feed
python jyb_spider.py --limit 3
```

The script prints each article's title, canonical URL, publish time (if it can
be detected) and the main content rendered as Markdown. Use `--page` to fetch a
different page of search results and `--log-level` to change the verbosity
(defaults to `INFO`).

If you need the results as structured data, you can import the crawler and
write them to JSON:

```bash
python - <<'PY'
from jyb_spider import JYBSpider
import dataclasses, json, pathlib

articles = JYBSpider().search('', limit=50)  # empty keyword fetches default feed
pathlib.Path('jyb_latest.json').write_text(
    json.dumps([dataclasses.asdict(a) for a in articles], ensure_ascii=False, indent=2),
    encoding='utf-8',
)
PY
```
