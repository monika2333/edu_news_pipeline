# Beijing Internal Positive Scoring Plan

## Goals
- Apply an importance score to Beijing internal positive articles similar to external positives.
- Reuse the external filter worker so operational tooling stays consistent.
- Preserve existing flows for non-positive or external articles.

## Constraints & Considerations
- Beijing gate currently promotes confirmed positives straight to `ready_for_export`; we must queue them for scoring instead.
- External filter prompt and threshold may need tuning for internal vs external; design must support separate configuration.
- Avoid regressions in export ordering: ensure Beijing positives use the new score.
- Provide observability for the new path (logs, raw payload markers).

## Implementation Steps

### 0. Prompt asset
1. Create `docs/internal_importance_prompt.md` containing the dedicated prompt (wrap final text in `<prompt>...</prompt>` like the existing external file for reuse in tooling).
2. Add loader support so the external filter model can pick the internal prompt when evaluating Beijing positives.

### 1. Data flow adjustments
1. Update `PostgresAdapter.complete_beijing_gate`:
   - When `is_beijing_related=True` and underlying sentiment is positive (from candidate), set
     - `status = 'pending_external_filter'`
     - `external_importance_status = 'pending_external_filter'`
   - Ensure `external_filter_fail_count = 0` and `external_filter_attempted_at = NULL` so the worker retries fresh.
   - Preserve existing behaviour for negative cases or rerouted candidates.
   - Include a category marker in `external_importance_raw` (e.g., `{"category": "internal"}`) if prior data exists.
2. Ensure `BeijingGateCandidate` conveys the sentiment label (already present) so the gate logic can confirm positivity before routing.

### 2. External filter worker enhancements
1. In `ExternalFilterCandidate`, confirm fields `sentiment_label` and `is_beijing_related` are available (already present); add helper `candidate_category = 'internal' if candidate.is_beijing_related else 'external'`.
2. Adjust `_score_candidate` (or a wrapper) to accept `category` so we can:
   - Load the internal prompt from `docs/internal_importance_prompt.md` when category is `internal` (fallback to external prompt otherwise).
   - Apply category-specific thresholds (`internal_threshold`, `external_threshold`).
3. Modify `call_external_filter_model` invocation to pass category context:
   - Update prompt builder to take category and optionally add section name or guidelines.
   - Log entries with category prefix (e.g., `INTERNAL OK article_id...`).
4. In `adapter.complete_external_filter`, include category in raw payload for visibility (`"category": category`).

### 3. Configuration updates
1. Extend `Settings`:
   - Add `internal_filter_threshold` (env: `INTERNAL_FILTER_THRESHOLD`, fallback to existing `external_filter_threshold`).
   - Add `internal_filter_prompt_path` to override the default `docs/internal_importance_prompt.md` when needed.
2. Wire new settings into worker functions so thresholds and prompt selection honour environment overrides.
3. Document new environment variables in README and `.env.example` (if present).

### 4. Testing and validation
1. Unit tests:
   - `tests/test_llm_beijing_gate.py`: ensure a positive Beijing article now sets status to `pending_external_filter`.
   - `tests/test_external_filter_worker.py` (create if absent): mock adapter/model to verify category-specific thresholds, prompt selection, and raw payload content.
2. Integration smoke (manual): run `python -m src.workers.external_filter --limit 1` with fixture data to ensure the flow works.
3. Verify export ordering still sorts Beijing positives by `external_importance_score` once populated.

### 5. Documentation and rollout
1. Update README "External Filter" section to mention it covers both Jingnei and Jingwai positives after the change and note the new internal prompt file.
2. Add release notes or migration tips: rerun the external filter worker to backfill existing Beijing positives if needed (provide script or instructions).
3. Communicate configuration changes to the team.

## Open Questions
- Do we need further tuning of the internal prompt content (e.g., domain experts to review)?
- [On hold] Dashboard updates: no immediate action required unless downstream stakeholders request changes.

## Checklist
- [x] 0.1 创建 `docs/internal_importance_prompt.md`，按 `<prompt>...</prompt>` 包裹内容，准备内部提示词
- [x] 0.2 更新加载逻辑，确保北京正向稿件使用新内部提示词
- [x] 1.1 调整 `PostgresAdapter.complete_beijing_gate`，正向北京稿件改入 `pending_external_filter` 并重置相关字段
- [x] 1.2 确认 `BeijingGateCandidate` 暴露情感标签并在网关逻辑中判定正向
- [x] 2.1 在 `ExternalFilterCandidate` 添加 `candidate_category` 辅助属性
- [x] 2.2 更新 `_score_candidate` 及调用链，按 `category` 选择提示词与阈值
- [x] 2.3 调整模型调用与日志，带上 `category` 上下文
- [x] 2.4 在 `adapter.complete_external_filter` 的原始 payload 中记录 `category`
- [x] 3.1 扩展 `Settings` 增加内部阈值和提示词路径配置，并读取环境变量
- [x] 3.2 将新配置接入 worker 使用流程
- [x] 3.3 在 README 与 `.env.local` 记录新环境变量
- [x] 4.1 补充/更新单元测试覆盖北京网关与外部过滤 worker 的分类分支
- [ ] 4.2 手动运行 `python -m src.workers.external_filter --limit 1` 做冒烟验证
- [ ] 4.3 确认导出排序依旧按 `external_importance_score` 工作
- [ ] 5.1 更新 README 外部过滤章节说明并引用内部提示词
- [ ] 5.2 撰写发布/迁移说明，指导如何回填既有北京正向稿件
- [ ] 5.3 通知团队新的配置和流程调整
