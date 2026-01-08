# Manual Filter JS Split Plan (Option 1)

## Goal
Split `src/console/web_static/js/dashboard.js` into smaller files without introducing a build step or ES modules. Preserve current behavior and loading model.

## Scope
- Only the manual filter dashboard frontend JS.
- No changes to backend behavior or API contracts.
- Keep global `state`, `elements`, and helper functions accessible via the window scope, but namespace them under `window.manualFilter` to reduce globals.
- Move inline `onclick` handlers to event delegation (reduce window globals).

## Proposed File Layout
Create a folder and split into these files:
- `src/console/web_static/js/manual_filter/core.js`
  - constants, state, flags, elements cache
- `src/console/web_static/js/manual_filter/utils.js`
  - shared helpers: toast, sentiment class, pagination, small DOM helpers
- `src/console/web_static/js/manual_filter/filter_tab.js`
  - filter tab load/render, clustering handlers, persist edits, bulk discard
- `src/console/web_static/js/manual_filter/review_tab.js`
  - review tab load/render, edit handlers, selection/bulk actions, sort mode
- `src/console/web_static/js/manual_filter/discard_tab.js`
  - discard list load/render/restore
- `src/console/web_static/js/manual_filter/export_modal.js`
  - export modal handling
- `src/console/web_static/js/manual_filter/search_drawer.js`
  - search drawer state, filters, rendering
- `src/console/web_static/js/manual_filter/init.js`
  - DOMContentLoaded setup, event bindings, initial data loads

## Script Load Order
Update `src/console/web_templates/manual_filter.html` to include scripts in this order:
1) `core.js`
2) `utils.js`
3) `filter_tab.js`
4) `review_tab.js`
5) `discard_tab.js`
6) `export_modal.js`
7) `search_drawer.js`
8) `init.js`
9) Replace the Sortable CDN script with a local static file (load it before the files above) and add a simple timestamp version query (e.g. `?v=20250110153000`).
10) Add the same timestamp version query to all manual filter scripts to avoid mixed-cache issues.

## Execution Steps
1) Confirm no other templates reference `dashboard.js` (`rg -n "dashboard\\.js" src`).
2) Create the `manual_filter/` directory and add empty files with headers.
3) Move constants/state/elements/flags into `core.js`.
4) Move general helpers into `utils.js`.
5) Move tab-specific logic into their files (filter/review/discard).
6) Move export modal logic into `export_modal.js`.
7) Move search drawer logic into `search_drawer.js`.
8) Move DOMContentLoaded setup into `init.js`.
9) Replace inline `onclick` handlers with delegated events in JS (by container, not `document`).
   - `changePage(...)` pagination buttons (filter/discard)
   - `restoreToBackup(...)` discard restore button
   - `changeSearchPage(...)` search drawer pagination
   - Update render functions to add `data-*` attributes for delegation targets.
10) Vendor Sortable.js into `src/console/web_static/vendor/` from official site/GitHub: store `Sortable.min.js` and `LICENSE` (version 1.15.6).
    - Source: https://github.com/SortableJS/Sortable/releases/tag/1.15.6
11) Update `manual_filter.html` to load local Sortable + new scripts with a timestamp version query (e.g. `?v=20250110153000`).
12) Remove `dashboard.js` once validated (no shim).

## Event Delegation Map
| Container | Event | Target selector | Notes |
| --- | --- | --- | --- |
| `#filter-pagination` | `click` | `button[data-page]` | replaces `changePage(...)` for filter tab |
| `#discard-pagination` | `click` | `button[data-page]` | replaces `changePage(...)` for discard tab |
| `#discard-list` | `click` | `button[data-action="restore"]` | replaces `restoreToBackup(...)` |
| `#search-pagination` | `click` | `button[data-page]` | replaces `changeSearchPage(...)` |
| `document` | `DOMContentLoaded` | N/A | `elements` cache is built after DOM is ready |

## Validation Checklist
- Filter tab loads, edits save, status changes persist.
- Review tab edits persist and re-render correctly.
- Sorting mode works and order saves.
- Discard tab lists and restore works.
- Export modal preview/confirm works.
- Search drawer works with filters/pagination.
- Review tab: edit then re-click same category button, content stays updated.
- Pagination buttons (filter/discard/search) still work after event delegation.
- Search drawer: Enter key still triggers search.

## Risks & Mitigations
- Risk: missing globals after split.
  - Mitigation: keep shared variables under `window.manualFilter` and load scripts in strict order.
- Risk: inline handlers broken after split.
  - Mitigation: replace inline `onclick` with delegated events in JS.
- Risk: duplicate function names after split.
  - Mitigation: keep only one definition and verify no overlap.
- Risk: vendored Sortable version not tracked or license omitted.
  - Mitigation: keep `Sortable.min.js` and `LICENSE` in `src/console/web_static/vendor/` and note the version in commit notes.

## Rollback
Revert `manual_filter.html` to load the original `dashboard.js` only and restore the file content from VCS.
