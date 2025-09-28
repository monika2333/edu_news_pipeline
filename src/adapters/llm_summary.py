from __future__ import annotations

from typing import Any, Dict


def build_summary_payload(article: Dict[str, Any]) -> str:
    """Prepare the prompt/context sent to the LLM."""
    raise NotImplementedError("build_summary_payload needs an implementation")


def summarise(article: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the LLM and return the structured summary result."""
    raise NotImplementedError("summarise needs an implementation")


__all__ = ["build_summary_payload", "summarise"]
