from __future__ import annotations

from dataclasses import replace

import pytest

from src.adapters import llm_duplicate_review as duplicate_review
from src.config import get_settings


def test_build_prompt_requests_only_article_id_groups() -> None:
    prompt = duplicate_review.build_prompt(
        [
            {
                "article_id": "a1",
                "title": "新闻一",
                "summary": "摘要一",
                "source": "来源一",
            }
        ]
    )

    assert '"duplicate_groups"' in prompt
    assert "不要返回理由或置信度" in prompt
    assert '"article_id":"a1"' in prompt


@pytest.mark.parametrize(
    ("raw_output", "expected"),
    [
        ('{"duplicate_groups":[["a1","a2"]]}', [["a1", "a2"]]),
        (
            '```json\n{"duplicate_groups":[["a1","a2"],["a3","a4"]]}\n```',
            [["a1", "a2"], ["a3", "a4"]],
        ),
        ('{"duplicate_groups":[]}', []),
    ],
)
def test_parse_duplicate_groups_accepts_supported_json(
    raw_output: str,
    expected: list[list[str]],
) -> None:
    assert duplicate_review.parse_duplicate_groups(raw_output) == expected


@pytest.mark.parametrize(
    "raw_output",
    [
        "not json",
        "{}",
        '{"duplicate_groups":["a1","a2"]}',
        '{"duplicate_groups":[["a1",2]]}',
    ],
)
def test_parse_duplicate_groups_rejects_invalid_responses(raw_output: str) -> None:
    with pytest.raises(duplicate_review.DuplicateReviewResponseError):
        duplicate_review.parse_duplicate_groups(raw_output)


def test_call_duplicate_review_uses_scoring_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(get_settings(), llm_scoring_model="score-model-for-duplicates")
    captured: dict[str, object] = {}

    def fake_post(payload, *, retries: int, timeout: int) -> str:
        captured["payload"] = payload
        captured["retries"] = retries
        captured["timeout"] = timeout
        return '{"duplicate_groups":[]}'

    monkeypatch.setattr(duplicate_review, "get_settings", lambda: settings)
    monkeypatch.setattr(duplicate_review, "_post_chat_completion", fake_post)

    groups = duplicate_review.call_duplicate_review([], retries=1)

    assert groups == []
    assert captured["payload"]["model"] == "score-model-for-duplicates"
    assert captured["timeout"] == settings.llm_scoring_timeout
