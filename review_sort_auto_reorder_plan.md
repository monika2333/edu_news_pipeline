# Review Tab Auto-Reorder Plan

## Goal
Add a one-click "auto reorder" action that is available only in Review tab sort mode. The reorder logic mirrors the category rules defined in this plan (category-based ordering: "市委教委", "中小学", "高校", "其他"). `reorder_brief.py` is temporary reference only and should be removed after implementation.

## Scope
- Review tab only.
- Sort mode only.
- Operates on the current view (`selected` or `backup`) and current report type (`zongbao` or `wanbao`).
- Reuses existing order persistence via the manual filter order API.

## Non-Goals
- No changes to backend ordering logic.
- No changes to non-review tabs.
- No automatic reorder outside of explicit user action.

## Files to Touch
- `src/console/web_templates/manual_filter.html` (add the button to the toolbar)
- `src/console/web_static/js/manual_filter/review_tab.js` (auto reorder logic + wiring)
- `src/console/web_static/js/manual_filter/init.js` (bind button)
- `src/console/web_static/css/modules/review.css` (show/hide button in sort mode)
- `reorder_brief.py` (remove after implementation)

## Reorder Logic (JS rules defined in this plan)
1. Define the category rules and order exactly as below (JS is the single source of truth):
   - `市委教委`: ["市委", "市委教育工委", "市教委", "教工委", "教育工委", "教育委员会", "首都教育两委", "教育两委"]
   - `中小学`: ["中小学", "小学", "初中", "高中", "义务教育", "基础教育", "幼儿园", "幼儿", "托育", "K12", "班主任", "青少年", "少儿", "少年"]
   - `高校`: ["高校", "大学", "学院", "本科", "研究生", "硕士", "博士"]
   - default: `其他`
2. Build a `classifyCategory(text)` helper:
   - Lowercase comparison.
   - `text` is a join of title + summary + llm_source_display from `state.reviewData` (not DOM) so it behaves like the brief entries.
   - Use `item.title`, `item.summary`, `item.llm_source_display` (no fallback logic).
3. Sort strategy:
   - For the current review list (`state.reviewData[state.reviewView]`, already scoped by `loadReviewData()` to the active `report_type`), build buckets by category in the order above (UI stays unchanged).
   - Preserve original relative order within each category (stable).
   - Apply within each review group (internal/external + sentiment) so the group structure remains consistent with existing UI.

## UI/UX
- Add a button in the Review toolbar: "自动排序".
- Visible and enabled only when `isSortMode === true`.
- When clicked:
  - Reorder items in-memory (`state.reviewData[view]`).
  - Re-render the review list.
  - Persist order with existing `persistReviewOrder()`.
  - Show a toast with counts per category (optional but helpful).
- If sort mode is off, show a gentle toast and do nothing.

## Implementation Steps
1. HTML:
   - Add a new button in `manual_filter.html` near `#btn-toggle-sort` with id `btn-auto-reorder` and class `sort-only`.
2. CSS:
   - Add a class to hide the button by default (e.g. `.sort-only { display: none; }`).
   - Toggle visibility via a parent class set in `applySortModeState` on `#review-tab` (e.g. `#review-tab.review-sort-mode .sort-only { display: inline-flex; }`).
3. JS - core logic in `review_tab.js`:
   - Define `CATEGORY_RULES` + `CATEGORY_ORDER` in JS as the single source of truth.
   - Treat JS as the single source of truth; remove `reorder_brief.py` after implementation.
   - Add `classifyCategory(text)` and `autoReorderReviewItems()` functions.
   - `autoReorderReviewItems()`:
     - Guard: `state.currentTab === 'review'` and `isSortMode === true`.
     - For current view, group items by `resolveGroupKey(item)`.
     - Within each group, reorder by category order (stable).
     - Update `state.reviewData[view]` with the new order.
     - Call `renderReviewView()` and `persistReviewOrder()`.
4. JS - wiring in `init.js`:
   - Bind `#btn-auto-reorder` click to `autoReorderReviewItems`.
5. JS - view state in `applySortModeState`:
   - Toggle a parent class on `#review-tab` to show/hide the button.

## Testing Checklist
- Enter review tab and toggle sort mode on; verify "自动排序" appears.
- Click "自动排序" with mixed categories; verify the order follows category order.
- Toggle sort mode off; ensure the button hides and does not run.
- Verify manual dragging still works after auto reorder.
- Ensure persisted order is saved by refreshing and confirming order.
