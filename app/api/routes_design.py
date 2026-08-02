import re

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.garden_styles import get_garden_style
from app.models.schemas import (
    DesignImageCreate,
    DesignStyleUpdate,
    MaterialReferenceCreate,
    MaterialReferenceUpdate,
)
from app.models.user import User
from app.service.auth_service import get_current_user
from app.service.conversation_service import protected_generated_url
from app.service.design_session_service import (
    DesignSessionError,
    add_material_reference,
    cleanup_expired_design_assets,
    clear_material_references,
    delete_material_reference,
    delete_space_image,
    get_design_state,
    reset_design_session,
    save_selected_style,
    save_space_image,
    update_material_reference,
)


router = APIRouter(prefix="/api/design", tags=["design"])
_SESSION_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def design_session_id(
    raw_session_id: str = Header(alias="X-Design-Session"),
    current_user: User = Depends(get_current_user),
) -> str:
    token = raw_session_id.strip()
    if not _SESSION_TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(
            status_code=400,
            detail="设计会话标识必须是至少 32 位的随机令牌",
        )
    return f"user:{current_user.id}:{token}"


def _raise_bad_request(exc: DesignSessionError):
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/session")
def read_design_session(session_id: str = Depends(design_session_id)):
    cleanup_expired_design_assets()
    state = get_design_state(session_id)
    if state["generated_image_url"]:
        state["generated_image_url"] = protected_generated_url(
            session_id, state["generated_image_url"]
        )
    return state


@router.put("/space-image")
def put_space_image(
    payload: DesignImageCreate,
    session_id: str = Depends(design_session_id),
):
    try:
        return save_space_image(
            session_id,
            payload.image,
            original_name=payload.original_name,
            request="庭院空间图",
        )
    except DesignSessionError as exc:
        _raise_bad_request(exc)


@router.delete("/space-image", status_code=status.HTTP_204_NO_CONTENT)
def remove_space_image(session_id: str = Depends(design_session_id)):
    delete_space_image(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/material-images", status_code=status.HTTP_201_CREATED)
def post_material_image(
    payload: MaterialReferenceCreate,
    session_id: str = Depends(design_session_id),
):
    try:
        return add_material_reference(
            session_id,
            payload.image,
            original_name=payload.original_name,
            name=payload.name,
            usages=payload.usages,
        )
    except DesignSessionError as exc:
        _raise_bad_request(exc)


@router.patch("/material-images/{image_id}")
def patch_material_image(
    image_id: int,
    payload: MaterialReferenceUpdate,
    session_id: str = Depends(design_session_id),
):
    try:
        return update_material_reference(
            session_id, image_id, payload.name, payload.usages
        )
    except DesignSessionError as exc:
        _raise_bad_request(exc)


@router.delete("/material-images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_material_image(
    image_id: int,
    session_id: str = Depends(design_session_id),
):
    try:
        delete_material_reference(session_id, image_id)
    except DesignSessionError as exc:
        _raise_bad_request(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/material-images", status_code=status.HTTP_204_NO_CONTENT)
def remove_all_material_images(session_id: str = Depends(design_session_id)):
    clear_material_references(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/style", status_code=status.HTTP_204_NO_CONTENT)
def patch_style(
    payload: DesignStyleUpdate,
    session_id: str = Depends(design_session_id),
):
    if get_garden_style(payload.style_id) is None:
        raise HTTPException(status_code=400, detail="庭院风格不存在")
    save_selected_style(session_id, payload.style_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT)
def reset_design(session_id: str = Depends(design_session_id)):
    reset_design_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
