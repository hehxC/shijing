import mimetypes
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import Material
from app.models.schemas import MaterialCreate, MaterialResponse
from app.service.image_store import data_url_to_bytes, get_image_store


router = APIRouter(prefix="/api/materials", tags=["materials"])


def _material_image_bytes(material: Material) -> bytes | None:
    """材料的图片字节：优先从对象存储读，旧数据回退 img 列。"""
    if material.image_key:
        try:
            return get_image_store().get(material.image_key)
        except (OSError, ValueError):
            return None
    legacy = getattr(material, "img", None)
    if legacy:
        try:
            data, _ = data_url_to_bytes(legacy)
            return data
        except ValueError:
            return None
    return None


def _material_image_url(material: Material) -> str:
    """材料的展示图 URL：前端 <img src> 按需加载，不再传输 data URL。"""
    return f"/api/materials/{material.id}/image"


def _serialize_material(material: Material) -> dict:
    """把材料 ORM 对象序列化成 API 响应（img 从存储动态构建）。"""
    return {
        "id": material.id,
        "material": material.material,
        "color": material.color,
        "spec": material.spec,
        "price": material.price,
        "unit": material.unit,
        "cat": material.cat,
        "desc": material.desc,
        "img": _material_image_url(material),
        "created_at": material.created_at,
    }


@router.get("", response_model=list[MaterialResponse])
def list_materials(db: Session = Depends(get_db)):
    rows = db.scalars(select(Material).order_by(Material.created_at.desc())).all()
    return [_serialize_material(row) for row in rows]


@router.post("", response_model=MaterialResponse, status_code=status.HTTP_201_CREATED)
def create_material(payload: MaterialCreate, db: Session = Depends(get_db)):
    # 前端传 data URL，落盘到图片存储，DB 只存 key
    data, mime_type = data_url_to_bytes(payload.img)
    extension = mimetypes.guess_extension(mime_type) or ".jpg"
    image_key = get_image_store().save(
        f"materials/{datetime.now().strftime('%Y%m%d')}/{uuid4().hex}{extension}",
        data,
        content_type=mime_type,
    )
    material = Material(
        **payload.model_dump(exclude={"img"}),
        image_key=image_key,
    )
    try:
        db.add(material)
        db.commit()
        db.refresh(material)
    except SQLAlchemyError as exc:
        get_image_store().delete(image_key)
        db.rollback()
        raise HTTPException(status_code=500, detail="材料保存失败，请检查数据库连接") from exc
    return _serialize_material(material)


@router.get("/{material_id}/image")
def read_material_image(material_id: int, db: Session = Depends(get_db)):
    """读取材料图片：从存储返回字节，带缓存头（材料图变更不频繁）。"""
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    data = _material_image_bytes(material)
    if data is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    mime_type = mimetypes.guess_type(material.image_key or "")[0] or "image/jpeg"
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(material_id: int, db: Session = Depends(get_db)):
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="材料不存在")
    image_key = material.image_key
    try:
        db.delete(material)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="材料删除失败") from exc
    if image_key:
        get_image_store().delete(image_key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
