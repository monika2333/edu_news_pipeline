from __future__ import annotations


def run(limit: int = 50) -> None:
    """Generate article summaries using the configured LLM adapter."""
    raise NotImplementedError("summarize.run needs an implementation")


__all__ = ["run"]
