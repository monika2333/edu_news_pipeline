from __future__ import annotations

from typing import Any, Optional

from src.workers import route_summary


class _RoutingAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.completed: list[tuple[str, Optional[bool], str]] = []

    def fetch_pending_summary_routes(
        self,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.rows[:limit]

    def complete_summary_routing(
        self,
        article_id: str,
        *,
        beijing_related: Optional[bool],
        status: str,
    ) -> None:
        self.completed.append((article_id, beijing_related, status))

def test_route_summary_uses_local_beijing_detection(monkeypatch) -> None:
    adapter = _RoutingAdapter(
        [
            {
                "article_id": "article-1",
                "title": "北京教育新闻",
                "content_markdown": "正文",
                "llm_summary": "摘要",
                "sentiment_label": "positive",
            }
        ]
    )
    monkeypatch.setattr(route_summary, "get_adapter", lambda: adapter)
    monkeypatch.setattr(route_summary, "load_beijing_keywords", lambda path: ["北京"])

    route_summary.run(limit=1)

    assert adapter.completed == [("article-1", True, "pending_beijing_gate")]
