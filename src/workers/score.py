from __future__ import annotations


def run(limit: int = 100) -> None:
    """Score summarised articles and store the correlation metrics."""
    raise NotImplementedError("score.run needs an implementation")


__all__ = ["run"]
