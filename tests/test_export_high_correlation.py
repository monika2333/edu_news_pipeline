import sqlite3
from pathlib import Path

import pytest

from tools.export_high_correlation import (
    CATEGORY_ORDER,
    DEFAULT_CATEGORY,
    classify_category,
    export_high_correlation,
)


def s(*codes: int) -> str:
    return ''.join(chr(c) for c in codes)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ((s(0x5317, 0x4eac, 0x5e02, 0x6559, 0x59d4), "", "", "", ""), s(0x5e02, 0x59d4, 0x6559, 0x59d4)),
        (("", "", s(0x793a, 0x8303, 0x5c0f, 0x5b66, 0x4e0e, 0x9ad8, 0x4e2d, 0x5171, 0x5efa, 0x8bfe, 0x7a0b), "", ""), s(0x4e2d, 0x5c0f, 0x5b66)),
        (("", "", s(0x9ad8, 0x6821, 0x79d1, 0x7814, 0x56e2, 0x961f, 0x53d1, 0x5e03, 0x6210, 0x679c), "", ""), s(0x9ad8, 0x6821)),
        (("", "", "", "", ""), DEFAULT_CATEGORY),
        (("", "", s(0x67d0, 0x4e2d, 0x5b66, 0x4e0e, 0x67d0, 0x9ad8, 0x6821, 0x8054, 0x5408, 0x529e, 0x5b66), "", ""), s(0x4e2d, 0x5c0f, 0x5b66)),
    ],
)
def test_classify_category(fields, expected):
    assert classify_category(*fields) == expected


def build_news_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE news_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT,
            title TEXT,
            summary TEXT,
            source TEXT,
            source_LLM TEXT,
            content TEXT,
            correlation INTEGER
        )
        """
    )
    rows = [
        (
            "1",
            s(0x793a, 0x8303, 0x4e2d, 0x5c0f, 0x5b66, 0x4e3b, 0x529e, 0x52b3, 0x52a8, 0x6559, 0x80b2, 0x5468),
            s(0x793a, 0x8303, 0x4e2d, 0x5c0f, 0x5b66, 0x5f00, 0x5c55, 0x52b3, 0x52a8, 0x6559, 0x80b2, 0x8bfe, 0x7a0b),
            s(0x5e02, 0x6559, 0x59d4),
            "",
            "",
            95,
        ),
        (
            "2",
            s(0x9996, 0x90fd, 0x9ad8, 0x6821, 0x63a8, 0x8fdb, 0x79d1, 0x7814, 0x5e73, 0x53f0, 0x5efa, 0x8bbe),
            s(0x9ad8, 0x6821, 0x79d1, 0x7814, 0x56e2, 0x961f, 0x53d1, 0x5e03, 0x91cd, 0x5927, 0x6210, 0x679c),
            s(0x5317, 0x4eac, 0x5e02, 0x6559, 0x80b2, 0x65b0, 0x95fb, 0x7f51),
            "",
            "",
            90,
        ),
        (
            "3",
            s(0x793e, 0x533a, 0x5bb6, 0x957f, 0x5b66, 0x6821, 0x542f, 0x52a8, 0x5bb6, 0x5ead, 0x6559, 0x80b2, 0x8ba1, 0x5212),
            s(0x793e, 0x533a, 0x6559, 0x80b2, 0x4e2d, 0x5fc3, 0x53d1, 0x5e03, 0x5bb6, 0x5ead, 0x6559, 0x80b2, 0x9879, 0x76ee),
            s(0x793e, 0x533a, 0x65e5, 0x62a5),
            "",
            "",
            85,
        ),
        (
            "4",
            s(0x5e02, 0x59d4, 0x6559, 0x59d4, 0x90e8, 0x7f72, 0x65b0, 0x5b66, 0x671f, 0x91cd, 0x70b9, 0x5de5, 0x4f5c),
            s(0x5e02, 0x59d4, 0x6559, 0x59d4, 0x5f3a, 0x8c03, 0x5f3a, 0x5316, 0x6559, 0x80b2, 0x4fdd, 0x969c),
            s(0x5317, 0x4eac, 0x5e02, 0x6559, 0x59d4),
            "",
            "",
            80,
        ),
    ]
    cur.executemany(
        "INSERT INTO news_summaries(article_id, title, summary, source, source_LLM, content, correlation) VALUES(?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_export_groups_and_orders(tmp_path, capsys):
    db_path = tmp_path / "articles.sqlite3"
    output_path = tmp_path / "out.txt"
    build_news_db(db_path)

    exported, skipped = export_high_correlation(db_path, output_path, dry_run=True)
    assert exported == 4
    assert skipped == 0

    dry_run_output = capsys.readouterr().out
    expected_counts = "; ".join([
        f"{CATEGORY_ORDER[0]}:2",
        f"{CATEGORY_ORDER[1]}:0",
        f"{CATEGORY_ORDER[2]}:1",
        f"{DEFAULT_CATEGORY}:1",
    ])
    assert expected_counts in dry_run_output

    exported, skipped = export_high_correlation(db_path, output_path)
    assert exported == 4
    assert skipped == 0

    contents = output_path.read_text(encoding="utf-8").strip().split("\n\n")
    assert len(contents) == 4
    first_titles = [entry.splitlines()[0] for entry in contents]
    assert first_titles[0] == s(0x793a, 0x8303, 0x4e2d, 0x5c0f, 0x5b66, 0x4e3b, 0x529e, 0x52b3, 0x52a8, 0x6559, 0x80b2, 0x5468)
    assert first_titles[1] == s(0x5e02, 0x59d4, 0x6559, 0x59d4, 0x90e8, 0x7f72, 0x65b0, 0x5b66, 0x671f, 0x91cd, 0x70b9, 0x5de5, 0x4f5c)
    assert first_titles[2] == s(0x9996, 0x90fd, 0x9ad8, 0x6821, 0x63a8, 0x8fdb, 0x79d1, 0x7814, 0x5e73, 0x53f0, 0x5efa, 0x8bbe)
    assert first_titles[3] == s(0x793e, 0x533a, 0x5bb6, 0x957f, 0x5b66, 0x6821, 0x542f, 0x52a8, 0x5bb6, 0x5ead, 0x6559, 0x80b2, 0x8ba1, 0x5212)

    conn = sqlite3.connect(db_path)
    history_count = conn.execute("SELECT COUNT(1) FROM export_history").fetchone()[0]
    conn.close()
    assert history_count == 4

    exported_again, skipped_again = export_high_correlation(db_path, output_path)
    assert exported_again == 0
    assert skipped_again == 4
