from __future__ import annotations

from pathlib import Path


def test_clear_review_buckets_script_uses_cli_and_propagates_exit_code() -> None:
    source = Path("scripts/run_clear_review_buckets.ps1").read_text(
        encoding="utf-8"
    )

    assert '[string]$Python = "python"' in source
    assert 'Join-Path $repoRoot "logs"' in source
    assert '"-m", "src.cli.main", "clear-review-buckets"' in source
    assert "Push-Location $repoRoot" in source
    assert "$exitCode = $LASTEXITCODE" in source
    assert "exit $exitCode" in source
