"""存量图片迁移：把 MySQL 里的 base64 图片落盘到图片存储，DB 只留对象 key。

运行方式（在项目根目录）：
    uv run python scripts/migrate_images.py

幂等：只处理"有旧数据且还没有对象 key"的行，可重复运行。
迁移对象：
    - materials.img → materials.image_key
    - design_reference_images.data_url → design_reference_images.object_key
"""

import mimetypes
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select  # noqa: E402

load_dotenv()

from app.database import SessionLocal  # noqa: E402
from app.models.design_reference_image import DesignReferenceImage  # noqa: E402
from app.models.material import Material  # noqa: E402
from app.service.image_store import data_url_to_bytes, get_image_store  # noqa: E402


def _key_for(kind: str, data_url: str) -> str:
    """按 类型/日期/uuid 生成对象 key。"""
    data, mime_type = data_url_to_bytes(data_url)
    extension = mimetypes.guess_extension(mime_type) or ".jpg"
    if extension in {".jpe", ".jpeg"}:
        extension = ".jpg"
    key = f"{kind}/{datetime.now().strftime('%Y%m%d')}/{uuid4().hex}{extension}"
    get_image_store().save(key, data, content_type=mime_type)
    return key


def migrate_materials(db) -> tuple[int, int]:
    """迁移 materials.img → image_key，返回 (成功数, 失败数)。"""
    rows = db.scalars(
        select(Material).where(Material.image_key.is_(None))
    ).all()
    ok = failed = 0
    for row in rows:
        legacy = getattr(row, "img", None)
        if not legacy:
            continue  # 没有旧图，跳过
        try:
            row.image_key = _key_for("materials", legacy)
            row.img = ""
            ok += 1
        except Exception:
            failed += 1
    return ok, failed


def migrate_design_images(db) -> tuple[int, int]:
    """迁移 design_reference_images.data_url → object_key，返回 (成功数, 失败数)。"""
    rows = db.scalars(
        select(DesignReferenceImage).where(DesignReferenceImage.object_key.is_(None))
    ).all()
    ok = failed = 0
    for row in rows:
        legacy = getattr(row, "data_url", None)
        if not legacy:
            continue
        try:
            row.object_key = _key_for(row.kind or "designs", legacy)
            row.data_url = ""
            ok += 1
        except Exception:
            failed += 1
    return ok, failed


def main() -> None:
    """执行迁移并输出统计。"""
    with SessionLocal.begin() as db:
        materials_ok, materials_failed = migrate_materials(db)
        designs_ok, designs_failed = migrate_design_images(db)
    print(f"materials 迁移：成功 {materials_ok}，失败 {materials_failed}")
    print(f"design_reference_images 迁移：成功 {designs_ok}，失败 {designs_failed}")


if __name__ == "__main__":
    main()
