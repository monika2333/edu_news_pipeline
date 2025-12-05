from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ExtractionResult:
    links: List[str]


@dataclass
class RunResult:
    stdout: str
    stderr: str
    returncode: int
    output_path: Optional[Path] = None


def extract_links(raw_text: str, source_path: Optional[Path] = None) -> ExtractionResult:
    """Placeholder for link extraction; will wrap existing extract_links logic."""
    raise NotImplementedError("extract_links is not implemented yet")


def run_codex(prompt: str, workdir: Path, output_file: Path) -> RunResult:
    """Placeholder for Codex CLI invocation."""
    raise NotImplementedError("run_codex is not implemented yet")


def compose_prompt(links_file: Optional[Path], summaries_file: Path) -> str:
    """Placeholder for prompt construction."""
    raise NotImplementedError("compose_prompt is not implemented yet")
