# Edu News Pipeline

Automated pipeline for collecting education-related articles, summarising them with an LLM, scoring relevance, and exporting daily briefs.

## Pipeline Overview

1. **Crawl** - Fetch latest articles from configured sources (default: Toutiao; optional: ChinaNews, China Daily, Guangming Daily, China Education Daily), upsert feed metadata into `raw_articles`, ensure bodies are fetched, and enqueue keyword-positive articles into `filtered_articles` with status `pending`.
2. **Hash / Deduplicate** - `hash_primary` computes an exact `content_hash`, 64-bit SimHash, and four 16-bit band hashes for each filtered article. Using SimHash band lookup and a Hamming-distance threshold (<= 3), duplicates are grouped under a primary article and promoted to `primary_articles`.
3. **Score** - LLM-based relevance scoring runs on entries in `primary_articles`. The LLM output becomes `raw_relevance_score`; keyword rules add a `keyword_bonus_score`, and their sum is persisted as `score`. Promotion still keys off `raw_relevance_score >= 60`, while the final score (without an upper bound) is used for ordering.
4. **Summarise & Sentiment** - `summarize` generates LLM summaries for promoted primaries, classifies sentiment (`positive` / `negative`), and writes the results back into `news_summaries` with status `ready_for_export` (failed attempts remain `pending`).
5. **Export** - Assemble the ready summaries into a briefing ordered by "Jingnei/Jingwai x Positive/Negative" buckets (sorted descending by score) and persist batch metadata in `brief_batches` / `brief_items`, sending an optional Feishu notification.

All stages are available through the CLI wrapper:

```bash
python -m src.cli.main crawl --sources toutiao,chinanews,chinadaily,jyb,gmw --limit 5000
python -m src.cli.main hash-primary --limit 200
python -m src.cli.main score --limit 500
python -m src.cli.main summarize --limit 500
python -m src.cli.main export --min-score 60 --limit 500
python -m src.cli.main repair --limit 500
python -m src.cli.main geo-tag --limit 500 --batch-size 200
```

Use `-h` on any command to see flags. `summarize` now operates on the queued pending rows—run `crawl` first so new candidates are available.

## Repairing Missing Content

If earlier runs inserted feed rows without article bodies, use the repair worker to fill them in. It will fetch only rows where `content_markdown` is empty and update them in place.

```bash
python -m src.cli.main repair --limit 500
```

Re-run as needed until the command reports no articles remaining.
## Directory Highlights

- `data/toutiao_author.txt` - List of Toutiao author tokens/URLs (one per line, `#` for comments). Used when crawling `--sources toutiao`.
- `src/adapters/db.py` - Singleton loader for the Postgres adapter.
- `src/adapters/db_postgres.py` - PostgreSQL access layer used by all workers.
- `src/workers/` - Implementations for `crawl`, `summarize`, `score`, and `export` steps.
- `database/` - SQL schema and migrations used for the Postgres deployment.
- `src/cli/main.py` - CLI entry point for worker commands (`python -m src.cli.main ...`).

## Prerequisites

- Python 3.10+
- PostgreSQL 16+ (or compatible) with credentials for the target database
- `pip install -r requirements.txt`
- Playwright Chromium browser for crawling:
  ```bash
  playwright install chromium
  ```

## Environment Configuration

### Local PostgreSQL Quick Start

1. Install PostgreSQL 16+ (the team standard uses Windows packages under `C:\Program Files\PostgreSQL\18`).
2. Ensure the service is running and note the administrator credentials (default user: `postgres`).
3. Apply the project schema: `psql -h localhost -U postgres -d postgres -f database/schema.sql`.\n   - Then apply migrations under `database/migrations/` as needed. Notably, `20251007194500_rename_toutiao_to_raw_articles.sql` renames `toutiao_articles` to `raw_articles` for multi-source support (safe to run multiple times).
4. Populate `.env.local` with the `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_SCHEMA` settings.
5. Run the Postgres adapter validation: `python -m pytest tests/test_db_postgres_adapter.py` (install `pytest` if it is not already available).

With these variables in place the worker and console commands automatically use the Postgres backend via `src.adapters.db.get_adapter()`.

The pipeline loads variables from `.env.local`, `.env`, and `config/abstract.env`. Key settings:

| Variable | Description |
| --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Connection details for the Postgres instance |
| `DB_SCHEMA` | Schema to target (defaults to `public`) |
| `TOUTIAO_AUTHORS_PATH` | Override Toutiao authors list path (defaults to `data/toutiao_author.txt`) |
| `TOUTIAO_FETCH_TIMEOUT` | Seconds for article fetch timeout (default 15) |
| `TOUTIAO_LANG` | `Accept-Language` header when fetching article content |
| `TOUTIAO_SHOW_BROWSER` | Set to `1` to run Playwright in headed mode |
| `GMW_BASE_URL` | Override Guangming Daily listing entry point |
| `GMW_TIMEOUT` | Seconds for Guangming Daily HTTP requests (default 15) |
| `PROCESS_LIMIT` | Global cap applied to worker limits |
| `SCORE_KEYWORD_BONUSES` | Optional JSON map overriding keyword ?bonus rules for scoring |
| `SCORE_KEYWORD_BONUSES_PATH` | Optional path to a JSON file providing keyword bonus rules (`data/score_keyword_bonuses.json` by default) |
| `CONCURRENCY` | Default worker concurrency override (falls back to 5) |
| `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` | API credentials and endpoint for the LLM provider |
| `SUMMARIZE_MODEL_NAME` / `SOURCE_MODEL_NAME` / `SCORE_MODEL_NAME` | Model identifiers used by the workers |
| `CRAWL_SOURCES` | Comma list of sources used by the pipeline wrapper (e.g., `toutiao,chinanews`; default `toutiao`) |
| `TOUTIAO_EXISTING_CONSECUTIVE_STOP` | Early‑stop after N consecutive existing items per author (default `5`; set `0` to disable) |
| `CHINANEWS_EXISTING_CONSECUTIVE_STOP` | Early‑stop after N consecutive existing items across scroll pages (default `5`; set `0` to disable) |


## Workflow Details

### Crawl Worker

- Command: `python -m src.cli.main crawl`
- Default limit: 500 articles (clamped by `PROCESS_LIMIT` if set)
- Sources: `--sources` comma list (default `toutiao`; add `chinanews` and/or `gmw` for additional feeds). The pipeline wrapper also respects `CRAWL_SOURCES` from env (e.g., `CRAWL_SOURCES=toutiao,chinanews,gmw`).
  - Toutiao uses Playwright (requires `playwright install chromium`) and reads authors from `TOUTIAO_AUTHORS_PATH`
  - Guangming Daily uses the bundled HTTP crawler (no Playwright). Configure the entry point with `GMW_BASE_URL` if you need a different node and tweak `GMW_TIMEOUT` to adjust the per-request timeout.
- Writes/updates rows in `raw_articles`
- Skips articles already present in the database

- Early‑stop policy for duplicates:
  - Toutiao: while scanning each author’s feed, stops after `TOUTIAO_EXISTING_CONSECUTIVE_STOP` consecutive items already present in the DB (default 5). Set it to `0` to never early‑stop on existing items.
  - ChinaNews: while iterating scroll pages, skips existing items and stops when `CHINANEWS_EXISTING_CONSECUTIVE_STOP` consecutive items are already present (default 5). Set it to `0` to never early‑stop on existing items.

#### Examples
- ChinaNews (first page only): `python -m src.cli.main crawl --sources chinanews --limit 50`
- ChinaNews (multi-page to approach 500): `python -m src.cli.main crawl --sources chinanews --limit 500 --pages 15`
- Toutiao + ChinaNews (total 500, sequential consumption): `python -m src.cli.main crawl --sources toutiao,chinanews --limit 500`
- Guangming Daily only: `python -m src.cli.main crawl --sources gmw --limit 100`
- Toutiao + ChinaNews + Guangming Daily: `python -m src.cli.main crawl --sources toutiao,chinanews,gmw --limit 500`
- Repair missing bodies (all sources): `python -m src.cli.main repair --limit 200`

#### Multi-source allocation
- `--limit` is a total upper bound per run.
- Sources are processed in the order you pass in `--sources` (e.g., `toutiao,chinanews`). Each source consumes from the remaining quota; there is no auto even-split.
- If you prefer fixed quotas (e.g., Toutiao 300 + ChinaNews 200), run separate commands for each for now.

#### ChinaNews specifics
- Paging: use `--pages N` to fetch multiple feed pages. Default is 1; it does not auto-flip without `--pages`.
  - Example: `python -m src.cli.main crawl --sources chinanews --limit 500 --pages 10`
  - The crawler reads the page navigator (`.pagebox`) and will not exceed the last available page.
- Published time: derived from the feed item (`.dd_time`) combined with the URL date. Stored as tz-aware (+08:00); exports can render `YYYY-MM-DD HH:MM`.
- Source (媒体来源): extracted from visible nodes (selectors aligned with our reference crawler), then fallback to meta tags.

#### Guangming Daily specifics
- Uses the custom HTTP crawler bundled in `src/adapters/http_gmw.py` (legacy CLI preserved in `gmw_crawl/` for now) to walk listing and detail pages, so each run fetches full article bodies without a second repair step.
- Publish time is parsed from article metadata or body; when available it is normalised to +08:00 and stored alongside the Unix timestamp.
- Requests honour `GMW_BASE_URL` and `GMW_TIMEOUT`. Duplicate URLs within a run are de-duplicated before writing to the database.

### Summarise Worker

- Command: `python -m src.cli.main summarize`
- Filters content against keywords from `data/education_keywords.txt` (override with `KEYWORDS_PATH`)
- Stores generated summaries in `news_summaries`

### Score Worker

- Command: `python -m src.cli.main score`
- Scores entries in `primary_articles` where status is pending/failed or `score` is `NULL`
- Persists:
  - `raw_relevance_score`: the raw LLM output (0?00 clamp removed)
  - `keyword_bonus_score`: additive bonus from keyword rules
  - `score`: `raw + bonus` (no upper limit) for downstream ordering
  - `score_details`: JSON metadata documenting matched rules and totals
- Promotion to `news_summaries` still hinges on `raw_relevance_score ?60`

#### Scoring Metrics Helper

- Command: `python scripts/pipeline_metrics.py --days 7`
- Outputs status counts for `primary_articles` / `news_summaries` plus aggregates for `raw_relevance_score`, `keyword_bonus_score`, and final `score`
- Pass `--days 0` to inspect the full history; omit the flag (default 7) for a recent view
- Useful for gauging how keyword rules affect the pipeline and verifying bonuses are being applied

### Export Worker

- Default min score: 60 (override with `--min-score`).
- Existing output files get numbered suffixes (e.g. `(1)`, `(2)`) to avoid overwriting.
- Command: `python -m src.cli.main export`
- Pulls high-score summaries from `news_summaries`.
- Writes a text brief (defaults to `outputs/high_score_summaries_<tag>.txt`), grouping entries into `[Beijing]` / `[Non-Beijing]` sections and sorting each section by descending score.
- Optionally records batches in the database (`brief_batches` / `brief_items`), storing the `is_beijing_related` flag in the metadata.
- Set `--no-record-history` or `--no-skip-exported` to adjust behaviour.

### Beijing Relevance Tagging

- Adds the `news_summaries.is_beijing_related` field to flag whether an article is Beijing-related. The `summarize` worker sets it by default when writing summaries, based on the article body, summary, and keyword hits.
- Keyword list lives in `data/beijing_keywords.txt`. Override it with the `BEIJING_KEYWORDS_PATH` environment variable if you need a custom file.
- To backfill older data or after tweaking keywords, run `python -m src.cli.main geo-tag --limit 200 --batch-size 200` (trim the scope if needed). The command batches through rows where `is_beijing_related IS NULL` and writes the boolean back.
- Export output and Feishu notifications use this field to split "Beijing" vs "non-Beijing" sections and include count summaries; the flag is also copied into `brief_items.metadata`.

## Development Notes

- Source code lives under `src/`; the old `tools/` scripts have been removed in favour of the worker pipeline.
- Tests: include a CLI smoke test in `tests/test_cli_parser.py` and a Postgres adapter validation in `tests/test_db_postgres_adapter.py`; extend with integration coverage as needed.
- Formatting: project uses standard Python formatting (PEP 8). Run `python -m pip install black isort` and apply if needed.
- When adding new workers or commands, expose them via `src/cli/main.py` so they are available through `python -m src.cli.main ...`.

## Troubleshooting

| Issue | Fix |
| --- | --- |
| Crawl returns zero items | Ensure Playwright works (Toutiao: `playwright install chromium`), check author tokens/`--pages`, increase `--limit` |
| Summarise skips everything | Confirm keywords list; ensure pending rows exist in `news_summaries`; adjust `--limit` or `PROCESS_LIMIT` if needed |
| Score/export find nothing | Make sure previous steps inserted rows into Postgres (`raw_articles` / `news_summaries`) |
| Database errors (connection / missing tables) | Verify Postgres credentials and apply the schema SQL before rerunning |

## License

MIT License (see repository root for details).


## Feishu Notifications

- Set the FEISHU_* environment variables (ID, secret, and receive ID). `FEISHU_RECEIVE_ID` or `FEISHU_OPEN_ID` both work (lowercase keys from older configs remain compatible).
- After a successful export, the worker posts a short summary *and* uploads the generated `.txt` as a Feishu file attachment.
- Text notifications include category counts and the first few entries; the full file is delivered through the attachment.
- Failures fall back gracefully and are logged in the export worker output.

## Scheduling and Automation

- **Linux/macOS cron**: schedule the full pipeline with `scripts/run_pipeline_once.py` (default steps crawl -> hash-primary -> score -> summarize -> export).
  ```bash
  0 9 * * * /usr/bin/python /path/to/repo/scripts/run_pipeline_once.py
```
- **Windows Task Scheduler**: use the helper script in this repo. Example action command:
  ```powershell
  powershell.exe -File "D:\600program\edu_news_pipeline\scripts\run_pipeline_daily.ps1" -Python "C:\Path\To\python.exe"
```

### China Daily Source

- Enable via CLI: `--sources chinadaily` (can be combined, e.g. `--sources toutiao,chinanews,chinadaily,gmw`).
- Optional flags: `--pages N` to bound pagination.
- Environment variables:
  - `CHINADAILY_START_URL` ?Channel listing entry (defaults to a China Daily site channel).
  - `CHINADAILY_TIMEOUT` ?Request timeout in seconds (default `20`).
  - `CHINADAILY_EXISTING_CONSECUTIVE_STOP` ?Early-stop after N consecutive existing items across pages (default `5`; `0` disables).

### China Education Daily (JYB)

- Enable via CLI: `--sources jyb` (can be combined, e.g. `--sources toutiao,chinanews,chinadaily,jyb,gmw`).
- Optional flags: `--pages N` to bound pagination.
- Environment variables:
  - `JYB_SEARCH_API_URL` ?JSON search API endpoint (defaults to `http://new.jyb.cn/jybuc/hyBaseCol/search.action`).
  - `JYB_START_URL` ?Fallback HTML listing/search page (defaults to `http://www.jyb.cn/search.html`).
  - `JYB_KEYWORDS` ?Optional keywords (comma-separated). Default: `教育`.
  - `JYB_TIMEOUT` ?Request timeout in seconds (default `20`).
  - `JYB_EXISTING_CONSECUTIVE_STOP` ?Early-stop after N consecutive existing items across pages (default `5`; `0` disables).
  Configure the trigger to run daily at your preferred time, enable "Run with highest privileges", and disable battery-stop conditions when needed.
- Customise steps with script parameters such as `-Steps crawl summarize`, `-Skip score`, or `-ContinueOnError`. Logs default to `logs/pipeline_<timestamp>.log`; override via `-LogDirectory`.

- For high-frequency refresh (e.g. crawl/summarize/score every 10 minutes), use `scripts/run_pipeline_every10.ps1` with a Task Scheduler trigger that repeats every 10 minutes ("Repeat task every" -> `10 minutes`, "for a duration of" -> `Indefinitely`).
  Example action command:
  ```powershell
  powershell.exe -File "D:ƀprogram\edu_news_pipeline\scripts
un_pipeline_every10.ps1" -Python "C:\Path\To\python.exe" -LogDirectory "D:\logs\edu-news-10min"
  ```
  The script maintains a lock under `locks\pipeline_every10.lock` to avoid overlapping runs; optional `-ContinueOnError` keeps later steps running after a failure.
- Continue using daily scheduling (see above) for the full crawl→export pipeline, and trigger `export` on demand when you need the latest brief.

- For ad-hoc single steps, call the CLI directly (`python -m src.cli.main summarize --limit 50`).

## Legacy Tooling Sunset

- The historical `tools/` directory has been removed; remaining shim scripts simply warn and forward to the worker entry points.
- Target date to delete those shims entirely is 2025-10-31, after verifying no external automation depends on them.
- Migrate any outstanding scripts to the new commands (`python -m src.cli.main ...`) or `scripts/run_pipeline_once.py` before that deadline.







## Logs Maintenance

- Baseline: compress logs older than 3 days and delete logs (including archives) older than 14 days.
- Manual run: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/clean-logs.ps1`
  - Options: `-LogsPath logs -CompressOlderThanDays 3 -DeleteOlderThanDays 14 -DryRun`
- Register daily cleanup (02:00):
  - `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/register-clean-logs-task.ps1 -Time 02:00 -TaskName EduNews_CleanLogs`
  - Verify: `schtasks /Query /TN EduNews_CleanLogs /V /FO LIST`

