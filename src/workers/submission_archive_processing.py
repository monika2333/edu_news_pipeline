from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Any, Optional, Sequence

from src.workers import log_info, worker_session

WORKER = "submission_archive_processing"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def launch_submission_report_processing(report_id: str) -> int:
    command = [
        sys.executable,
        "-m",
        "src.workers.submission_archive_processing",
        report_id,
    ]
    process_kwargs: dict[str, Any] = {
        "cwd": _REPO_ROOT,
    }
    if sys.platform == "win32":
        process_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        process_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **process_kwargs)
    return process.pid


def process_submission_report(report_id: str) -> dict[str, int]:
    from src.console import submission_archive_service
    from src.workers.submission_dedup import backfill_archive_embeddings

    with worker_session(WORKER):
        link_summary = submission_archive_service.process_report_links(
            report_id
        )
        log_info(WORKER, f"Linked report {report_id}: {link_summary}")
        embedded = backfill_archive_embeddings()
        log_info(WORKER, f"Embedded {embedded} archive items")
    return {
        **link_summary,
        "embedded": embedded,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Process submission archive links outside the console server",
    )
    parser.add_argument("report_id")
    args = parser.parse_args(argv)
    process_submission_report(str(args.report_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "launch_submission_report_processing",
    "main",
    "process_submission_report",
]
