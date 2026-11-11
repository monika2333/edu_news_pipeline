"""控制台静态资源版本号的单元测试。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from src.console import web_routes
from src.console.app import create_app


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def test_asset_version_is_stable_without_changes(tmp_path: Path) -> None:
    static_dir = tmp_path / "web_static"
    _write(static_dir / "css" / "a.css", "body {}")

    first = web_routes._asset_version(static_dir)
    second = web_routes._asset_version(static_dir)

    assert first == second
    assert len(first) == 12


def test_asset_version_changes_when_file_changes_or_is_added(tmp_path: Path) -> None:
    static_dir = tmp_path / "web_static"
    path = static_dir / "css" / "a.css"
    _write(path, "body {}")
    first = web_routes._asset_version(static_dir)

    stat = path.stat()
    _write(path, "body { color: red; }")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = web_routes._asset_version(static_dir)
    assert second != first

    _write(static_dir / "js" / "b.js", "console.log(1);")
    third = web_routes._asset_version(static_dir)
    assert third != second


def test_rendered_page_uses_asset_version(monkeypatch) -> None:
    monkeypatch.setattr(web_routes, "_asset_version", lambda: "assetversion")
    response = TestClient(create_app()).get("/login")

    assert response.status_code == 200
    assert "?v=assetversion" in response.text
