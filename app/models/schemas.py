from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    message: str = ""
    display_message: str | None = Field(default=None, max_length=20_000)
    message_type: str = Field(default="chat", pattern="^(chat|recognize|effect|quote)$")
    session_id: str | None = Field(default=None, max_length=64)
    style_id: str | None = Field(default=None, max_length=64)
    generate_effect_image: bool = False


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class DesignImageCreate(BaseModel):
    image: str
    original_name: str | None = Field(default=None, max_length=255)


class MaterialReferenceCreate(DesignImageCreate):
    name: str | None = Field(default=None, max_length=100)
    usages: list[str] = Field(default_factory=list, max_length=7)


class MaterialReferenceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    usages: list[str] = Field(default_factory=list, max_length=7)


class DesignStyleUpdate(BaseModel):
    style_id: str = Field(min_length=1, max_length=64)


class UserCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class MaterialBase(BaseModel):
    """材料公共字段：创建时 img 是 data URL，响应时 img 是图片 URL。"""

    material: str = Field(min_length=1, max_length=40)
    color: str | None = Field(default=None, max_length=30)
    spec: str | None = Field(default=None, max_length=50)
    price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    unit: str | None = Field(default=None, max_length=20)
    cat: str = Field(default="石材", min_length=1, max_length=30)
    desc: str | None = Field(default=None, max_length=2000)
    img: str = Field(min_length=1)

    @field_validator("material", "color", "spec", "unit", "cat", "desc", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class MaterialCreate(MaterialBase):
    @field_validator("img")
    @classmethod
    def validate_image(cls, value: str) -> str:
        if not value.startswith("data:image/"):
            raise ValueError("图片必须是有效的 Data URL")
        return value


class MaterialResponse(MaterialBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
