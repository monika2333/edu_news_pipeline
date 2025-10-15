# crawl-practice

This repository contains a small crawler script for the Guangming Daily channel at
[https://news.gmw.cn/node_4108.htm](https://news.gmw.cn/node_4108.htm).

## Requirements

The crawler now relies solely on Python's standard library modules, so no
third-party packages are required.

## Usage

Run the crawler from the repository root:

```bash
python gmw_crawler.py --max-articles 5 --output articles.json
```

By default, it discovers additional listing pages linked from the node so it can
collect larger batches (for example `--max-articles 100` will walk through
multiple pages until at least 100 unique articles are gathered). The script
requires network access to reach `news.gmw.cn`; if outbound HTTP requests are
blocked (for example by a corporate proxy), it will log warnings about skipped
pages and produce an empty result set.

- Handles gzip/deflate-compressed responses automatically so current pages on
  `news.gmw.cn` are parsed correctly.
- Forces UTF-8 output on stdout/stderr, preventing mojibake when Chinese text is
  displayed in a console using a different default code page.

- `--url`: Override the listing page URL (defaults to `https://news.gmw.cn/node_4108.htm`).
- `--max-articles`: Limit the number of articles to crawl.
- `--timeout`: Request timeout in seconds (default `15`).
- `--output`: Path to save the JSON output. If omitted, the JSON is printed to stdout.
- `--indent`: Indentation level for the JSON output (default `2`).
- `--log-level`: Logging level (default `INFO`).

Each article entry in the JSON output contains the `title`, canonical `url`,
`publish_time`, and the article body converted to basic Markdown in `content_markdown`.
