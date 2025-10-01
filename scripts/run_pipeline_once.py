from __future__ import annotations

import argparse
import json
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence

from src.workers.crawl_toutiao import run as run_crawl
from src.workers.export_brief import run as run_export
from src.workers.score import run as run_score
from src.workers.summarize import run as run_summarize


StepHandler = Callable[[], Optional[Dict[str, str]]]
DEFAULT_PIPELINE: Sequence[str] = ("crawl", "summarize", "score", "export")


@dataclass
class StepResult:
    name: str
    status: str
    started_at: datetime
    finished_at: datetime
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


@dataclass
class PipelineRunResult:
    run_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str = "running"
    steps: List[StepResult] = field(default_factory=list)
    artifacts: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "steps": [step.to_dict() for step in self.steps],
            "artifacts": dict(self.artifacts),
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _run_crawl_step() -> Dict[str, str]:
    run_crawl()
    return {}


def _run_summarize_step() -> Dict[str, str]:
    run_summarize()
    return {}


def _run_score_step() -> Dict[str, str]:
    run_score()
    return {}


def _run_export_step() -> Dict[str, str]:
    output_path = run_export()
    if output_path is None:
        return {}
    return {"export_path": str(output_path)}


STEP_REGISTRY: Dict[str, StepHandler] = {
    "crawl": _run_crawl_step,
    "summarize": _run_summarize_step,
    "score": _run_score_step,
    "export": _run_export_step,
}


def run_pipeline_once(
    steps: Optional[Sequence[str]] = None,
    *,
    continue_on_error: bool = False,
) -> PipelineRunResult:
    plan = list(steps) if steps is not None else list(DEFAULT_PIPELINE)
    unknown = [name for name in plan if name not in STEP_REGISTRY]
    if unknown:
        raise ValueError(f"Unknown pipeline step(s): {', '.join(unknown)}")

    result = PipelineRunResult(run_id=uuid.uuid4().hex, started_at=_utcnow())
    had_failure = False

    for name in plan:
        handler = STEP_REGISTRY[name]
        step_started = _utcnow()
        error_text: Optional[str] = None
        artifacts: Dict[str, str] = {}
        try:
            handler_result = handler()
            if handler_result:
                artifacts.update(handler_result)
            status = "success"
        except Exception as exc:  # pragma: no cover - defensive logging for manual runs
            status = "failed"
            had_failure = True
            error_text = "".join(traceback.format_exception(exc)).rstrip()
        step_finished = _utcnow()
        result.steps.append(
            StepResult(
                name=name,
                status=status,
                started_at=step_started,
                finished_at=step_finished,
                error=error_text,
            )
        )
        if artifacts:
            result.artifacts.update(artifacts)
        if status != "success" and not continue_on_error:
            break

    result.finished_at = result.steps[-1].finished_at if result.steps else result.started_at

    if not had_failure:
        result.status = "success"
    else:
        if continue_on_error and len(result.steps) == len(plan):
            result.status = "partial"
        else:
            result.status = "failed"

    return result


def _format_plan(steps: Sequence[str], skip: Sequence[str]) -> List[str]:
    plan = list(steps)
    if skip:
        skip_set = set(skip)
        plan = [step for step in plan if step not in skip_set]
    return plan


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the edu news pipeline once.")
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=list(STEP_REGISTRY.keys()),
        help="Explicit step order to run (default: crawl summarize score export)",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        choices=list(STEP_REGISTRY.keys()),
        default=[],
        help="Steps to skip from the selected plan.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Attempt remaining steps even if a step fails.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    return parser.parse_args(argv)


def _print_human_summary(result: PipelineRunResult) -> None:
    print(f"run_id: {result.run_id}")
    print(f"status: {result.status}")
    print(f"started_at: {result.started_at.isoformat()}")
    finished = result.finished_at.isoformat() if result.finished_at else "-"
    print(f"finished_at: {finished}")
    for step in result.steps:
        duration = f"{step.duration_seconds:.2f}s"
        print(f"step {step.name}: {step.status} ({duration})")
        if step.error:
            print("  error: " + step.error.replace("\n", " | "))
    if result.artifacts:
        for key, value in result.artifacts.items():
            print(f"artifact {key}: {value}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    selected_steps = args.steps or list(DEFAULT_PIPELINE)
    plan = _format_plan(selected_steps, args.skip)
    result = run_pipeline_once(plan, continue_on_error=args.continue_on_error)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human_summary(result)

    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
