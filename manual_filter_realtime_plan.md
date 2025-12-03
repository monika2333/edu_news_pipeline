# Manual Filter Realtime Decisions Plan

## Goals
- Make per-article decisions (采纳/备选/放弃) apply immediately without a full page reload and remove decided cards from the DOM.
- Keep untouched articles in the Pending view until explicitly acted on.
- Replace the bulk action button copy/behavior with “放弃本页剩余内容”, discarding all still-visible items.

## Current Behavior (noted)
- Decisions are gathered via “提交当前页选择”, saved through `/edit` then `/decide`, followed by `loadFilterData()` to reload the page.
- Status radio defaults to 放弃; no per-item auto-submit. Cluster-level radios exist.
- DOM removal only happens via reload.

## Plan
1. **Per-item immediate update**
   - Add delegated listeners for status changes (single cards and cluster-level radios). On change, collect the affected card IDs plus current summary/source edits, POST to `/edit`, then POST to `/decide` with the chosen status.
   - On success, remove the relevant card(s) from the DOM; if a cluster becomes empty, remove its wrapper. Update counters via `loadStats()` (no full list reload).
   - On failure, show toast and revert the radio selection to prior state.
2. **Pending retention**
   - Only remove cards that received an explicit status change; untouched cards stay rendered. Avoid automatic refresh that would drop Pending items.
3. **“放弃本页剩余内容” bulk**
   - Rename the button text.
   - Bulk handler: gather all currently visible undecided cards (or all remaining), send their summaries/sources to `/edit`, then call `/decide` with those IDs marked discarded, remove them from DOM, refresh stats.

## Edge Handling
- Clusters: a cluster-level radio change should apply to all cards inside that cluster in one request.
- Collapsed cards: ensure hidden cards in a cluster are included in bulk and cluster decisions.
- Data capture: make sure summary/source values are sent before removal to avoid losing edits.

## Testing Outline
- Manually change status on single and clustered items; confirm immediate removal, correct toast, stats update, no page reload.
- Leave items untouched; verify they persist.
- Use “放弃本页剩余内容”; all remaining cards disappear and are marked discarded server-side.
- Error path: simulate failed request (e.g., offline) to confirm selection reverts and cards stay. 
