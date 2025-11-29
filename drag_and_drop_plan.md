# Drag and Drop Sorting Implementation Plan

This feature allows users to manually reorder news items in the "Review" (审阅) tab. The order determines the sequence in the exported report.

## Feasibility Assessment
**Is it easy?** Yes.
-   **Frontend**: Using a library like **SortableJS** makes enabling drag-and-drop very simple (a few lines of code).
-   **Backend**: Requires adding a field to store the order (e.g., `manual_rank`) and updating the export logic to respect this order.

## Implementation Steps

### 1. Frontend Implementation (SortableJS)

#### [MODIFY] `src/console/web/templates/manual_filter.html`
-   Add SortableJS library (via CDN or local static file).
    ```html
    <script src="https://cdn.jsdelivr.net/npm/sortablejs@latest/Sortable.min.js"></script>
    ```

#### [MODIFY] `src/console/web/static/js/dashboard.js`
-   Initialize Sortable on the "Selected" and "Backup" lists in the Review tab.
-   **Feature**: Allow dragging between "Selected" and "Backup" lists to automatically change status.
    ```javascript
    function initSortable() {
        const selectedList = document.querySelector('#review-list .review-col:first-child'); // Adjust selector
        const backupList = document.querySelector('#review-list .review-col:last-child'); // Adjust selector

        new Sortable(selectedList, {
            group: 'shared', // Allow dragging between lists
            animation: 150,
            onEnd: function (evt) {
                // Logic to handle drop:
                // 1. Detect if item moved to a new list (Status change).
                // 2. Detect new order.
                // 3. Call backend to save new order/status.
            }
        });
        
        new Sortable(backupList, {
            group: 'shared',
            animation: 150,
            // ...
        });
    }
    ```

### 2. Backend Implementation (Persistence)

To ensure the order is saved and used for export, we need to store it in the database.

#### [SQL] Database Schema Update
-   Add a `manual_rank` column to `news_summaries`.
    ```sql
    ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_rank DOUBLE PRECISION;
    ```
    *Using `DOUBLE PRECISION` allows inserting items between others by averaging ranks without re-indexing everything.*

#### [MODIFY] `src/console/routes/manual_filter.py` & `services/manual_filter.py`
-   **Update `bulk_decide` or create `update_rank` endpoint**:
    -   Accept a list of `{article_id, rank}` or an ordered list of IDs.
    -   Update the `manual_rank` in the database.
-   **Update `export_batch`**:
    -   Change `ORDER BY` clause to prioritize `manual_rank`.
    -   Current: `ORDER BY score DESC...`
    -   New: `ORDER BY manual_rank ASC NULLS LAST, score DESC...`

### 3. Quick Alternative (No DB Schema Change)
If you only care about the order *at the moment of export* and don't need it to persist across page reloads:
-   **Frontend**: Collect the list of IDs in the current DOM order when clicking "Export".
-   **Backend**: Update `export_batch` to accept an optional `ordered_ids: List[str]` argument.
-   **Logic**: If `ordered_ids` is provided, use it to sort the results.

## Recommendation
**Go with the Full Persistence (Step 2)**. It provides a better user experience (you can save your work and come back) and fits better with the "Save" button workflow.
