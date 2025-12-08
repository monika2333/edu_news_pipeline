from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import subprocess
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

XHS_ROOT = Path(os.getenv("XHS_SUMMARY_ROOT", "xiaohongshu-summary - origin"))
DEFAULT_INPUT = XHS_ROOT / "input_task.txt"
GUIDE_PATH = XHS_ROOT / "xiaohongshu-post-analysis-guide.md"

LINK_PATTERN = re.compile(r"http://xhslink\.com/o/[A-Za-z0-9]+")
ALLOWED_FILENAME = re.compile(r"^[0-9A-Za-z_-]+(?:\\.txt)?$")


@dataclass
class ExtractionResult:
    links: List[str]


@dataclass
class RunResult:
    stdout: str
    stderr: str
    returncode: int
    output_path: Optional[Path] = None


@dataclass
class TaskState:
    status: str
    output_path: Optional[Path]
    content: Optional[str] = None
    error: Optional[str] = None
    prompt: Optional[str] = None


_tasks: Dict[str, TaskState] = {}


def _ensure_text(raw_text: str, source_path: Optional[Path]) -> str:
    if raw_text and raw_text.strip():
        return raw_text
    target = source_path or DEFAULT_INPUT
    if not target.exists():
        raise FileNotFoundError(f"Input file not found: {target}")
    return target.read_text(encoding="utf-8")


def _extract_links_from_text(text: str) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for match in LINK_PATTERN.findall(text):
        if match in seen:
            continue
        seen.add(match)
        ordered.append(match)
    return ordered


def extract_links(raw_text: str, source_path: Optional[Path] = None) -> ExtractionResult:
    text = _ensure_text(raw_text, source_path)
    links = _extract_links_from_text(text)
    return ExtractionResult(links=links)


def compose_prompt(links: List[str], links_file: Optional[Path], summaries_file: Path) -> str:
    guide_ref = GUIDE_PATH.name
    links_section = "\n".join(f"- {link}" for link in links) if links else "- （未提供链接列表）"
    links_hint = (
        f"链接已写入 {links_file.name}，如需可自行读取；" if links_file else "链接列表直接附在下方；"
    )
    prompt = (
        f"请调用 chrome-devtools mcp，按照《小红书帖文分析指南》({guide_ref}) 执行。\n"
        f"{links_hint}依次处理以下链接：\n"
        f"{links_section}\n"
        f"将结果写入 {summaries_file.name}，并直接返回最终总结内容。"
    )
    return prompt


def _sanitize_filename(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("文件名不能为空")
    if not ALLOWED_FILENAME.fullmatch(cleaned):
        raise ValueError("文件名仅允许字母、数字、下划线、连字符（可选 .txt 扩展名）")
    if not cleaned.endswith(".txt"):
        cleaned = f"{cleaned}.txt"
    return cleaned


def _dedupe_path(base_path: Path) -> Path:
    if not base_path.exists():
        return base_path
    stem, suffix = base_path.stem, base_path.suffix
    idx = 1
    while True:
        candidate = base_path.with_name(f"{stem}({idx}){suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def _resolve_output_path(summaries_filename: Optional[str]) -> Path:
    XHS_ROOT.mkdir(parents=True, exist_ok=True)
    if summaries_filename:
        safe_name = _sanitize_filename(summaries_filename)
        return _dedupe_path(XHS_ROOT / safe_name)
    today = dt.datetime.now().strftime("%Y%m%d")
    base = XHS_ROOT / f"{today}xiaohongshu-summaries.txt"
    return _dedupe_path(base)


def _write_links_temp(links: List[str]) -> Optional[Path]:
    if not links:
        return None
    today = dt.datetime.now().strftime("%Y%m%d")
    temp_path = XHS_ROOT / f"{today}-xiaohongshu-links-temp.txt"
    temp_path.write_text("\n".join(links), encoding="utf-8")
    return temp_path


def run_codex(prompt: str, workdir: Path, output_file: Path) -> RunResult:
    if os.getenv("XHS_SUMMARY_FAKE_OUTPUT"):
        workdir.mkdir(parents=True, exist_ok=True)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        fake_text = os.getenv("XHS_SUMMARY_FAKE_TEXT") or "fake output for testing"
        output_file.write_text(fake_text, encoding="utf-8")
        return RunResult(stdout=fake_text, stderr="", returncode=0, output_path=output_file)

    if not shutil.which("codex"):
        return RunResult(
            stdout="",
            stderr="codex CLI 未安装或不可用",
            returncode=127,
            output_path=output_file,
        )
    workdir = workdir.resolve()
    output_file = output_file.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    cmd = [
        "codex",
        "exec",
        "--full-auto",
        "--sandbox",
        "danger-full-access",
        # Avoid interactive prompt when the workdir is not a git repo.
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_file),
        prompt,
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=str(workdir),
        )
        return RunResult(
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            returncode=completed.returncode,
            output_path=output_file,
        )
    except FileNotFoundError:
        return RunResult(
            stdout="",
            stderr="codex CLI 未找到，请确认已安装并在 PATH 中可用",
            returncode=127,
            output_path=output_file,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return RunResult(
            stdout="",
            stderr=str(exc),
            returncode=1,
            output_path=output_file,
        )


async def start_summary_task(links: List[str], summaries_filename: Optional[str] = None) -> tuple[str, str]:
    normalized_links = []
    seen = set()
    for link in links or []:
        if not link:
            continue
        link_str = str(link).strip()
        if not link_str or link_str in seen:
            continue
        seen.add(link_str)
        normalized_links.append(link_str)
    if not normalized_links:
        raise ValueError("请提供至少一个链接")

    output_path = _resolve_output_path(summaries_filename)
    temp_links_file = _write_links_temp(normalized_links)
    prompt = compose_prompt(normalized_links, temp_links_file, output_path)

    task_id = str(uuid.uuid4())
    _tasks[task_id] = TaskState(status="pending", output_path=output_path, prompt=prompt)

    async def _run() -> None:
        state = _tasks.get(task_id)
        if state:
            state.status = "running"
        result = await asyncio.to_thread(run_codex, prompt, XHS_ROOT, output_path)
        state = _tasks.get(task_id)
        if state is None:
            return
        if temp_links_file and temp_links_file.exists():
            try:
                temp_links_file.unlink()
            except Exception:
                logger.warning("Failed to remove temp links file: %s", temp_links_file)
        if result.returncode != 0:
            state.status = "failed"
            state.error = result.stderr or f"codex exited with code {result.returncode}"
            return
        state.status = "succeeded"
        state.output_path = result.output_path
        try:
            if state.output_path and state.output_path.exists():
                state.content = state.output_path.read_text(encoding="utf-8")
            else:
                state.content = result.stdout
        except Exception as exc:  # pragma: no cover - defensive read
            state.content = ""
            state.error = f"读取输出失败: {exc}"

    loop = asyncio.get_running_loop()
    loop.create_task(_run())
    return task_id, prompt


def get_task(task_id: str) -> Optional[Dict[str, object]]:
    state = _tasks.get(task_id)
    if not state:
        return None
    payload: Dict[str, object] = {
        "task_id": task_id,
        "status": state.status,
        "output_path": str(state.output_path) if state.output_path else None,
        "error": state.error,
        "content": state.content,
        "prompt": state.prompt,
    }
    return payload


__all__ = [
    "compose_prompt",
    "extract_links",
    "get_task",
    "run_codex",
    "start_summary_task",
]
