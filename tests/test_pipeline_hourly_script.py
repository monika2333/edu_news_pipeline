from __future__ import annotations

from pathlib import Path


def test_hourly_pipeline_runs_submission_dedup_after_external_filter() -> None:
    source = Path("scripts/run_pipeline_hourly.ps1").read_text(
        encoding="utf-8"
    )

    external_filter_index = source.index('"external-filter",')
    submission_dedup_index = source.index('"submission-dedup",')
    trigger_source_index = source.index('"--trigger-source",')

    assert external_filter_index < submission_dedup_index
    assert submission_dedup_index < trigger_source_index
