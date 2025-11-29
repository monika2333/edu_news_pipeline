# Sort View (Compact Mode) Design

To improve the drag-and-drop experience, we will implement a "Sort View" that collapses article cards to show only essential information (Titles), making it easier to see and reorder multiple items at once.

## 1. UI/UX Design

### Toolbar Update
-   Add a **"Sort Mode" (排序模式)** toggle button to the Review Tab toolbar (next to "Export").
-   **State**:
    -   **Default**: "Detail View" (Full cards with summaries).
    -   **Active**: "Sort View" (Compact cards, titles only).

### Visual Changes (Sort View)
When "Sort Mode" is active:
1.  **Hide**: Summaries (`.summary-box`), Metadata (`.meta-row`), and Status Dropdowns (optional, or keep small).
2.  **Show**: Title, Drag Handle (icon), and a simplified Status indicator.
3.  **Layout**: Cards become thin rows (e.g., 40px height), allowing 10-20 items to fit on screen.

## 2. Implementation Steps

### Step 1: HTML Updates (`manual_filter.html`)
Add the toggle button and a drag handle icon to the card template.

```html
<!-- In the Toolbar -->
<button class="btn btn-secondary" id="btn-toggle-sort">
    <span class="icon">🔃</span> 排序模式
</button>

<!-- In the Article Card Template (JS) -->
<div class="article-card">
    <div class="drag-handle">⋮⋮</div> <!-- New Handle -->
    <div class="card-header">
        ...
    </div>
    ...
</div>
```

### Step 2: CSS Updates (`dashboard.css`)
Define the `.compact-mode` styles. This class will be toggled on the `#review-list` container.

```css
/* Compact Mode Styles */
.review-grid.compact-mode .article-card {
    padding: 8px 16px;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
}

.review-grid.compact-mode .summary-box,
.review-grid.compact-mode .meta-row {
    display: none; /* Hide details */
}

.review-grid.compact-mode .card-header {
    margin-bottom: 0;
    flex: 1;
    display: flex;
    align-items: center;
}

.review-grid.compact-mode .article-title {
    font-size: 0.95rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* Drag Handle */
.drag-handle {
    cursor: grab;
    color: var(--text-secondary);
    font-size: 1.2rem;
    display: none; /* Hidden by default */
}

.review-grid.compact-mode .drag-handle {
    display: block; /* Show in compact mode */
}
```

### Step 3: JavaScript Logic (`dashboard.js`)
Implement the toggle logic.

```javascript
// State
let isSortMode = false;

// Event Listener
document.getElementById('btn-toggle-sort').addEventListener('click', () => {
    isSortMode = !isSortMode;
    const container = document.querySelector('.review-grid');
    const btn = document.getElementById('btn-toggle-sort');
    
    if (isSortMode) {
        container.classList.add('compact-mode');
        btn.classList.add('active'); // Style as pressed
        btn.textContent = '退出排序';
    } else {
        container.classList.remove('compact-mode');
        btn.classList.remove('active');
        btn.textContent = '排序模式';
    }
});
```

## 3. Benefits
-   **Efficiency**: Users can see the overall structure of the list.
-   **Usability**: Dragging long distances is much easier without scrolling past huge text boxes.
-   **Focus**: Removes distraction when the user's goal is purely ordering.
