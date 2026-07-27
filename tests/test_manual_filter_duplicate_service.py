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
        "external_importance_score": 88,
        "bonus_keywords": ["重点"],
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
    assert result["groups"][0]["items"][0]["score"] == 88
    assert result["groups"][0]["items"][0]["bonus_keywords"] == ["重点"]


def test_check_duplicates_uses_supplied_review_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_item("a1"), _item("a2")]
    calls: list[dict[str, Any]] = []

    def review_loader(decision: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"decision": decision, **kwargs})
        return {"items": items, "total": len(items)}

    monkeypatch.setattr(
        duplicate_service,
        "list_review",
        lambda *args, **kwargs: pytest.fail("default review loader should not be used"),
    )
    monkeypatch.setattr(
        duplicate_service,
        "call_duplicate_review",
        lambda model_items: [["a1", "a2"]],
    )
    _patch_settings(monkeypatch)

    result = duplicate_service.check_duplicates(
        report_type="wanbao",
        decision="backup",
        review_loader=review_loader,
    )

    assert len(calls) == 2
    assert all(call["decision"] == "backup" for call in calls)
    assert all(call["report_type"] == "wanbao" for call in calls)
    assert result["groups"][0]["group_id"] == "duplicate-1"


def test_response_item_does_not_fall_back_to_primary_score() -> None:
    item = _item("a1")
    item["external_importance_score"] = None
    item["score"] = 55

    result = duplicate_service._response_item(item)

    assert result["score"] is None


def test_check_duplicates_refreshes_items_and_filters_moved_news(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_items = [_item("a1"), _item("a2"), _item("a3")]
    latest_a1 = _item("a1")
    latest_a1["summary"] = "最新摘要"
    latest_a1["llm_source_display"] = "最新来源"
    latest_items = [latest_a1, _item("a3"), _item("a4")]
    review_results = iter(
        [
            {"items": initial_items, "total": len(initial_items)},
            {"items": latest_items, "total": len(latest_items)},
        ]
    )
    monkeypatch.setattr(
        duplicate_service,
        "list_review",
        lambda *args, **kwargs: next(review_results),
    )
    monkeypatch.setattr(
        duplicate_service,
        "call_duplicate_review",
        lambda model_items: [["a1", "a2", "a3"]],
    )
    _patch_settings(monkeypatch)

    result = duplicate_service.check_duplicates(
        report_type="zongbao",
        decision="selected",
    )

    assert result["checked_count"] == 3
    assert result["current_count"] == 3
    assert result["added_count"] == 1
    assert result["removed_count"] == 1
    assert result["report_type"] == "zongbao"
    assert result["decision"] == "selected"
    assert [item["article_id"] for item in result["groups"][0]["items"]] == [
        "a1",
        "a3",
    ]
    assert result["groups"][0]["items"][0]["summary"] == "最新摘要"
    assert result["groups"][0]["items"][0]["source"] == "最新来源"


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
