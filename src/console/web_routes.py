from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.adapters.db_postgres_core import get_adapter
from src.business_config import resolve_review_sort_keywords
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user, require_role
from src.console.shifts_service import (
    ShiftScheduleIncompleteError,
    generate_shifts,
)

router = APIRouter(tags=["console"], include_in_schema=False)

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "web_templates"
_STATIC_DIR = Path(__file__).resolve().parent / "web_static"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _asset_version(static_dir: Path | None = None) -> str:
    """静态资源指纹：文件不变时稳定，任意文件改动后自动换新。

    按相对路径、mtime_ns、大小做短哈希，用于模板里的 ?v= 缓存控制。只做
    stat 不读文件内容，页面渲染开销可忽略；git checkout / 正常部署都会更新
    mtime，所以改完 CSS/JS 后下一次请求就能失效旧缓存。
    """
    directory = static_dir or _STATIC_DIR
    digest = hashlib.sha1()
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            digest.update(path.relative_to(directory).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()[:12]


def _review_sort_rules() -> dict[str, list[str]]:
    """审阅页「自动排序」词表：读分区行，缺失或读库失败时回退默认值。

    排序只是展示层功能，不能因它阻断页面渲染——数据加载的真实错误
    会由页面内的 API 调用暴露，这里刻意收窄故障面。
    """
    try:
        row = get_adapter().app_config.fetch_setting("review_sort_keywords")
    except Exception as exc:  # 渲染路径的降级处理，见 docstring
        logger.warning("读取 review_sort_keywords 失败，审阅页回退默认词表：%s", exc)
        return resolve_review_sort_keywords(None)
    return resolve_review_sort_keywords(row["value"] if row else None)


@router.get("/manual_filter", response_class=HTMLResponse)
async def manual_filter_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    # 静态资源指纹：文件不变时稳定，改了 CSS/JS 自动换版本号
    version = _asset_version()
    return templates.TemplateResponse(
        "manual_filter.html",
        {
            "request": request,
            "version": version,
            "current_user": user,
            "workspace_mode": "admin",
            "admin_view": "filter",
            "review_sort_rules": _review_sort_rules(),
        },
    )


@router.get("/admin/review", response_class=HTMLResponse)
async def admin_review_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "manual_filter.html",
        {
            "request": request,
            "version": version,
            "current_user": user,
            "workspace_mode": "admin",
            "admin_view": "review",
            "review_sort_rules": _review_sort_rules(),
        },
    )


@router.get("/", include_in_schema=False)
async def root_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> Response:
    """Open the active workflow for the user's role and browser history."""
    if user.role == "duty_editor":
        return RedirectResponse(url=request.url_for("duty_page"), status_code=307)
    version = _asset_version()
    return templates.TemplateResponse(
        "admin_entry.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/duty", response_class=HTMLResponse)
async def duty_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "manual_filter.html",
        {
            "request": request,
            "version": version,
            "current_user": user,
            "workspace_mode": "duty",
            "admin_view": "filter",
            "review_sort_rules": _review_sort_rules(),
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    try:
        generate_shifts(days=14, actor_user_id=user.user_id)
    except ShiftScheduleIncompleteError:
        pass
    version = _asset_version()
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/admin/users/new", response_class=HTMLResponse)
async def admin_user_create_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "admin_user_create.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    """系统设置页：模型、数据源与抓取账号的管理员配置入口。"""
    version = _asset_version()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/admin/duty-summary", response_class=HTMLResponse)
async def duty_summary_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    try:
        generate_shifts(days=14, actor_user_id=user.user_id)
    except ShiftScheduleIncompleteError:
        pass
    version = _asset_version()
    return templates.TemplateResponse(
        "duty_summary.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "version": version},
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "version": version},
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "version": version, "current_user": user},
    )


def _submission_archive_response(
    request: Request,
    user: ConsoleUser,
    *,
    view: str,
    report_id: str | None = None,
) -> HTMLResponse:
    version = _asset_version()
    return templates.TemplateResponse(
        "submission_archive.html",
        {
            "request": request,
            "version": version,
            "current_user": user,
            "archive_view": view,
            "report_id": report_id,
        },
    )


def _redirect_non_admin_to_duty(
    request: Request,
    user: ConsoleUser,
) -> RedirectResponse | None:
    # 报送存档是管理员工作区，值班编辑访问时重定向回值班工作区。
    if user.role == "admin":
        return None
    return RedirectResponse(url=request.url_for("duty_page"), status_code=302)


@router.get("/submission-archive", response_class=HTMLResponse)
async def submission_archive_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> Response:
    redirect = _redirect_non_admin_to_duty(request, user)
    if redirect is not None:
        return redirect
    return _submission_archive_response(request, user, view="list")


@router.get("/submission-archive/new", response_class=HTMLResponse)
async def submission_archive_new_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> Response:
    redirect = _redirect_non_admin_to_duty(request, user)
    if redirect is not None:
        return redirect
    return _submission_archive_response(request, user, view="new")


@router.get("/submission-archive/link-queue", response_class=RedirectResponse)
async def submission_archive_link_queue_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> RedirectResponse:
    redirect = _redirect_non_admin_to_duty(request, user)
    if redirect is not None:
        return redirect
    # 回链确认页已删除（功能由报告详情页的批量入口覆盖）。直接删路由会落到
    # /submission-archive/{report_id}，以 report_id="link-queue" 进入详情逻辑，
    # 因此保留一条重定向回存档库列表。
    return RedirectResponse(url="/submission-archive", status_code=302)


@router.get("/submission-archive/search", response_class=RedirectResponse)
async def submission_archive_search_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> RedirectResponse:
    redirect = _redirect_non_admin_to_duty(request, user)
    if redirect is not None:
        return redirect
    # 全库搜索页已删除（功能由检索抽屉覆盖）。直接删路由会落到
    # /submission-archive/{report_id}，以 report_id="search" 进入详情逻辑，
    # 因此保留一条重定向回存档库列表。
    return RedirectResponse(url="/submission-archive", status_code=302)


@router.get("/submission-archive/{report_id}", response_class=HTMLResponse)
async def submission_archive_detail_page(
    request: Request,
    report_id: str,
    user: ConsoleUser = Depends(require_console_user),
) -> Response:
    redirect = _redirect_non_admin_to_duty(request, user)
    if redirect is not None:
        return redirect
    return _submission_archive_response(
        request,
        user,
        view="detail",
        report_id=report_id,
    )


__all__ = ["router"]
