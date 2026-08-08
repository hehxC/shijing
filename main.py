from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_design import router as design_router
from app.api.routes_materials import router as materials_router
from app.service.design_session_service import cleanup_expired_design_assets

app = FastAPI()

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(design_router)
app.include_router(materials_router)


@app.get("/static/generated/{asset_path:path}", include_in_schema=False)
def deny_public_generated_image(asset_path: str):
    raise HTTPException(status_code=404, detail="图片不存在")


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def startup():
    """启动时把数据库迁移到最新版本，并清理过期设计素材。"""
    run_database_migrations()
    cleanup_expired_design_assets()


def run_database_migrations() -> None:
    """执行 Alembic 迁移到最新版本。

    schema 统一由 Alembic 管理：新环境从零建表，旧环境增量迁移；
    已在最新版本时是幂等的空操作。
    """
    project_root = Path(__file__).resolve().parent
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@app.get("/")
async def root():
    return FileResponse("static/chat.html")
