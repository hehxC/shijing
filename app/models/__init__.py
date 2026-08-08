"""统一导入全部 SQLAlchemy 模型，注册进 Base.metadata。

让 Alembic autogenerate、create_all 等能一次性看到所有表。
"""

from app.models.chat_conversation import ChatConversation  # noqa: F401
from app.models.chat_message import ChatMessage  # noqa: F401
from app.models.chat_session_context import ChatSessionContext  # noqa: F401
from app.models.design_reference_image import DesignReferenceImage  # noqa: F401
from app.models.material import Material  # noqa: F401
from app.models.user import User  # noqa: F401
