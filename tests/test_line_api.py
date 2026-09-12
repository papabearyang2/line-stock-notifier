import base64
import hashlib
import hmac

from app.config import get_settings
from app.line_api import (
    bot_is_mentioned,
    conversation_id,
    verify_signature,
    without_bot_mention,
)


def test_signature_verification(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "line_channel_secret", "secret")
    body = b'{"events":[]}'
    signature = base64.b64encode(hmac.new(b"secret", body, hashlib.sha256).digest()).decode()
    assert verify_signature(body, signature)
    assert not verify_signature(body + b" ", signature)


def test_conversation_id_uses_user_for_private_chat():
    assert conversation_id({"type": "user", "userId": "U123"}) == "U123"


def test_conversation_id_uses_group_id_for_group_chat():
    assert (
        conversation_id({"type": "group", "groupId": "C123", "userId": "U123"})
        == "C123"
    )


def test_conversation_id_uses_room_id_for_multi_person_chat():
    assert (
        conversation_id({"type": "room", "roomId": "R123", "userId": "U123"})
        == "R123"
    )


def test_detects_mention_to_bot():
    message = {
        "text": "@股票萬事通 清單",
        "mention": {
            "mentionees": [
                {"index": 0, "length": 6, "type": "user", "isSelf": True}
            ]
        },
    }

    assert bot_is_mentioned(message)
    assert without_bot_mention(message) == "清單"


def test_mention_to_another_user_does_not_trigger_bot():
    message = {
        "text": "@小明 清單",
        "mention": {
            "mentionees": [
                {"index": 0, "length": 3, "type": "user", "isSelf": False}
            ]
        },
    }

    assert not bot_is_mentioned(message)
