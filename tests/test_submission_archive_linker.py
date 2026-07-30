from __future__ import annotations

from src.domain.submission_archive_linker import (
    LinkCandidate,
    build_link_candidate_index,
    link_submission_item,
    select_link_candidates,
)


def test_link_l1_exact_normalized_title() -> None:
    result = link_submission_item(
        "学校发布新规！",
        "正文",
        [LinkCandidate("a", "学校发布新规", "正文")],
    )
    assert result.status == "exact"
    assert result.article_id == "a"


def test_link_l2_fuzzy_auto_binds() -> None:
    result = link_submission_item(
        "北京某高校发布招生新政策",
        "学校今天公布招生政策细节",
        [
            LinkCandidate(
                "a",
                "北京某高校发布招生政策",
                "学校今日公布招生政策细节",
            )
        ],
        auto_threshold=0.75,
        review_threshold=0.40,
    )
    assert result.status == "fuzzy"
    assert result.article_id == "a"


def test_link_l3_pending_does_not_bind() -> None:
    result = link_submission_item(
        "北京高校开展校园活动",
        "师生参加活动",
        [LinkCandidate("a", "北京学校举办校园活动", "学生参加活动")],
        auto_threshold=0.95,
        review_threshold=0.50,
    )
    assert result.status == "pending"
    assert result.article_id is None
    assert result.best_candidate_article_id == "a"


def test_link_l4_unmatched_still_records_best_candidate() -> None:
    result = link_submission_item(
        "完全不同的标题",
        "完全不同的正文",
        [LinkCandidate("a", "天气预报", "明天有雨")],
        auto_threshold=0.85,
        review_threshold=0.55,
    )
    assert result.status == "unmatched"
    assert result.article_id is None
    assert result.best_candidate_article_id == "a"
    assert result.combined_score >= 0


def test_link_title_guard_prevents_false_auto_binding() -> None:
    result = link_submission_item(
        "北京大学成立人工智能学院",
        "学校宣布成立人工智能学院并启动招生",
        [
            LinkCandidate(
                "a",
                "清华新闻：新学院挂牌招生",
                "学校宣布成立人工智能学院并启动招生",
            )
        ],
        auto_threshold=0.45,
        review_threshold=0.30,
    )
    assert result.title_score < 0.70
    assert result.status != "fuzzy"


def test_candidate_index_preserves_first_exact_candidate() -> None:
    candidate_index = build_link_candidate_index(
        [
            LinkCandidate("first", "学校发布新规"),
            LinkCandidate("second", "学校发布新规！"),
        ]
    )

    selection = select_link_candidates("学校发布新规", candidate_index)

    assert selection.exact is not None
    assert selection.exact.candidate.article_id == "first"
