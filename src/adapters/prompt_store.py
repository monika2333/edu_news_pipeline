from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from src.config import get_settings
from src.domain.prompt import (
    PROMPT_DEFINITIONS,
    PROMPT_KEYS,
    PromptDefinition,
    PromptTemplate,
    format_prompt_version,
    parse_prompt_version,
)

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter

logger = logging.getLogger(__name__)

PROMPT_TAG_PATTERN = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)
PROMPT_VERSIONS_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "prompts" / "VERSIONS"
)


def read_prompt_template(path: Path) -> str:
    """Read a prompt file and return only the effective ``<prompt>`` body."""

    content = path.read_text(encoding="utf-8")
    match = PROMPT_TAG_PATTERN.search(content)
    if match:
        return match.group(1).strip()
    return content.strip()


def read_prompt_versions(path: Optional[Path] = None) -> dict[str, int]:
    """Read the file-fallback version labels.

    Lenient by design: this path is only used while the database has no revision
    for a prompt key, so a malformed file must not take the console down. The
    legacy ``load_prompt_versions`` in ``external_filter_model`` keeps its strict
    contract for tests and older callers.
    """

    versions_path = path or PROMPT_VERSIONS_PATH
    versions: dict[str, int] = {}
    try:
        lines = versions_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        logger.warning("Prompt versions file is missing: %s", versions_path)
        return versions
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, version = line.partition(":")
        if not separator:
            logger.warning(
                "Ignoring malformed prompt version line in %s: %s",
                versions_path,
                line,
            )
            continue
        revision = parse_prompt_version(version)
        if key.strip() not in PROMPT_KEYS or revision is None:
            logger.warning(
                "Ignoring unknown prompt version entry in %s: %s",
                versions_path,
                line,
            )
            continue
        versions[key.strip()] = revision
    return versions


def prompt_path_for_key(key: str, settings: Any = None) -> Path:
    current = settings or get_settings()
    paths = {
        "external_positive": current.external_filter_prompt_path,
        "external_negative": current.external_negative_filter_prompt_path,
        "internal_positive": current.internal_filter_prompt_path,
        "internal_negative": current.internal_negative_filter_prompt_path,
        "beijing_gate": current.beijing_gate_prompt_path,
    }
    try:
        return Path(paths[key])
    except KeyError as exc:
        raise KeyError(f"未知提示词：{key}") from exc


def load_file_prompt(
    definition: PromptDefinition,
    *,
    settings: Any = None,
    versions: Optional[dict[str, int]] = None,
) -> PromptTemplate:
    """Resolve the file fallback for one prompt.

    This is what the pipeline used before revisions existed; it stays as the
    source of truth until the first console save for that key.
    """

    resolved_versions = (
        versions if versions is not None else read_prompt_versions()
    )
    path = prompt_path_for_key(definition.key, settings)
    content = ""
    if path.exists():
        content = read_prompt_template(path)
    else:
        logger.warning("Prompt file is missing: %s", path)
    revision = resolved_versions.get(definition.key, 1)
    return PromptTemplate(
        key=definition.key,
        version=format_prompt_version(revision),
        content=content,
        source="file",
        revision=revision,
    )


def load_active_prompts(
    adapter: Optional[PostgresAdapter] = None,
    *,
    settings: Any = None,
) -> dict[str, PromptTemplate]:
    """Resolve all five prompts: database revision first, file fallback second.

    Call this once per pipeline run and pass the resulting mapping to workers so
    content and version cannot diverge inside a run.
    """

    if adapter is None:
        from src.adapters.db_postgres_core import get_adapter

        adapter = get_adapter()
    rows = adapter.prompt_revisions.fetch_latest_by_key()
    versions = read_prompt_versions()
    prompts: dict[str, PromptTemplate] = {}
    for definition in PROMPT_DEFINITIONS:
        row = rows.get(definition.key)
        if row is None:
            prompts[definition.key] = load_file_prompt(
                definition,
                settings=settings,
                versions=versions,
            )
            continue
        revision = int(row["revision"])
        prompts[definition.key] = PromptTemplate(
            key=definition.key,
            version=format_prompt_version(revision),
            content=str(row["content"]).strip(),
            source="database",
            revision=revision,
            revision_id=int(row["id"]),
            updated_at=row.get("created_at"),
            updated_by_user_id=(
                str(row["created_by_user_id"])
                if row.get("created_by_user_id")
                else None
            ),
            updated_by_display_name=row.get("created_by_display_name"),
        )
    return prompts


def load_active_prompt(
    key: str,
    adapter: Optional[PostgresAdapter] = None,
    *,
    settings: Any = None,
) -> PromptTemplate:
    prompts = load_active_prompts(adapter, settings=settings)
    try:
        return prompts[key]
    except KeyError as exc:
        raise KeyError(f"未知提示词：{key}") from exc


__all__ = [
    "PROMPT_TAG_PATTERN",
    "PROMPT_VERSIONS_PATH",
    "load_active_prompt",
    "load_active_prompts",
    "load_file_prompt",
    "prompt_path_for_key",
    "read_prompt_template",
    "read_prompt_versions",
]
