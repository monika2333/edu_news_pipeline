from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from src.config import get_settings
from src.console import manual_filter_duplicate_service as duplicate_service


def _item(
    article_id: str,
    *,
    report_type: str = "zongbao",
    status: str = "selected",
) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "title": f"标题 {article_id}",
        "summary": f"摘要 {article_id}",
        "llm_source_display": f"来源 {article_id}",
        "url": f"https://example.com/{article_id}",
        "manual_status": status,
        "report_type": report_type,
    }


def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(get_settings(), llm_scoring_model="duplicate-test-model")
    monkeypatch.setattr(duplicate_service, "get_settings", lambda: settings)


def test_check_duplicates_merges_overlaps_and_filters_unknown_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item("a1"), _item("a2"), _item("a3"), _item("a4")]
    captured: dict[str, object] = {}

    def fake_list_review(decision: str, **kwargs: Any) -> dict[str, Any]:
        captured["decision"] = decision
        captured.update(kwargs)
        return {"items": items, "total": len(items)}

    monkeypatch.setattr(duplicate_service, "list_review", fake_list_review)
    monkeypatch.setattr(
        duplicate_service,
        "call_duplicate_review",
        lambda model_items: [
            ["a1", "a2", "a2", "unknown"],
            ["a2", "a3"],
            ["a4", "unknown"],
        ],
    )
    _patch_settings(monkeypatch)

    result = duplicate_service.check_duplicates(report_type="wanbao", decision="backup")

    assert captured["decision"] == "backup"
    assert captured["report_type"] == "wanbao"
    assert result["checked_count"] == 4
    assert result["model"] == "duplicate-test-model"
    assert [[item["article_id"] for item in group["items"]] for group in result["groups"]] == [
        ["a1", "a2", "a3"]
    ]


@pytest.mark.parametrize("count", [0, 1])
def test_check_duplicates_skips_model_for_short_lists(
    count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item("a1")][:count]
    monkeypatch.setattr(
        duplicate_service,
        "list_review",
        lambda *args, **kwargs: {"items": items, "total": count},
    )
    monkeypatch.setattr(
        duplicate_service,
        "call_duplicate_review",
        lambda model_items: pytest.fail("model should not be called"),
    )
    _patch_settings(monkeypatch)

    result = duplicate_service.check_duplicates(report_type="zongbao", decision="selected")

    assert result["checked_count"] == count
    assert result["groups"] == []


def test_check_duplicates_rejects_columns_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        duplicate_service,
        "list_review",
        lambda *args, **kwargs: {
            "items": [_item("a1")],
            "total": duplicate_service.MAX_DUPLICATE_REVIEW_ITEMS + 1,
        },
    )

    with pytest.raises(duplicate_service.DuplicateReviewLimitError, match="超过单次查重上限"):
        duplicate_service.check_duplicates(report_type="zongbao", decision="selected")
