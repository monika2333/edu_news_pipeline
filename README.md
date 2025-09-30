# Edu News Pipeline

Automated pipeline for collecting Toutiao education articles, summarising them with an LLM, scoring relevance, and exporting daily briefs.

## Pipeline Overview

1. **Crawl** – Fetch latest Toutiao articles defined in `data/author_tokens.txt` and store them in the Supabase table `toutiao_articles`.
2. **Summarise** – Generate summaries for new articles and write them to `news_summaries`.
3. **Score** – Ask the LLM to rate each summary and persist the `correlation` score in `news_summaries`.
4. **Export** – Assemble the highest-scoring summaries into a plain-text brief and optionally log the batch in `brief_batches` / `brief_items`.

All steps are available through the CLI wrapper:

```bash
python run_pipeline.py crawl --limit 500
python run_pipeline.py summarize --limit 100
python run_pipeline.py score --limit 100
python run_pipeline.py export --min-score 70 --limit 50
```

Use `-h` on any command to see flags.

## Directory Highlights

- `data/author_tokens.txt` – List of Toutiao author tokens/URLs (one per line, `#` for comments).
- `src/adapters/db_supabase.py` – Supabase access layer shared by all workers.
- `src/workers/` – Implementations for `crawl`, `summarize`, `score`, and `export` steps.
- `src/cli/main.py` – CLI entry point used by `run_pipeline.py`.
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

- Command: `python run_pipeline.py crawl`
- Default limit: 500 articles (clamped by `PROCESS_LIMIT` if set)
- Reads author tokens from `TOUTIAO_AUTHORS_PATH`
- Writes new rows to `toutiao_articles`
- Skips articles already present in Supabase

### Summarise Worker

- Command: `python run_pipeline.py summarize`
- Uses `data/summarize_cursor.json` to resume from the last processed article
- Filters content against keywords from `education_keywords.txt`
- Stores generated summaries in `news_summaries`

### Score Worker

- Command: `python run_pipeline.py score`
- Selects `news_summaries` rows with `correlation` missing
- Calls the LLM scoring adapter and saves the resulting `correlation`

### Export Worker

- Command: `python run_pipeline.py export`
- Pulls high-correlation summaries from `news_summaries`
- Writes a text brief (defaults to `outputs/high_correlation_summaries_<tag>.txt`)
- Optionally records batches in Supabase (`brief_batches` / `brief_items`)
- Set `--no-record-history` or `--no-skip-exported` to adjust behaviour

## Development Notes

- Source code lives under `src/`; the old `tools/` scripts have been removed in favour of the worker pipeline.
- Tests: currently only placeholders in `tests/`; feel free to extend.
- Formatting: project uses standard Python formatting (PEP 8). Run `python -m pip install black isort` and apply if needed.
- When adding new workers or commands, expose them via `src/cli/main.py` so `run_pipeline.py` automatically supports them.

## Troubleshooting

| Issue | Fix |
| --- | --- |
| Crawl returns zero items | Ensure Playwright works (`playwright install chromium`), check author tokens, increase `--limit` |
| Summarise skips everything | Confirm keywords list, check `summarize_cursor.json`, set `--reset-cursor` manually (delete file) |
| Score/export find nothing | Make sure previous steps completed and Supabase contains data |
| Supabase errors (PGRST205 / missing tables) | Initialise the schema from `supabase/schema.sql` or adjust query targets |

## License

MIT License (see repository root for details).
