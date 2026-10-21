from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from src.adapters.db_postgres_shared import json_safe, keyword_labels_from_score_details


def test_json_safe_recursively_serializes_audit_values() -> None:
    payload = {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "created_at": datetime(2026, 7, 25, 8, 30, tzinfo=timezone.utc),
        "coverage_date": date(2026, 7, 25),
        "items": [{"score": Decimal("82.5")}],
    }

    result = json_safe(payload)

    assert result == {
        "id": "00000000-0000-0000-0000-000000000001",
        "created_at": "2026-07-25T08:30:00+00:00",
        "coverage_date": "2026-07-25",
        "items": [{"score": 82.5}],
    }
    assert json.loads(json.dumps(result)) == result


def test_keyword_labels_from_score_details_handles_all_historical_shapes() -> None:
    assert keyword_labels_from_score_details(
        {
            "matched_rules": [
                {"label": "高考", "bonus": 10},
                {"rule_id": "校园安全"},
                {"label": ""},
                "not-a-rule",
            ]
        }
    ) == ["高考", "校园安全"]
    assert keyword_labels_from_score_details({"matched_rules": "bad"}) == []
    assert keyword_labels_from_score_details([{"label": "legacy"}]) == []
    assert keyword_labels_from_score_details(None) == []
