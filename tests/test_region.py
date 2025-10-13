from __future__ import annotations

from pathlib import Path

from src.domain.region import is_beijing_related, load_beijing_keywords


def test_load_beijing_keywords(tmp_path: Path) -> None:
    keywords_file = tmp_path / "keywords.txt"
    keywords_file.write_text("北京\n朝阳区\n北京\n", encoding="utf-8")

    keywords = load_beijing_keywords(keywords_file)

    assert keywords == {"北京", "朝阳区"}


def test_is_beijing_related_matches_texts() -> None:
    keywords = {"北京", "延庆"}

    related = is_beijing_related(["教育资讯：北京高校动态"], keywords)
    unrelated = is_beijing_related(["上海新闻"], keywords)
    keyword_hit = is_beijing_related(["乡村教育"], keywords | {"乡村"})
    with_keywords = is_beijing_related(["河北资讯"], {"延庆"})
    via_list = is_beijing_related(["", "首都教育"], {"首都"})

    assert related is True
    assert unrelated is False
    assert keyword_hit is True
    assert with_keywords is False
    assert via_list is True

