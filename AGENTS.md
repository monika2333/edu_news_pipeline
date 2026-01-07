# Edu News Pipeline - Agent Guidelines

This document provides comprehensive guidelines for AI agents working on the Edu News Pipeline codebase. Follow these rules to maintain consistency and quality.

## Build & Development Commands

### Running the Application
```bash
# Start the web console (FastAPI + uvicorn, default port 8000)
python run_console.py

# Alternative: Direct uvicorn command
uvicorn src.console.app:app --host 0.0.0.0 --port 8000
```

### Pipeline Commands
```bash
# Run individual pipeline steps
python -m src.cli.main crawl --sources toutiao,tencent --limit 5000
python -m src.cli.main hash-primary --limit 5000
python -m src.cli.main score --limit 2500
python -m src.cli.main summarize --limit 2500
python -m src.cli.main external-filter --limit 2000
python -m src.cli.main export --min-score 60

# Get help for any command
python -m src.cli.main [command] --help
```

### Testing Commands
```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/test_score_worker.py

# Run tests with coverage
python -m pytest --cov=src --cov-report=html

# Run tests in verbose mode
python -m pytest -v

# Run specific test class/method
python -m pytest tests/test_score_worker.py::TestScoreWorker::test_scoring_logic -v
```

### Development Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Install in development mode (if setup.py exists)
pip install -e .

# Run with auto-reload (development)
uvicorn src.console.app:app --reload --host 0.0.0.0 --port 8000
```

## Code Style Guidelines

### Python Version & Imports
- **Python 3.9+** with `from __future__ import annotations` in ALL files
- **Import organization** (strict order):
  1. `from __future__ import annotations`
  2. Standard library imports (alphabetically sorted)
  3. Third-party imports (alphabetically sorted)
  4. Local imports (alphabetically sorted)

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI

from src.config import get_settings
from src.domain.models import ArticleInput
```

### Type Hints
- **Mandatory** for all function parameters and return values
- Use modern typing syntax (`list` instead of `List`, `dict` instead of `Dict`)
- Use `Optional[T]` for nullable types
- Use `Union` sparingly, prefer specific types
- Use `TypedDict` for structured dictionaries

```python
def process_articles(articles: list[ArticleInput]) -> list[ProcessedArticle]:
    pass

def fetch_content(url: str, timeout: Optional[float] = None) -> Optional[str]:
    pass
```

### Data Classes
- **Domain models** use `@dataclass(slots=True)` for memory efficiency
- **Adapter models** use standard `@dataclass`
- Include type hints for all fields
- Use `field(default_factory=list)` for mutable defaults

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

### Naming Conventions
- **Functions**: `snake_case` (e.g., `process_articles`, `fetch_content`)
- **Classes**: `PascalCase` (e.g., `ArticleInput`, `ScoreWorker`)
- **Variables**: `snake_case` (e.g., `article_id`, `processed_count`)
- **Constants**: `ALL_CAPS` with underscores (e.g., `DEFAULT_LIMIT`, `SCORE_THRESHOLD`)
- **Modules**: `snake_case` (e.g., `article_input.py`, `score_worker.py`)

### Error Handling
- **Use specific exceptions** instead of generic `Exception`
- **Log errors** using the worker logging utilities
- **Avoid bare except clauses** - catch specific exceptions
- **Use context managers** for resource management

```python
from src.workers import log_error

try:
    result = process_article(article)
except ValueError as e:
    log_error(f"Invalid article data: {e}")
    return None
except requests.RequestException as e:
    log_error(f"Network error fetching article: {e}")
    raise
```

### Logging
- **Use worker logging utilities**: `log_info`, `log_error`, `log_summary`
- **Include context** in log messages
- **Log at appropriate levels** (info for progress, error for failures)

```python
from src.workers import log_info, log_error, log_summary

log_info(f"Processing {len(articles)} articles")
log_error(f"Failed to process article {article_id}: {error}")
log_summary(f"Completed scoring: {success_count}/{total_count} articles")
```

### Module Structure
- **Use `__all__`** to explicitly define public API
- **Group related functionality** in modules
- **Keep modules focused** on single responsibility

```python
__all__ = [
    "ArticleInput",
    "MissingContentTarget",
    "SummaryCandidate",
]
```

### Database Operations
- **Use the adapter pattern** (`src/adapters/db.py`)
- **Handle transactions appropriately**
- **Use typed parameters** for SQL queries
- **Log database operations** for debugging

```python
from src.adapters.db import get_adapter

adapter = get_adapter()
articles = adapter.fetch_primary_articles_for_scoring(limit=100)
```

### API Design (FastAPI)
- **Use dependency injection** for authentication/middleware
- **Return typed responses** with Pydantic models
- **Document endpoints** with docstrings
- **Use appropriate HTTP status codes**

```python
from fastapi import APIRouter, Depends
from src.console.security import require_console_user

router = APIRouter()

@router.get("/articles", dependencies=[Depends(require_console_user)])
async def get_articles() -> list[ArticleResponse]:
    """Get list of articles for manual review."""
    pass
```

### Testing
- **Use pytest** as the testing framework
- **Name test files** as `test_*.py`
- **Use descriptive test names** (e.g., `test_scoring_with_valid_content`)
- **Use fixtures** for common setup/teardown
- **Mock external dependencies** (database, HTTP calls, LLM APIs)

```python
import pytest
from unittest.mock import Mock

def test_scoring_with_valid_content():
    # Arrange
    scorer = ArticleScorer()

    # Act
    score = scorer.score("Valid article content")

    # Assert
    assert score >= 0
    assert score <= 100
```

### Configuration
- **Use environment variables** for sensitive/configurable values
- **Access config** through `src.config.get_settings()`
- **Provide sensible defaults** for optional settings

```python
from src.config import get_settings

settings = get_settings()
db_config = {
    "host": settings.db_host,
    "port": settings.db_port,
    "database": settings.db_name,
}
```

### File Structure
- **Follow existing patterns**:
  - `src/` - Main application code
  - `src/cli/` - Command-line interface
  - `src/console/` - Web console (FastAPI)
  - `src/workers/` - Background processing workers
  - `src/adapters/` - External service integrations
  - `src/domain/` - Business logic and models
  - `tests/` - Test files
  - `scripts/` - Utility scripts
  - `docs/` - Documentation
  - `config/` - Configuration files

### Code Quality Standards
- **No type: ignore comments** - fix type issues properly
- **No bare except clauses** - catch specific exceptions
- **No unused imports** - clean up imports regularly
- **No mutable default arguments** - use `field(default_factory=...)`
- **No hardcoded secrets** - use environment variables
- **Keep functions focused** - max 50 lines per function
- **Keep classes focused** - single responsibility principle

### Performance Considerations
- **Use async/await** for I/O operations in FastAPI routes
- **Batch database operations** when possible
- **Use streaming** for large data transfers
- **Cache expensive computations** appropriately
- **Profile performance** bottlenecks before optimizing

### Security
- **Validate all inputs** - never trust user data
- **Use parameterized queries** - prevent SQL injection
- **Authenticate API endpoints** - use the security module
- **Log security events** - monitor for suspicious activity
- **Keep dependencies updated** - watch for security advisories

## Agent-Specific Guidelines

### When Adding New Features
1. **Follow existing patterns** in similar modules
2. **Add type hints** to all new code
3. **Write tests** for new functionality
4. **Update documentation** if needed
5. **Run tests** before committing

### When Refactoring
1. **Maintain backward compatibility** unless explicitly breaking
2. **Update tests** to match new interfaces
3. **Run full test suite** after changes
4. **Document breaking changes** clearly

### When Debugging
1. **Use logging** to trace execution flow
2. **Check database state** for data issues
3. **Test with minimal reproduction** cases
4. **Verify environment configuration**

### When Working with External APIs
1. **Use the adapter pattern** in `src/adapters/`
2. **Handle rate limits** and retries appropriately
3. **Mock external calls** in tests
4. **Log API interactions** for debugging

Remember: This codebase emphasizes **type safety**, **clear separation of concerns**, and **testable code**. Always prioritize maintainability and correctness over short-term speed gains.</content>
<parameter name="filePath">AGENTS.md