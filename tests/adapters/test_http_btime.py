from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.adapters import http_btime


LIST_PAYLOAD = {
    "code": 0,
    "message": "",
    "data": {
        "data": [
            {
                "gid": "43r9knd9fc388q9qfgtr1q0k5i0",
                "open_url": "http://item.btime.com/43r9knd9fc388q9qfgtr1q0k5i0",
                "url": "http://item.btime.com/fallback",
                "data": {
                    "title": "珍稀标本被当成玩具",
                    "pdate": 1788131274,
                    "source": "BRTV新闻 社会新闻",
                    "summary": "列表摘要不会写进入库行",
                },
            },
            {
                "gid": "43rkpoj3c4485dbba5skhqo87a8",
                "url": "https://item.btime.com/43rkpoj3c4485dbba5skhqo87a8",
                "data": {
                    "title": "应急安全提示",
                    "pdate": 1788102279,
                    "source": "BRTV新闻 社会新闻",
                },
            },
        ]
    },
}

DETAIL_HTML = """
<html>
  <head><title>珍稀标本被当成玩具_北京时间</title></head>
  <body>
    <nav>频道导航</nav>
    <div class="seo_aritcle_content">
      <div class="article_content">
        <h1>珍稀标本被当成玩具_北京时间</h1>
        <div class="aritcle content"><p>重复的 SEO 正文。</p></div>
        <article>
          <p><img src="https://example.com/cover.jpg" alt="封面"></p>
          <p>视频稿的一句说明。</p>
          <div class="share">分享到</div>
          <div class="recommend">推荐阅读</div>
          <div class="editor">责任编辑：测试</div>
        </article>
      </div>
    </div>
    <footer>站点页脚</footer>
  </body>
</html>
"""


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(LIST_PAYLOAD)


def test_load_uid_entries_accepts_bare_ids_urls_bom_and_deduplicates(tmp_path: Path) -> None:
    path = tmp_path / "uids.txt"
    path.write_text(
        "\ufeff# accounts\n2874221\nhttps://record.btime.com/show?uid=2874221\n2874222\n",
        encoding="utf-8",
    )

    entries = http_btime.load_uid_entries(path)

    assert [entry.uid for entry in entries] == ["2874221", "2874222"]
    assert entries[0].profile_url == "https://record.btime.com/show?uid=2874221"


def test_verified_list_params_keep_refresh_one_and_omit_jsonp_fields() -> None:
    params = http_btime._build_list_params("2874221")

    assert params["refresh"] == 1
    assert params["page"] == 1
    assert params["offset"] == 0
    assert "callback" not in params
    assert "_" not in params


def test_parse_feed_payload_maps_gid_url_time_and_source() -> None:
    items = http_btime._parse_feed_payload(LIST_PAYLOAD)

    assert len(items) == 2
    assert items[0].gid == "43r9knd9fc388q9qfgtr1q0k5i0"
    assert items[0].url == "https://item.btime.com/43r9knd9fc388q9qfgtr1q0k5i0"
    assert items[0].publish_time_iso == "2026-08-31T07:07:54+08:00"
    assert items[0].section == "BRTV新闻 社会新闻"
    assert http_btime.make_article_id(items[0].url) == "btime:43r9knd9fc388q9qfgtr1q0k5i0"


def test_list_items_uses_source_referer_and_ignores_unconfirmed_pages(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    path = tmp_path / "uids.txt"
    path.write_text("2874221\n", encoding="utf-8")
    session = FakeSession()
    monkeypatch.setattr(http_btime, "_resolve_uids_path", lambda: path)
    monkeypatch.setattr(http_btime, "_session", lambda: session)

    items = http_btime.list_items(pages=99)

    assert len(items) == 2
    assert len(session.calls) == 1
    assert session.calls[0]["params"]["refresh"] == 1
    assert session.calls[0]["headers"]["Referer"] == (
        "https://record.btime.com/show?uid=2874221"
    )


def test_detail_parser_uses_article_node_and_removes_page_noise() -> None:
    data = http_btime._parse_detail_html(
        DETAIL_HTML,
        "http://item.btime.com/43r9knd9fc388q9qfgtr1q0k5i0",
    )

    assert data["title"] == "珍稀标本被当成玩具"
    assert "视频稿的一句说明" in data["content_markdown"]
    assert "https://example.com/cover.jpg" in data["content_markdown"]
    assert "重复的 SEO 正文" not in data["content_markdown"]
    assert "分享到" not in data["content_markdown"]
    assert "推荐阅读" not in data["content_markdown"]
    assert "责任编辑" not in data["content_markdown"]
    assert "频道导航" not in data["content_markdown"]
    assert "站点页脚" not in data["content_markdown"]


def test_empty_video_body_is_valid_and_feed_summary_stays_none() -> None:
    detail = http_btime._parse_detail_html("<html><h1>空视频稿</h1></html>", "https://item.btime.com/x")
    item = http_btime._parse_feed_payload(LIST_PAYLOAD)[0]
    fetched_at = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
    row = http_btime.feed_item_to_row(
        item,
        http_btime.make_article_id(item.gid),
        fetched_at=fetched_at,
    )

    assert detail["content_markdown"] == ""
    assert row["summary"] is None
    assert row["profile_url"] is None

