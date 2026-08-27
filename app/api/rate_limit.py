"""FastAPI 路由共享的限流响应转换。"""

from dataclasses import asdict

from fastapi import HTTPException, status

from app.service.rate_limit_service import RateLimitDecision


def enforce_rate_limit(decision: RateLimitDecision) -> None:
    """超限时返回稳定 JSON 明细和标准 ``Retry-After`` 响应头。"""
    if decision.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=asdict(decision),
        headers={"Retry-After": str(decision.retry_after)},
    )
