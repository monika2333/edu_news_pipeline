from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)

from src.config import get_settings
from src.console import auth_service
from src.console.auth_schemas import (
    ChangePasswordRequest,
    ConsoleUserResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
)
from src.console.security import ConsoleUser, require_console_user, require_csrf

router = APIRouter(tags=["auth"])


def _user_response(user: ConsoleUser) -> ConsoleUserResponse:
    return ConsoleUserResponse(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


def _login_key(request: Request, username: str) -> str:
    return f"{_client_host(request)}:{username.strip().lower()}"


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/api/auth/login", response_model=LoginResponse)
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> LoginResponse:
    login_key = _login_key(request, payload.username)
    try:
        auth_service.ensure_login_allowed(login_key)
        session = auth_service.authenticate_and_create_session(
            username=payload.username,
            password=payload.password,
        )
    except auth_service.LoginRateLimitError as exc:
        auth_service.record_login_failure_audit(
            username=payload.username,
            client_host=_client_host(request),
            rate_limited=True,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    except (auth_service.AuthenticationError, ValueError) as exc:
        auth_service.record_login_failure(login_key)
        auth_service.record_login_failure_audit(
            username=payload.username,
            client_host=_client_host(request),
            rate_limited=False,
            request_id=request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        ) from exc

    auth_service.clear_login_failures(login_key)
    settings = get_settings()
    max_age = settings.console_session_days * 24 * 60 * 60
    response.set_cookie(
        auth_service.SESSION_COOKIE_NAME,
        session.raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.console_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        auth_service.CSRF_COOKIE_NAME,
        session.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.console_cookie_secure,
        samesite="lax",
        path="/",
    )
    return LoginResponse(user=_user_response(session.user))


@router.post(
    "/api/auth/logout",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def logout(
    request: Request,
    response: Response,
    user: ConsoleUser = Depends(require_console_user),
) -> MessageResponse:
    raw_token = request.cookies.get(auth_service.SESSION_COOKIE_NAME) or ""
    auth_service.revoke_session(raw_token, user=user)
    settings = get_settings()
    response.delete_cookie(
        auth_service.SESSION_COOKIE_NAME,
        httponly=True,
        secure=settings.console_cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        auth_service.CSRF_COOKIE_NAME,
        httponly=False,
        secure=settings.console_cookie_secure,
        samesite="lax",
        path="/",
    )
    return MessageResponse(message="已退出登录")


@router.get("/api/me", response_model=ConsoleUserResponse)
def current_user(
    user: ConsoleUser = Depends(require_console_user),
) -> ConsoleUserResponse:
    return _user_response(user)


@router.post(
    "/api/me/change-password",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def change_password(
    payload: ChangePasswordRequest,
    user: ConsoleUser = Depends(require_console_user),
) -> MessageResponse:
    try:
        auth_service.change_password(
            user=user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except auth_service.AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return MessageResponse(message="密码已更新")


__all__ = ["router"]
