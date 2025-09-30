# Edu News Pipeline

This project ingests Toutiao education news, stores raw articles in Supabase, generates LLM summaries, scores their relevance, and exports high-signal briefs.

## High-level flow
1. **crawl** – grab fresh Toutiao articles via Playwright and push them into `raw_articles` / `toutiao_articles` (worker stub ready).
2. **summarize** – read pending articles, filter by keywords, ask SiliconFlow for summaries, upsert into `news_summaries`.
3. **score** – compute an education-correlation score for each summary and store it back in Supabase.
4. **export** – assemble high scoring summaries into daily briefs and record the export batch history.

All production code now lives under `src/` with clear boundaries between adapters, domain models, workers, and the CLI entry point.

```
edu_news_pipeline/
├─ run_pipeline.py          # CLI shim (python run_pipeline.py <command>)
├─ src/
│  ├─ config.py             # Environment settings loader (.env.local, etc.)
│  ├─ domain/               # States, scoring stubs, shared dataclasses
│  ├─ adapters/             # Supabase + SiliconFlow adapters
│  ├─ workers/              # crawl / summarize / score / export workers
│  └─ cli/main.py           # edu-news command dispatcher
├─ tools/                   # Support data (e.g. author token lists)
├─ supabase/                # Schema + migrations
├─ tests/                   # Smoke tests
└─ ...                      # Keywords, outputs, replace-news-JGW, etc.
```

## Environment & dependencies

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium   # first run of the crawler
```

Configuration is read (in order) from `.env.local`, `.env`, and `config/abstract.env`. Key variables:

| Area | Variables |
| --- | --- |
| Supabase REST / PostgREST | `SUPABASE_URL`, `SUPABASE_KEY` / `SUPABASE_SERVICE_ROLE_KEY` |
| Supabase direct Postgres | `SUPABASE_DB_PASSWORD`, optional `SUPABASE_DB_USER`, `SUPABASE_DB_HOST`, `SUPABASE_DB_PORT`, `SUPABASE_DB_NAME`, `SUPABASE_DB_SCHEMA` |
| SiliconFlow LLM | `SILICONFLOW_API_KEY`, optional `MODEL_NAME`, `CONCURRENCY`, `ENABLE_THINKING`, `SILICONFLOW_BASE_URL` |
| Pipeline tuning | `PROCESS_LIMIT`, `KEYWORDS_PATH`, `CONCURRENCY` |

## CLI usage

All day-to-day tasks run through the unified controller:

```
python run_pipeline.py <command> [options]
```

| Command | Description | Common options |
| --- | --- | --- |
| `crawl` | Collect raw Toutiao articles | `--limit`, `--concurrency` |
| `summarize` | Generate LLM summaries for pending articles | `--limit`, `--concurrency`, `--keywords PATH` |
| `score` | Score summaries for education relevance | `--limit`, `--concurrency` |
| `export` | Produce briefs from high scoring summaries | `--limit`, `--date YYYY-MM-DD`, `--report-tag TAG`, `--min-score`, `--(no-)skip-exported`, `--(no-)record-history`, `--output PATH` |

Examples:

```
# Summarize 100 articles with a custom keyword list
python run_pipeline.py summarize --limit 100 --keywords data/custom_keywords.txt

# Score summaries sequentially when rate limited
python run_pipeline.py score --limit 200 --concurrency 1

# Export today's brief without writing back history
python run_pipeline.py export --date 2025-09-30 --min-score 65 --no-record-history
```

## Scheduling notes

- Each command is idempotent and prints structured `[worker]` logs with totals (`ok=... failed=...`).
- Recommended cron spacing: crawl (hourly), summarize (hourly), score (hourly), export (daily). Use `--limit` to throttle per window.
- Plan worker concurrency carefully (respect SiliconFlow rate limits). Use `--concurrency 1` for serial execution.

## Smoke tests

Run the quick parser/import checks before deployments:

```
pytest tests/test_smoke.py
```

The smoke suite ensures the CLI can be built and all worker entry points are importable without hitting external services.

## Legacy tools & deprecation plan

Scripts under `tools/` now only emit a deprecation warning and forward to the new workers. Once the CLI workflows are battle-tested in production, remove the `tools/*.py` shims and update automation to call `python run_pipeline.py ...` exclusively.

## Troubleshooting

- **Playwright failures**: rerun `playwright install chromium` or add `--show-browser` in the future crawler implementation.
- **Supabase auth errors**: double-check `.env.local` credentials, especially service-role keys for summary/score/export steps.
- **LLM timeouts / 429**: lower `--concurrency`, or set `CONCURRENCY=1` in the environment for the duration of the run.
- **No summaries exported**: confirm `--min-score` threshold and whether `--skip-exported` filtered everything.

## Next steps

- Flesh out the crawler worker with Playwright ingestion.
- Replace the placeholder scoring logic in `src/domain/scoring.py` with the production heuristics.
- Expand unit/integration coverage around adapters once the Supabase test harness is available.

