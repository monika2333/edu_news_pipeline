# 协作约定与操作规程

本文件是「在这仓库里怎么干活」的操作手册：协作方式、测试、worktree、变异测试、合并推送。全部来自实际踩坑后的沉淀，按流程照做可以绕开绝大部分环境坑。

## 1. 协作方式：委托书式任务

用户以结构化「委托书」分派功能任务，固定包含：背景、目标语义、**按文件的允许改动/禁止改动清单**、行为测试验收标准、逐条编号的变异测试（M1…Mn）、交付说明必含章节（设计取舍与理由、版本决策、API 变化、变异逐条结果、全量 pytest 结果）。

- **文件范围清单是硬约束**（超出即违规）。共享 helper 没有落位文件时，宁可两处各写小函数也不要越界改文件。
- 变异测试用来验证测试有效性，不是可选环节：每条变异必须至少让一个测试失败。变异暴露测试盲区时，先补测试再复测变异。
- 顺序类变异注意：dict 相等断言忽略键序，必须 `list(d.items())` 有序断言才能捕获「读取时顺序被打乱」类变异。
- 轻量需求（一行 ORDER BY 之类）用户会明说不用走全流程；拿不准就问。

## 2. 测试怎么跑

- **必须用仓库根 venv**：Git Bash 下 `./venv/Scripts/python.exe -m pytest`。系统默认 python（3.14）缺 psycopg 等依赖，裸 `python -m pytest` 会在收集阶段 ModuleNotFoundError。worktree 里要用主仓库 venv 的绝对路径。
- DB 行为测试**直连真实 Postgres**（`get_settings()` 的 db_* 配置），无 embedded/testcontainers。模式：给涉及的每张表建 `CREATE TEMP TABLE {t} (LIKE public.{t} INCLUDING DEFAULTS) ON COMMIT DROP` 再 seed，几分钟跑完。新写 DB 行为测试照抄 `tests/test_article_attribution_search.py` 这类现成模式。
- 新增 UPDATE/写路径 SQL 时**必须配真实 DB 测试**：fake-cursor 测试抓不到占位符顺序类 bug（实际发生过：`SET ... = %s` 参数排在 WHERE 参数之后导致运行错位）。
- jsdom 测试（`tests/js/`，pytest 包装在 `test_*_js_behavior.py`）需要 Node 20+ 和 `npm ci --prefix tests/js`。**全量报告出现 "3 skipped" = jsdom 依赖又没了**（3 个 jsdom 测试文件静默跳过；CI 缺依赖会直接失败）。
- 变异测试时直接跑 node 套件更快：用主仓 venv 执行 `from tests.test_settings_js_behavior import _render_settings_page` 渲染出临时 HTML，然后 `cd tests/js && CONSOLE_STATIC_DIR=<静态目录> SETTINGS_PAGE_HTML=<临时HTML> node --test --test-reporter=spec xxx.test.js`（两个环境变量缺一不可，名字是 CONSOLE_STATIC_DIR）。
- psql 18 已在用户 PATH；但 ZCode 的 Bash 是非交互 shell，重启前可能要 `export PATH="/c/Program Files/PostgreSQL/18/bin:$PATH"`。`test_migration_replay` 报错先看是 psql 缺失还是 CRLF，再下结论。
- 真实库测试无库可连时应 skip（connect_timeout=5），不要硬炸。

## 3. worktree 操作规程

日常任务在 `.worktrees/<name>` 里做（脚本 `wt.mjs`，参数走 **stdin JSON**，不是 argv；路径一律**正斜杠 + 仓库相对路径**——echo 管道里 Windows 反斜杠会被 Git Bash 折叠导致 JSON.parse 失败）。

新建 worktree 后的三件套：
1. **复制 `.env.local`**（DB 密码），否则所有连 DB 的测试 `fe_sendauth: no password supplied`。
2. `tests/js/node_modules` 建目录联接复用主仓：`powershell -NoProfile -Command "New-Item -ItemType Junction -Path '<worktree绝对路径>\tests\js\node_modules' -Target '<主仓库绝对路径>\tests\js\node_modules'"`（或 `cmd //c mklink /J`，联接名用相对名 `node_modules`、目标用绝对路径；失败就退回 PowerShell 方案）。主仓 node_modules 可能被并行会话清空，保险起见 worktree 里直接 `npm ci --prefix tests/js`。
3. `git config core.autocrlf false && git rm --cached -r . && git reset --hard` 规范化回 LF，否则 `test_migration_replay` 因 schema.sql 函数体 CRLF/LN 差异失败。

**拆 worktree 前必须先用 `cmd /c rmdir "tests\js\node_modules"` 摘掉联接**——`git worktree remove` 会穿透 junction 把主仓 node_modules 内容删光（2026-10-07 实际发生，靠 `npm ci --prefix tests/js` 恢复）。worktree 根的自建未跟踪临时脚本会挡住 remove（「工作区有未提交改动」），用完先 `rm`；截图 PNG 等产物目录也要先删。`wt.mjs exit` 报「没有活动 worktree」属正常，跳过直接合并。

## 4. 合并与推送

收尾顺序：worktree 内全量 pytest → `wt.mjs exit` → 主仓库合并 → 主仓库全量 pytest → `wt.mjs remove`（`{"path":"...","confirm_remove":true,"delete_branch":true}`，未跟踪的 .env.local/node_modules 会被一并删）。

- **能快进就快进（用户 2026-10-08 明确要求）**：main 未被并行推进时一律 `git merge --ff-only`，不产生 "Merge branch ..." 提交（单点小改动套 merge 节点，用户嫌历史噪音大）。合并前判断分叉：`git log --oneline main..<branch>` 与 `<branch>..main`，后者为空即快进场景。只有真分叉时才 `--no-ff`，并在交付说明里说明原因。
- 合并后必须跑**主仓库全量 pytest** 验证语义合并，不能只看「Auto-merging 无冲突」（实际发生过与并行会话的提交都改了 `business_config.py` + `env_reference.md`，自动融合后靠全量确认）。
- **推送 main 被 worktree-guard 拦截**，按提示三步**独立命令**执行：① `wt.mjs authorize-main`（带 reason 的 JSON 管道）；② 单独 `git push`；③ `wt.mjs revoke-main`。三条写进同一条复合命令会被 PreToolUse hook 整体拦截。
- 本地分支删除要先删远端分支，否则 remove 的 delete_branch 会以「未合并到 upstream」拒绝（`git branch -D` 兜底）。

## 5. 变异测试规程

- **先 commit 功能改动再跑变异**。`git checkout -- <file>` 会把未提交的实现/微调一并清掉（实际发生多次，被迫重打+全量复跑自证）；回滚一律 `cp file file.bak` + `mv` 还原。
- **脚本读写一律 bytes**：`Path.write_text()` 在 Windows 会把 LF 全量翻译成 CRLF，还原动作同样走 write_text 时连还原都变成全文件改写。修法：`read_bytes` + `replace(b"\r\n", b"\n")` + `write_bytes`；靠 `git diff --numstat` 增删行数异常（如 861/599）能发现这种假 diff。
- 跑 pytest 前设 `PYTHONDONTWRITEBYTECODE=1`（或清被变异文件的 `__pycache__`）：等长替换 + git checkout 还原后 mtime/size 可能仍匹配旧缓存，pytest 会读到变异版 pyc，症状是「还原后状态也不对」。
- **含反斜杠的变异目标串不要经 Bash 传**（Bash 工具会把 `\\` 折半），写 Python 驱动脚本（heredoc 内用 `chr(92)` 构造反斜杠）。
- **Bash 大块 heredoc 会被无声截断**（实测两次，甚至吞掉后续命令）——大段内容一律 Write 工具写临时文件再拼接。
- 纯 CSS 层叠等 pytest/jsdom 覆盖不到的改动，用浏览器夹具验证法：真实 CSS + 真实 DOM 结构静态夹具 → `python -m http.server` → 浏览器打开（**不能开 file: URL，必须 http**）→ `document.elementFromPoint` 对目标元素 20%/50%/75%/90% 高度多点命中检测（中心点可能恰好落在覆盖区外）；变异打在**拷贝件**上，生产文件不动。见 archive/2026-10-08-账号菜单z-index修复.md。

## 6. 提交卫生

- **Edit 工具会把被编辑文件整个写回成 CRLF**（仓库标准 LF），`git add -u` 会把全仓库行尾改写吞进提交（实际发生过 amend 卷进 443 个文件）。规矩：`git add` 只加显式路径；Edit 过的 src 文件提交前 `sed -i 's/\r$//'` 转回 LF；提交后 `git show --stat` 核对文件数与增删行数符合预期。
- Python 脚本改文档同理：`write_text` 文本模式产生 775 行假 diff 的事故发生过，行尾敏感文件用 `write_bytes`。
- ZCode 的 Bash 在多次调用间 cwd 可能重置，跨仓库操作每条命令显式 `cd` 绝对路径。
- `grep` 找「外部调用方」的两个坑：`head -N` 截断输出会漏看后面的匹配就下结论；Git Bash 的 grep 输出反斜杠路径，`grep -v "src/adapters/"` 过滤不掉。正确姿势：先按目录圈定 `grep -rn ... 目录清单`，不截断。

## 7. 杂项环境坑

- 浏览器技能打不开 `file:` URL，一律 `python -m http.server` 托管（静态夹具 `translate_path` 要剥 `?v=` query 否则 404）；浏览器按 URL 缓存，改 CSS 后要换 `?v=` 戳。
- 无登录凭据时渲染页面截图：TestClient 依赖覆盖渲染 HTML + `/static/` 重写为同源 + http.server 托管快照目录，改 viewport 截图（吸顶头部页面禁用 fullPage，会拼接伪影）。
- 登录页 Playwright 点击提交按钮会超时，用 `form.requestSubmit()`（evaluate）替代。
- 与并行会话（其他 worktree 任务）合流时可能撞名：先查 main 是否已有同功能文件，取一方并把己方用例移植过去，勿维护两套。
