"""Windows PowerShell 5.1 按 ANSI 代码页读取无 BOM 的 .ps1。

带非 ASCII 字符又没有 UTF-8 BOM 的脚本会在解析阶段就失败（`param(` 被并进
上一行的注释里），计划任务因此只留下 Last Result=1，连日志都不会生成。
2026-10-30 的 EduNewsPromptOptimizer 就是这样连续两晚空跑的，见
.memory/pitfalls.md。此测试锁定「要么纯 ASCII，要么带 BOM」。
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS_ROOT = Path("scripts")
UTF8_BOM = b"\xef\xbb\xbf"


def test_powershell_scripts_are_ascii_or_carry_utf8_bom() -> None:
    offenders = [
        str(path)
        for path in sorted(SCRIPTS_ROOT.rglob("*.ps1"))
        if not path.read_bytes().startswith(UTF8_BOM)
        and any(byte > 0x7F for byte in path.read_bytes())
    ]

    assert offenders == [], (
        "以下 .ps1 含非 ASCII 字符但没有 UTF-8 BOM，Windows PowerShell 5.1 "
        f"会按 ANSI 解码并导致解析失败：{offenders}"
    )
