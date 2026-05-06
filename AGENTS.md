# Edu News Pipeline - Agent Guidelines

IMPORTANT: Read and write this file as UTF-8. In Windows PowerShell, use `Get-Content -Encoding UTF8 AGENTS.md` when checking Chinese text.

本文档是 AI agent 在本项目中工作的全局指南。它负责说明通用命令、代码风格和工程约束；目录级细节请优先阅读对应目录下的局部 `AGENTS.md`。

## 局部 Agent 指南

- 修改 `src/console/` 前，先阅读 `src/console/AGENTS.md`。
- 修改 `src/adapters/` 前，先阅读 `src/adapters/AGENTS.md`。
- 局部 `AGENTS.md` 用来补充该目录的边界、历史包袱和重构护栏；不要把局部规则复制回根目录。

## 构建与开发命令

### 运行应用

```bash
# 启动 Web 控制台（FastAPI + uvicorn，默认端口 8000）
python run_console.py

# 也可以直接使用 uvicorn
uvicorn src.console.app:app --host 0.0.0.0 --port 8000
```

### 流水线命令

```bash
# 运行单个流水线步骤
python -m src.cli.main crawl --sources toutiao,tencent --limit 5000
python -m src.cli.main hash-primary --limit 5000
python -m src.cli.main score --limit 2500
python -m src.cli.main summarize --limit 2500
python -m src.cli.main external-filter --limit 2000
python -m src.cli.main export --min-score 60

# 查看任意命令帮助
python -m src.cli.main [command] --help
```

### 测试命令

```bash
# 运行全部测试
python -m pytest

# 运行单个测试文件
python -m pytest tests/test_score_worker.py

# 运行覆盖率
python -m pytest --cov=src --cov-report=html

# 详细输出
python -m pytest -v

# 运行指定测试类或测试方法
python -m pytest tests/test_score_worker.py::TestScoreWorker::test_scoring_logic -v
```

### 开发环境

```bash
# 安装依赖
pip install -r requirements.txt

# 如果存在 setup.py，可安装为开发模式
pip install -e .

# 开发时使用自动重载
uvicorn src.console.app:app --reload --host 0.0.0.0 --port 8000
```

## 代码风格

### Python 版本与导入顺序

- 使用 Python 3.9+。
- 所有 Python 文件都应包含 `from __future__ import annotations`。
- 导入顺序：
  1. `from __future__ import annotations`
  2. 标准库导入，按字母顺序
  3. 第三方导入，按字母顺序
  4. 本地导入，按字母顺序

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import requests
from fastapi import FastAPI

from src.config import get_settings
from src.domain.models import ArticleInput
```

### 类型标注

- 所有函数参数和返回值都必须有类型标注。
- 优先使用现代类型语法：`list`、`dict`，而不是 `List`、`Dict`。
- 可空类型使用 `Optional[T]`。
- 谨慎使用 `Union`，能具体表达时不要写过宽类型。
- 结构化字典优先使用 `TypedDict`。

```python
def process_articles(articles: list[ArticleInput]) -> list[ProcessedArticle]:
    pass


def fetch_content(url: str, timeout: Optional[float] = None) -> Optional[str]:
    pass
```

### 数据类

- 领域模型使用 `@dataclass(slots=True)`。
- adapter 模型可使用普通 `@dataclass`。
- 所有字段都必须有类型标注。
- 可变默认值使用 `field(default_factory=...)`。

```python
@dataclass(slots=True)
class ArticleInput:
    article_id: Optional[str]
    title: Optional[str]
    source: Optional[str]
    publish_time: Optional[int]
    content: Optional[str]
    raw_payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 命名约定

- 函数：`snake_case`，例如 `process_articles`。
- 类：`PascalCase`，例如 `ArticleInput`。
- 变量：`snake_case`，例如 `article_id`。
- 常量：`ALL_CAPS`，例如 `DEFAULT_LIMIT`。
- 模块：`snake_case`，例如 `score_worker.py`。

### 错误处理

- 使用具体异常类型，不要默认捕获 `Exception`。
- worker 中使用日志工具记录错误。
- 避免 bare `except`。
- 资源管理使用 context manager。

```python
from src.workers import log_error

try:
    result = process_article(article)
except ValueError as exc:
    log_error(f"Invalid article data: {exc}")
    return None
except requests.RequestException as exc:
    log_error(f"Network error fetching article: {exc}")
    raise
```

### 日志

- worker 中使用 `log_info`、`log_error`、`log_summary`。
- 日志应包含足够上下文。
- 进度用 info，失败用 error，收尾统计用 summary。

```python
from src.workers import log_error, log_info, log_summary

log_info(f"Processing {len(articles)} articles")
log_error(f"Failed to process article {article_id}: {error}")
log_summary(f"Completed scoring: {success_count}/{total_count} articles")
```

### 模块结构

- 使用 `__all__` 明确公共 API。
- 相关功能放在同一模块或相邻模块中。
- 模块职责保持聚焦。

```python
__all__ = [
    "ArticleInput",
    "MissingContentTarget",
    "SummaryCandidate",
]
```

## 数据库操作

- 使用 adapter 模式，入口为 `src/adapters/db_postgres_core.py`。
- 需要多个写入保持一致时，明确使用事务。
- SQL 查询使用参数化参数。
- 数据库调试信息应有适当日志。

```python
from src.adapters.db_postgres_core import get_adapter

adapter = get_adapter()
articles = adapter.fetch_primary_articles_for_scoring(limit=100)
```

## API 设计（FastAPI）

- 使用依赖注入处理认证和 middleware。
- 返回结构使用 Pydantic model 或明确的类型。
- endpoint 应有简短 docstring。
- 使用合适的 HTTP 状态码。

```python
from fastapi import APIRouter, Depends

from src.console.security import require_console_user

router = APIRouter()


@router.get("/articles", dependencies=[Depends(require_console_user)])
async def get_articles() -> list[ArticleResponse]:
    """Get list of articles for manual review."""
    pass
```

## 测试

- 使用 pytest。
- 测试文件命名为 `test_*.py`。
- 测试名应描述行为，例如 `test_scoring_with_valid_content`。
- 公共 setup/teardown 使用 fixture。
- 外部依赖必须 mock，包括数据库、HTTP 调用和 LLM API。

```python
from unittest.mock import Mock

import pytest


def test_scoring_with_valid_content():
    scorer = ArticleScorer()

    score = scorer.score("Valid article content")

    assert score >= 0
    assert score <= 100
```

## 配置

- 敏感和可配置内容使用环境变量。
- 通过 `src.config.get_settings()` 读取配置。
- 可选配置应提供合理默认值。

```python
from src.config import get_settings

settings = get_settings()
db_config = {
    "host": settings.db_host,
    "port": settings.db_port,
    "database": settings.db_name,
}
```

## 文件结构

遵循当前项目结构：

- `src/`：主应用代码。
- `src/cli/`：命令行入口。
- `src/console/`：Web 控制台，当前使用 flat modules：
  - `*_routes.py`、`*_service.py`、`*_schemas.py`
  - `web_templates/`、`web_static/`
- `src/workers/`：后台流水线 worker。
- `src/adapters/`：外部系统适配器。
- `src/domain/`：业务规则和领域模型。
- `src/domain/reporting/`：导出格式和分桶逻辑。
- `tests/`：测试文件。
- `scripts/`：工具脚本。
- `docs/`：提示词和流程文档。
- `config/`：配置文件。

## 代码质量标准

- 避免新增 `type: ignore`。如果确实需要，必须限定具体错误码并说明原因，例如 `# type: ignore[attr-defined]`。
- 不要使用 bare `except`。
- 不要保留未使用导入。
- 不要使用可变默认参数。
- 不要硬编码 secret，使用环境变量。
- 函数保持聚焦，目标是不超过 50 行。
- 类保持单一职责。

## 性能

- FastAPI route 中涉及 I/O 时优先使用 async/await。
- 数据库操作尽量批量处理。
- 大数据传输考虑 streaming。
- 对昂贵计算可使用缓存，但先确认瓶颈。
- 优化前先定位性能问题。

## 安全

- 校验所有输入，不信任用户数据。
- 使用参数化查询，避免 SQL 注入。
- 控制台 API 必须通过 `src.console.security` 认证，除非 endpoint 明确公开。
- 安全相关事件应记录日志。
- 依赖变更时注意安全风险。

## 文档策略

- 文档优先记录代码无法表达的信息：业务约束、历史原因、反直觉行为、重构护栏。
- 不要为函数、类、文件做机械摘要。
- 如果只是代码能直接看出的事实，优先改代码命名、结构或测试，而不是写解释性文档。
- Markdown、配置和中文文本文件统一使用 UTF-8 编码。

## Agent 工作规则

### 新增功能

1. 先参考相似模块的现有模式。
2. 新代码必须有类型标注。
3. 为新增行为写测试。
4. 需要时更新文档，优先补充约束和原因，而不是复述代码。
5. 完成前运行相关测试。

### 重构

1. 除非明确要求破坏兼容，否则保持向后兼容。
2. 更新测试以匹配新接口或新边界。
3. 影响范围较大时运行完整测试。
4. 破坏性变化必须在文档或变更说明中写清楚。

### 调试

1. 使用日志追踪执行流。
2. 检查数据库状态，排除数据问题。
3. 构造最小复现。
4. 确认环境变量和配置。

### 外部 API

1. 使用 `src/adapters/` 中的 adapter 模式。
2. 正确处理限流、重试和失败响应。
3. 测试中 mock 外部调用。
4. 调试时保留必要 API 交互日志，但不要记录 secret。

本项目重视类型安全、清晰的职责边界和可测试性。维护时优先考虑正确性和长期可维护性，不要为了短期速度扩大历史包袱。
