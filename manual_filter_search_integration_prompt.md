# Manual Filter + Articles Search Integration Brief

## Current State
- Separate entry points:
  - `/manual_filter` renders `src/console/web_templates/manual_filter.html` and loads `src/console/web_static/js/dashboard.js` + `src/console/web_static/css/dashboard.css`.
  - `/articles/search` renders `src/console/web_templates/search.html`.
  - Landing page `/` links to `/manual_filter`, `/dashboard`, and `/articles/search`.
- Manual filter UI (single page app):
  - Tabs: Filter (pending), Review (selected/backup), Discard.
  - Data comes from `/api/manual_filter/*` endpoints (candidates, review, discarded, stats, edit, decide, export, order).
  - The Review tab has a search box, but it only filters the current list in-memory (title and summary). It does not query the database.
- Articles search page:
  - Server-rendered GET form with params: `q`, `source`, `sentiment`, `status`, `start_date`, `end_date`, `page`, `limit`.
  - Backend path: `articles_service.search_articles` -> `PostgresAdapter.search_news_summaries`.
  - Searches `news_summaries` by title/content/summary plus filters.
  - Results show fields like title, article_id, status, publish time, source, score, sentiment, external status, summary, raw content, and keywords.
- No direct navigation from `/manual_filter` to `/articles/search` (the search page only links to Dashboard).

## Pain Points
- Manual filtering and search are in separate pages, which breaks focus and flow.
- While reviewing candidates, it is hard to check whether related or previously published items already exist.
- The only search inside manual filter is local, limited to the current list on screen.

## Desired Outcome
- Add a search entry point inside the manual filter console.
- Allow "search while filtering" so the user can keep reviewing candidates and check historical articles at the same time.
- Reuse the current search capability (same filters and dataset from `news_summaries`).
- Search results should surface key fields useful for duplication checks (title, date, source, status, summary, link).

## Integration Direction (preferred)
- Add a right-side hidden drawer in `/manual_filter` that overlays the page and can be expanded via a left-arrow toggle (for example "<" or a left-arrow icon).
- Drawer width target: ~40% of the viewport when expanded.
- The drawer hosts the existing search filters and full results layout (reuse `search.html` structure where possible), backed by `/api/articles/search`.
- Result links open in a new tab.
- Mobile: disable the drawer; keep `/articles/search` as the mobile fallback.
- Keep the dedicated `/articles/search` page for full-screen use, but the drawer avoids navigation away during review.
- Consider sharing styling or components between `search.html` and `manual_filter.html` to reduce duplication.
- Reuse the same console authentication/authorization guard as `/articles/search`.
- Default drawer state: collapsed.

## Toggle Placement
- Place the drawer toggle on the right edge of the page, slightly above vertical center.

## Deferred (not in scope yet)
- Auto-fill the search form using the current candidate title or keywords.

## Recommendation (state persistence)
- Persist drawer open/closed state and last search filters in `localStorage` for the current browser.

## Search Interaction Details
- Trigger search via both button click and Enter key.
- Empty-result UI should match the existing `/articles/search` page behavior.

## Drawer Close Behavior
- Close via the drawer close icon (top-right), ESC key, or clicking outside on the overlay mask.
