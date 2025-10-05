# Edu News Pipeline

Automated pipeline for collecting Toutiao education articles, summarising them with an LLM, scoring relevance, and exporting daily briefs.

## Pipeline Overview

1. **Crawl** - Fetch latest Toutiao articles defined in `data/author_tokens.txt` and store them in the Postgres table `toutiao_articles`.
2. **Summarise** - Generate summaries for new articles and write them to `news_summaries`.
3. **Score** - Ask the LLM to rate each summary and persist the `correlation` score in `news_summaries`.
4. **Export** - Assemble the highest-scoring summaries into a plain-text brief and optionally log the batch metadata in `brief_batches` / `brief_items`.

All steps are available through the CLI wrapper:

```bash
python -m src.cli.main crawl --limit 500
python -m src.cli.main summarize --limit 100
python -m src.cli.main score --limit 100
python -m src.cli.main export --min-score 60 --limit 50
```

Use `-h` on any command to see flags.

## Directory Highlights

- `data/author_tokens.txt` - List of Toutiao author tokens/URLs (one per line, `#` for comments).
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
3. Apply the project schema: `psql -h localhost -U postgres -d postgres -f database/schema.sql` (repeat for any additional SQL in `database/migrations/`).
4. Populate `.env.local` with the `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_SCHEMA` settings.
5. Run the Postgres adapter validation: `python -m pytest tests/test_db_postgres_adapter.py` (install `pytest` if it is not already available).

With these variables in place the worker and console commands automatically use the Postgres backend via `src.adapters.db.get_adapter()`.

The pipeline loads variables from `.env.local`, `.env`, and `config/abstract.env`. Key settings:

| Variable | Description |
| --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | Connection details for the Postgres instance |
| `DB_SCHEMA` | Schema to target (defaults to `public`) |
| `TOUTIAO_AUTHORS_PATH` | Override authors list path (defaults to `data/author_tokens.txt`) |
| `TOUTIAO_FETCH_TIMEOUT` | Seconds for article fetch timeout (default 15) |
| `TOUTIAO_LANG` | `Accept-Language` header when fetching article content |
| `TOUTIAO_SHOW_BROWSER` | Set to `1` to run Playwright in headed mode |
| `PROCESS_LIMIT` | Global cap applied to worker limits |
| `CONCURRENCY` | Default worker concurrency override (falls back to 5) |
| `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` | API credentials and endpoint for the LLM provider |
| `SUMMARIZE_MODEL_NAME` / `SOURCE_MODEL_NAME` / `SCORE_MODEL_NAME` | Model identifiers used by the workers |


## Workflow Details

### Crawl Worker

- Command: `python -m src.cli.main crawl`
- Default limit: 500 articles (clamped by `PROCESS_LIMIT` if set)
- Reads author tokens from `TOUTIAO_AUTHORS_PATH`
- Writes new rows to `toutiao_articles`
- Skips articles already present in the database

### Summarise Worker

- Command: `python -m src.cli.main summarize`
- Uses `data/summarize_cursor.json` to resume from the last processed article
- Filters content against keywords from `education_keywords.txt`
- Stores generated summaries in `news_summaries`

### Score Worker

- Command: `python -m src.cli.main score`
- Selects `news_summaries` rows with `correlation` missing
- Calls the LLM scoring adapter and saves the resulting `correlation`

### Export Worker

- Default min score: 60 (override with `--min-score`).
- Existing output files get numbered suffixes (e.g. `(1)`, `(2)`) to avoid overwriting.
- Command: `python -m src.cli.main export`
- Pulls high-correlation summaries from `news_summaries`
- Writes a text brief (defaults to `outputs/high_correlation_summaries_<tag>.txt`)
- Optionally records batches in the database (`brief_batches` / `brief_items`)
- Set `--no-record-history` or `--no-skip-exported` to adjust behaviour

## Development Notes

- Source code lives under `src/`; the old `tools/` scripts have been removed in favour of the worker pipeline.
- Tests: include a CLI smoke test in `tests/test_cli_parser.py` and a Postgres adapter validation in `tests/test_db_postgres_adapter.py`; extend with integration coverage as needed.
- Formatting: project uses standard Python formatting (PEP 8). Run `python -m pip install black isort` and apply if needed.
- When adding new workers or commands, expose them via `src/cli/main.py` so they are available through `python -m src.cli.main ...`.

## Troubleshooting

| Issue | Fix |
| --- | --- |
| Crawl returns zero items | Ensure Playwright works (`playwright install chromium`), check author tokens, increase `--limit` |
| Summarise skips everything | Confirm keywords list, check `summarize_cursor.json`, set `--reset-cursor` manually (delete file) |
| Score/export find nothing | Make sure previous steps inserted rows into Postgres (`toutiao_articles` / `news_summaries`) |
| Database errors (connection / missing tables) | Verify Postgres credentials and apply the schema SQL before rerunning |

## License

MIT License (see repository root for details).


## Feishu Notifications

- Set the FEISHU_* environment variables (ID, secret, and receive ID). `FEISHU_RECEIVE_ID` or `FEISHU_OPEN_ID` both work (lowercase keys from older configs remain compatible).
- After a successful export, the worker posts a short summary *and* uploads the generated `.txt` as a Feishu file attachment.
- Text notifications include category counts and the first few entries; the full file is delivered through the attachment.
- Failures fall back gracefully and are logged in the export worker output.

## Scheduling and Automation

- **Linux/macOS cron**: schedule the full pipeline with `scripts/run_pipeline_once.py` (default steps crawl -> summarize -> score -> export).
  ```bash
  0 9 * * * /usr/bin/python /path/to/repo/scripts/run_pipeline_once.py
  ```
- **Windows Task Scheduler**: use the helper script in this repo. Example action command:
  ```powershell
  powershell.exe -File "D:\600program\edu_news_pipeline\scripts\run_pipeline_daily.ps1" -Python "C:\Path\To\python.exe"
  ```
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




