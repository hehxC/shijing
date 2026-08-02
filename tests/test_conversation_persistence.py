import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.chat_conversation import ChatConversation
from app.models.chat_message import ChatMessage
from app.models.user import User
from app.service import conversation_service


class ConversationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        User.__table__.create(self.engine)
        ChatConversation.__table__.create(self.engine)
        ChatMessage.__table__.create(self.engine)
        self.Session = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        self.original_session_local = conversation_service.SessionLocal
        conversation_service.SessionLocal = self.Session
        with self.Session.begin() as db:
            db.add(User(id=1, username="tester", password_hash="hash"))

    def tearDown(self):
        conversation_service.SessionLocal = self.original_session_local
        self.engine.dispose()

    def begin(self, index: int = 1):
        return conversation_service.begin_user_turn(
            user_id=1,
            session_id="user:1:session-000000000000000000000000000001",
            client_session_id="session-000000000000000000000000000001",
            display_content=f"第 {index} 条庭院要求",
            request_text=f"第 {index} 条庭院要求",
            message_type="chat",
            style_id=None,
        )

    def test_failed_turn_keeps_only_user_message_and_is_excluded_from_model_history(self):
        user_message = self.begin()
        conversation_service.fail_turn(user_message.id)

        payload = conversation_service.get_conversation(
            1, "session-000000000000000000000000000001"
        )
        self.assertEqual(1, len(payload["messages"]))
        self.assertEqual("user", payload["messages"][0]["role"])
        self.assertEqual("failed", payload["messages"][0]["status"])
        self.assertEqual(
            [],
            conversation_service.load_recent_model_history(
                "user:1:session-000000000000000000000000000001"
            ),
        )

    def test_only_latest_ten_complete_turns_are_loaded_for_model_context(self):
        for index in range(1, 12):
            user_message = self.begin(index)
            conversation_service.complete_turn(user_message.id, f"第 {index} 条完整回复")

        history = conversation_service.load_recent_model_history(
            "user:1:session-000000000000000000000000000001"
        )
        self.assertEqual(20, len(history))
        self.assertEqual("第 2 条庭院要求", history[0]["content"])
        self.assertEqual("第 11 条完整回复", history[-1]["content"])

        payload = conversation_service.get_conversation(
            1, "session-000000000000000000000000000001"
        )
        self.assertEqual(22, len(payload["messages"]))

    def test_title_is_created_from_first_message_and_can_be_renamed(self):
        self.begin()
        conversations = conversation_service.list_conversations(1)
        self.assertEqual("第 1 条庭院要求", conversations[0]["title"])

        renamed = conversation_service.rename_conversation(
            1,
            "session-000000000000000000000000000001",
            "  苏式庭院改造  ",
        )
        self.assertTrue(renamed)
        with self.Session() as db:
            row = db.scalar(select(ChatConversation))
            self.assertEqual("苏式庭院改造", row.title)
            self.assertTrue(row.title_manually_edited)

    def test_generated_url_is_scoped_to_the_client_conversation(self):
        self.assertEqual(
            "/api/conversations/session-id/generated/result.png",
            conversation_service.protected_generated_url(
                "user:1:session-id", "/static/generated/result.png"
            ),
        )


if __name__ == "__main__":
    unittest.main()
