# Reporting Refactor Plan

Goal: extract shared export formatting and bucketing into `src/core/reporting` and reuse it in:
- `src/console/manual_filter_export.py`
- `src/workers/export_brief.py`

## Scope

- Move only formatting, bucketing, and period calculations.
- Keep DB reads/writes and adapter interactions in existing modules.
- Preserve output text and ordering.

## Proposed structure

- `src/core/reporting/__init__.py`
- `src/core/reporting/buckets.py`
  - Sentiment/region bucketing
  - Sorting and ordering
  - Category counts
- `src/core/reporting/formatters.py`
  - Text block generation
  - Titles/summaries/source suffix
  - Header generation
- `src/core/reporting/periods.py`
  - Period/total calculation with meta state

## Common interfaces

1) Bucketing (core)
```python
def build_buckets(
    candidates: list[ExportCandidate],
    *,
    template: str,
) -> tuple[list[tuple[str, list[ExportCandidate]]], dict[str, int]]:
    """Return ordered bucket list (label, items) and category counts."""
```

2) Formatting (core)
```python
def format_export_text(
    *,
    template: str,
    buckets: list[tuple[str, list[ExportCandidate]]],
    period: int,
    total: int,
    report_date: date,
) -> str:
    """Return the final export text body including headers."""
```

3) Periods (core)
```python
def resolve_periods(
    template: str,
    provided_period: int | None,
    provided_total: int | None,
    *,
    report_type: str,
    meta_state: dict[str, Any],
    today: date | None = None,
) -> tuple[int, int, dict[str, Any], str]:
    """Return period, total, updated meta, today iso string."""
```

## Migration steps

1) Create `src/core/reporting/` with the three modules above and minimal `__init__.py`.
2) Move period calculation from `manual_filter_export.py` into `periods.py`.
3) Move bucketing/sorting logic into `buckets.py`.
4) Move text generation (headers + section texts) into `formatters.py`.
5) Update `manual_filter_export.py` to call core/reporting and keep DB IO only.
6) Update `src/workers/export_brief.py` to reuse the same core/reporting functions.
7) Verify output parity (sample run or compare text).

## Acceptance criteria

- Export output structure and ordering unchanged for both console and worker.
- No new dependencies from core to console/worker/adapters.
- Existing tests pass; add a small unit test for core/reporting if needed.

