from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import Material
from app.models.schemas import MaterialCreate, MaterialResponse


router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.get("", response_model=list[MaterialResponse])
def list_materials(db: Session = Depends(get_db)):
    return db.scalars(select(Material).order_by(Material.created_at.desc())).all()


@router.post("", response_model=MaterialResponse, status_code=status.HTTP_201_CREATED)
def create_material(payload: MaterialCreate, db: Session = Depends(get_db)):
    material = Material(**payload.model_dump())
    try:
        db.add(material)
        db.commit()
        db.refresh(material)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="材料保存失败，请检查数据库连接") from exc
    return material


@router.delete("/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_material(material_id: int, db: Session = Depends(get_db)):
    material = db.get(Material, material_id)
    if material is None:
        raise HTTPException(status_code=404, detail="材料不存在")
    try:
        db.delete(material)
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="材料删除失败") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
