from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlalchemy import inspect, text
from starlette.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_design import router as design_router
from app.api.routes_materials import router as materials_router
from app.database import Base, engine
from app.models import material as _material_model  # noqa: F401
from app.models import chat_session_context as _chat_session_context_model  # noqa: F401
from app.models import chat_user_message as _chat_user_message_model  # noqa: F401
from app.models import user as _user_model  # noqa: F401
from app.models import design_reference_image as _design_reference_image_model  # noqa: F401
from app.service.design_session_service import cleanup_expired_design_assets

app = FastAPI()

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(design_router)
app.include_router(materials_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def create_tables():
    Base.metadata.create_all(bind=engine)
    ensure_chat_session_context_columns()
    ensure_chat_user_message_columns()
    cleanup_expired_design_assets()


def ensure_chat_session_context_columns():
    """兼容旧库表：create_all 不会给已存在的表自动增加新列。"""
    table_name = "chat_session_contexts"
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    statements = []
    if "reference_image_data_url" not in existing_columns:
        statements.append(
            "ALTER TABLE chat_session_contexts ADD COLUMN reference_image_data_url LONGTEXT NULL"
        )
    if "reference_image_request" not in existing_columns:
        statements.append(
            "ALTER TABLE chat_session_contexts ADD COLUMN reference_image_request TEXT NULL"
        )

    column_definitions = {
        "selected_style_id": "VARCHAR(64) NULL",
        "context_revision": "INT NOT NULL DEFAULT 0",
        "effect_revision": "INT NULL",
        "assets_expired_at": "DATETIME NULL",
    }
    for column_name, definition in column_definitions.items():
        if column_name not in existing_columns:
            statements.append(
                f"ALTER TABLE chat_session_contexts ADD COLUMN {column_name} {definition}"
            )

    if statements:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


def ensure_chat_user_message_columns():
    """兼容旧库表：create_all 不会给已存在的表自动增加新列。"""
    table_name = "chat_user_messages"
    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    statements = []
    if "image_size" not in existing_columns:
        statements.append(
            "ALTER TABLE chat_user_messages ADD COLUMN image_size INT NULL"
        )

    if statements:
        with engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))


@app.get("/")
async def root():
    return FileResponse("static/chat.html")
