"""存活与就绪健康检查。

- ``/health/live``：进程存活探针，不依赖任何外部组件；
- ``/health/ready``：就绪探针，检查数据库是否可用，供负载均衡器路由流量。

就绪探针只把数据库作为流量门槛：对象存储（S3/MinIO）在图片读写时按请求
失败并返回明确错误，不阻塞实例就绪，避免每次探针都打一次存储服务。
"""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.database import engine

router = APIRouter(tags=["health"])


@router.get("/health/live", include_in_schema=False)
def health_live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready", include_in_schema=False)
def health_ready(response: Response) -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "checks": {"database": "unavailable"}}
    return {"status": "ready", "checks": {"database": "ok"}}
