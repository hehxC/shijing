from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.schemas import AuthResponse, UserCredentials, UserResponse
from app.models.user import User
from app.service.auth_service import create_access_token, get_current_user, hash_password, verify_password
from app.api.rate_limit import enforce_rate_limit
from app.service.rate_limit_service import (
    RateLimitService,
    RateLimitSettings,
    get_rate_limit_service,
)


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """使用直连客户端地址作为认证限流主体，避免默认信任伪造转发头。"""
    return request.client.host if request.client else "unknown"


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: UserCredentials,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimitService = Depends(get_rate_limit_service),
):
    settings = RateLimitSettings.from_environment()
    enforce_rate_limit(
        limiter.allow(
            f"register:ip:{_client_ip(request)}",
            limit=settings.register_per_hour,
            window_seconds=3_600,
            reason="注册请求过于频繁，请稍后再试",
        )
    )
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="注册失败，请检查数据库连接") from exc

    return AuthResponse(access_token=create_access_token(user), user=user)


@router.post("/login", response_model=AuthResponse)
def login(
    payload: UserCredentials,
    request: Request,
    db: Session = Depends(get_db),
    limiter: RateLimitService = Depends(get_rate_limit_service),
):
    settings = RateLimitSettings.from_environment()
    enforce_rate_limit(
        limiter.allow(
            f"login:ip:{_client_ip(request)}",
            limit=settings.login_per_minute,
            window_seconds=60,
            reason="登录请求过于频繁，请稍后再试",
        )
    )
    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="用户已停用")

    return AuthResponse(access_token=create_access_token(user), user=user)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user
