"""Auth router - HTTP endpoints for registration, login, refresh, me.

Public endpoints (all under /api/v1/auth):
- POST /register   : create user + auto tenant + tokens
- POST /login      : email/password -> tokens
- POST /refresh    : refresh token -> new access token
- GET  /me         : current user info (requires Authorization header)

Auth flow uses Authorization: Bearer <access_token>.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from omnibase.auth.schemas import (
    AuthErrorResponse,
    LoginRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    TenantPublic,
    TokenResponse,
    UserPublic,
)
from omnibase.auth.service import (
    AuthError,
    EmailAlreadyRegistered,
    InvalidCredentials,
    login,
    refresh_access_token,
    register,
)
from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger
from omnibase.core.rate_limit import enforce_auth_rate_limit
from omnibase.tenants.dependencies import CurrentPrincipal, get_current_principal

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)


# -----------------------------------------------------------
# Helper: standard error envelope
# -----------------------------------------------------------
def _auth_error(code: str, message: str, status_code: int) -> HTTPException:
    """Build a standard auth error HTTPException."""
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message}},
    )


# -----------------------------------------------------------
# POST /api/auth/register
# -----------------------------------------------------------
@router.post(
    "/register",
    dependencies=[Depends(enforce_auth_rate_limit)],
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": AuthErrorResponse, "description": "Email already registered"},
        422: {"model": AuthErrorResponse, "description": "Weak password or invalid email"},
    },
    summary="Register a new user (auto-creates a default tenant)",
)
def register_endpoint(payload: RegisterRequest) -> TokenResponse:
    """Register."""
    try:
        result = register(
            email=payload.email,
            password=payload.password,
            tenant_name=payload.tenant_name,
        )
    except ValueError as exc:
        # Password strength validation
        raise _auth_error("weak_password", str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY) from exc
    except EmailAlreadyRegistered as exc:
        raise _auth_error("email_conflict", str(exc), status.HTTP_409_CONFLICT) from exc
    except AuthError as exc:
        log.error("register.failed", error=str(exc))
        raise _auth_error(
            "registration_failed",
            "Registration failed",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc
    except Exception as exc:
        # Surface tenant creation errors (SchemaError, IntegrityError, etc.)
        log.error("register.unhandled", error=str(exc), exc_info=True)
        raise _auth_error(
            "registration_failed",
            "Registration failed",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc

    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=expires_in,
        user=UserPublic.model_validate(result.user),
        tenant=TenantPublic.model_validate(result.tenant),
    )


# -----------------------------------------------------------
# POST /api/auth/login
# -----------------------------------------------------------
@router.post(
    "/login",
    dependencies=[Depends(enforce_auth_rate_limit)],
    response_model=TokenResponse,
    responses={
        401: {"model": AuthErrorResponse, "description": "Invalid credentials"},
    },
    summary="Authenticate and obtain tokens",
)
def login_endpoint(payload: LoginRequest) -> TokenResponse:
    """Login."""
    try:
        result = login(email=payload.email, password=payload.password)
    except InvalidCredentials as exc:
        raise _auth_error("invalid_credentials", str(exc), status.HTTP_401_UNAUTHORIZED) from exc
    except Exception as exc:
        log.error("login.failed", error=str(exc), exc_info=True)
        raise _auth_error(
            "login_failed", "Login failed", status.HTTP_500_INTERNAL_SERVER_ERROR
        ) from exc

    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    return TokenResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=expires_in,
        user=UserPublic.model_validate(result.user),
        tenant=TenantPublic.model_validate(result.tenant),
    )


# -----------------------------------------------------------
# POST /api/auth/refresh
# -----------------------------------------------------------
@router.post(
    "/refresh",
    dependencies=[Depends(enforce_auth_rate_limit)],
    response_model=RefreshResponse,
    responses={
        401: {"model": AuthErrorResponse, "description": "Invalid or expired refresh token"},
    },
    summary="Exchange refresh token for a new access token",
)
def refresh_endpoint(payload: RefreshRequest) -> RefreshResponse:
    """Refresh."""
    try:
        access_token, expires_in = refresh_access_token(refresh_token=payload.refresh_token)
    except InvalidCredentials as exc:
        raise _auth_error("invalid_refresh_token", str(exc), status.HTTP_401_UNAUTHORIZED) from exc
    except Exception as exc:
        log.error("refresh.failed", error=str(exc), exc_info=True)
        raise _auth_error(
            "refresh_failed", "Token refresh failed", status.HTTP_500_INTERNAL_SERVER_ERROR
        ) from exc

    return RefreshResponse(access_token=access_token, expires_in=expires_in)


# -----------------------------------------------------------
# GET /api/auth/me
# -----------------------------------------------------------
@router.get(
    "/me",
    response_model=UserPublic,
    responses={
        401: {"model": AuthErrorResponse, "description": "Authentication required"},
    },
    summary="Get current user info",
)
def me_endpoint(
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> UserPublic:
    """Return the active database-backed current user."""
    return UserPublic.model_validate(principal.user)


__all__ = ["router"]
