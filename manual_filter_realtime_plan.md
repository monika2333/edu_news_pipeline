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
1. **Per-item immediate update** ✅
   - Added delegated radio listeners to single cards and clusters that POST edits then decisions, remove the affected cards/clusters from the DOM, refresh stats, and revert selection with a toast on failure.
2. **Pending retention** ✅
   - Immediate handlers only remove the cards/clusters whose status changed; untouched items stay on the page with no automatic reload.
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
