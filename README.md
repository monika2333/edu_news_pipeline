# Edu News Pipeline

Automated pipeline for collecting Toutiao education articles, summarising them with an LLM, scoring relevance, and exporting daily briefs.

## Pipeline Overview

1. **Crawl** – Fetch latest Toutiao articles defined in `data/author_tokens.txt` and store them in the Supabase table `toutiao_articles`.
2. **Summarise** – Generate summaries for new articles and write them to `news_summaries`.
3. **Score** – Ask the LLM to rate each summary and persist the `correlation` score in `news_summaries`.
4. **Export** – Assemble the highest-scoring summaries into a plain-text brief and optionally log the batch in `brief_batches` / `brief_items`.

All steps are available through the CLI wrapper:

```bash
python -m src.cli.main crawl --limit 500
python -m src.cli.main summarize --limit 100
python -m src.cli.main score --limit 100
python -m src.cli.main export --min-score 60 --limit 50
```

Use `-h` on any command to see flags.

## Directory Highlights

- `data/author_tokens.txt` – List of Toutiao author tokens/URLs (one per line, `#` for comments).
- `src/adapters/db_supabase.py` – Supabase access layer shared by all workers.
- `src/workers/` – Implementations for `crawl`, `summarize`, `score`, and `export` steps.
- `src/cli/main.py` - CLI entry point for worker commands (`python -m src.cli.main ...`).
- `supabase/` – Reference SQL schema for Supabase tables (run separately when provisioning a new project).

## Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- Supabase project (REST and PostgreSQL endpoints)
- Playwright Chromium browser for crawling:
  ```bash
  playwright install chromium
  ```

## Environment Configuration

The pipeline loads variables from `.env.local`, `.env`, and `config/abstract.env`. Key settings:

| Variable | Description |
| --- | --- |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` (or `SUPABASE_KEY` / `SUPABASE_ANON_KEY`) | Supabase API key |
| `SUPABASE_DB_PASSWORD` | Postgres password (for crawl upserts) |
| `SUPABASE_DB_USER` / `SUPABASE_DB_NAME` / `SUPABASE_DB_PORT` | Optional Postgres overrides |
| `SUPABASE_DB_SCHEMA` | Schema name (defaults to `public`) |
| `TOUTIAO_AUTHORS_PATH` | Override authors list path (defaults to `data/author_tokens.txt`) |
| `TOUTIAO_FETCH_TIMEOUT` | Seconds for article fetch timeout (default 15) |
| `TOUTIAO_LANG` | `Accept-Language` header when fetching article content |
| `TOUTIAO_SHOW_BROWSER` | Set to `1` to run Playwright in headed mode |
| `PROCESS_LIMIT` | Global cap applied to worker limits |

Supabase credentials must be present before running any worker.

## Workflow Details

### Crawl Worker

- Command: `python -m src.cli.main crawl`
- Default limit: 500 articles (clamped by `PROCESS_LIMIT` if set)
- Reads author tokens from `TOUTIAO_AUTHORS_PATH`
- Writes new rows to `toutiao_articles`
- Skips articles already present in Supabase

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
- Optionally records batches in Supabase (`brief_batches` / `brief_items`)
- Set `--no-record-history` or `--no-skip-exported` to adjust behaviour

## Development Notes

- Source code lives under `src/`; the old `tools/` scripts have been removed in favour of the worker pipeline.
- Tests: include a CLI smoke test in `tests/test_cli_parser.py`; extend with integration coverage as needed.
- Formatting: project uses standard Python formatting (PEP 8). Run `python -m pip install black isort` and apply if needed.
- When adding new workers or commands, expose them via `src/cli/main.py` so they are available through `python -m src.cli.main ...`.

## Troubleshooting

| Issue | Fix |
| --- | --- |
| Crawl returns zero items | Ensure Playwright works (`playwright install chromium`), check author tokens, increase `--limit` |
| Summarise skips everything | Confirm keywords list, check `summarize_cursor.json`, set `--reset-cursor` manually (delete file) |
| Score/export find nothing | Make sure previous steps completed and Supabase contains data |
| Supabase errors (PGRST205 / missing tables) | Initialise the schema from `supabase/schema.sql` or adjust query targets |

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
  powershell.exe -File "D:ƀprogram\edu_news_pipeline\scriptsun_pipeline_every10.ps1" -Python "C:\Path\To\python.exe" -LogDirectory "D:\logs\edu-news-10min"
  ```
  The script maintains a lock under `locks\pipeline_every10.lock` to avoid overlapping runs; optional `-ContinueOnError` keeps later steps running after a failure.
- Continue using daily scheduling (see above) for the full crawl→export pipeline, and trigger `export` on demand when you need the latest brief.

- For ad-hoc single steps, call the CLI directly (`python -m src.cli.main summarize --limit 50`).

## Legacy Tooling Sunset

- The historical `tools/` directory has been removed; remaining shim scripts simply warn and forward to the worker entry points.
- Target date to delete those shims entirely is 2025-10-31, after verifying no external automation depends on them.
- Migrate any outstanding scripts to the new commands (`python -m src.cli.main ...`) or `scripts/run_pipeline_once.py` before that deadline.
