# 注意（后续清理提醒）
- 已移除代码中的“config/ 优先、data/ 回退”兼容逻辑，并清理 `.gitignore` 中与 `data/*` 相关的过渡性忽略项；文档也已更新为只描述 `config/` 默认路径。

# Config Directory Migration Plan

## Goal
- Rename/standardize the repository’s configuration assets from `data/` to `config/`.
- Keep backward compatibility so external scripts or env files that still point to `data/` continue to work during the transition.

## Scope
- Files to relocate to `config/`:
  - `education_keywords.txt`
  - `beijing_keywords.txt`
  - `score_keyword_bonuses.json`
  - `toutiao_author.txt`

## Backward Compatibility Strategy
- Prefer `config/` defaults. If a default `config/*` file does not exist and no env override is provided, fall back to the legacy `data/*` path.
- All existing env overrides remain valid:
  - `KEYWORDS_PATH`, `BEIJING_KEYWORDS_PATH`, `SCORE_KEYWORD_BONUSES_PATH`, `SCORE_KEYWORD_BONUSES`, `TOUTIAO_AUTHORS_PATH`.

## Steps
1) Code: add fallback logic and switch defaults to `config/`
   - `src/config.py`
     - Default `keywords_path` -> `config/education_keywords.txt` with fallback to `data/education_keywords.txt`.
     - Default `beijing_keywords_path` -> `config/beijing_keywords.txt` with fallback.
     - Default `SCORE_KEYWORD_BONUSES_PATH` -> `config/score_keyword_bonuses.json` with fallback.
   - `src/workers/crawl_sources.py`
     - Prefer `config/toutiao_author.txt` (via `_resolve_authors_path`), fallback to `data/toutiao_author.txt`.
2) Docs: update references from `data/` to `config/` where they refer to configuration inputs.
   - `README.md` sections describing keyword paths, bonus rules, and Toutiao authors.
   - Keep notes that env vars can override the defaults.
3) Repo hygiene
   - `.gitignore`: add `config/toutiao_author.txt`, `config/summarized_cursor.json` (keep legacy `data/*` ignores temporarily).
4) Move files
   - Create `config/` if absent, then move:
     - `data/education_keywords.txt` -> `config/education_keywords.txt`
     - `data/beijing_keywords.txt` -> `config/beijing_keywords.txt`
     - `data/score_keyword_bonuses.json` -> `config/score_keyword_bonuses.json`
     - `data/toutiao_author.txt` -> `config/toutiao_author.txt`
5) Validation
   - Repo-wide search to confirm no hard-coded `data/` references remain in code (docs will still mention `config/`).
   - Run a quick import/parse check for each path to ensure files load from `config/`.

## Rollback Plan
- If issues arise, revert to prior commit. Fallback logic ensures older `data/` files still work during transition anyway.

## Notes
- After a grace period, consider removing the fallback and any legacy `data/*` ignores in `.gitignore`.
