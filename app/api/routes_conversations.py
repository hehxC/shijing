import re
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.models.schemas import ConversationRename
from app.models.user import User
from app.service.auth_service import get_current_user
from app.service.conversation_service import (
    conversation_owns_generated_file,
    delete_conversation,
    generated_file_bytes,
    get_conversation,
    list_conversations,
    rename_conversation,
)


router = APIRouter(prefix="/api/conversations", tags=["conversations"])
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]{1,255}$")


@router.get("")
def read_conversations(current_user: User = Depends(get_current_user)):
    return list_conversations(current_user.id)


@router.get("/{client_session_id}")
def read_conversation(
    client_session_id: str,
    current_user: User = Depends(get_current_user),
):
    payload = get_conversation(current_user.id, client_session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="历史会话不存在")
    return payload


@router.patch("/{client_session_id}")
def patch_conversation(
    client_session_id: str,
    payload: ConversationRename,
    current_user: User = Depends(get_current_user),
):
    if not rename_conversation(current_user.id, client_session_id, payload.title):
        raise HTTPException(status_code=404, detail="历史会话不存在")
    return {"ok": True}


@router.delete("/{client_session_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_conversation(
    client_session_id: str,
    current_user: User = Depends(get_current_user),
):
    if not delete_conversation(current_user.id, client_session_id):
        raise HTTPException(status_code=404, detail="历史会话不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{client_session_id}/generated/{filename}")
def read_generated_image(
    client_session_id: str,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    if not _SAFE_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="图片不存在")
    if not conversation_owns_generated_file(current_user.id, client_session_id, filename):
        raise HTTPException(status_code=404, detail="图片不存在")
    data = generated_file_bytes(filename)
    if data is None:
        raise HTTPException(status_code=404, detail="图片不存在")
    mime_type = mimetypes.guess_type(filename)[0] or "image/png"
    return Response(content=data, media_type=mime_type)
