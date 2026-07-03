from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user


def _anonymous_console_user() -> ConsoleUser:
    return ConsoleUser(method="test")


def _build_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    return TestClient(app)


def test_console_root_redirects_to_manual_filter() -> None:
    client = _build_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/manual_filter")


def test_removed_console_pages_are_not_registered() -> None:
    client = _build_client()

    assert client.get("/dashboard").status_code == 404
    assert client.get("/articles/search").status_code == 404


def test_duplicate_check_button_is_before_sort_mode() -> None:
    response = _build_client().get("/manual_filter")

    assert response.status_code == 200
    html = response.text
    assert html.index('id="btn-check-duplicates"') < html.index('id="btn-toggle-sort"')
    assert 'id="duplicate-review-modal"' in html
    assert 'id="duplicate-review-select-all"' in html
    assert 'id="duplicate-review-bulk-status"' in html
    assert 'id="btn-duplicate-prev-group"' in html
    assert 'id="btn-duplicate-next-group"' in html
    assert '/static/css/modules/review.css?v=' in html


def test_duplicate_recheck_uses_clicked_button_loading_state() -> None:
    script_path = (
        Path(__file__).parents[1]
        / "src/console/web_static/js/manual_filter/review_tab_duplicates.js"
    )
    script = script_path.read_text(encoding="utf-8")

    assert "event && event.currentTarget ? event.currentTarget : fallbackButton" in script
    assert "duplicateReviewCheckInFlight" in script
    assert "setDuplicateReviewCheckLoading(true, button)" in script
    assert "['btn-check-duplicates', 'btn-recheck-duplicates']" in script
