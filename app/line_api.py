import base64
import hashlib
import hmac

import httpx

from app.config import get_settings


def conversation_id(source: dict) -> str | None:
    source_type = source.get("type")
    if source_type == "group":
        return source.get("groupId")
    if source_type == "room":
        return source.get("roomId")
    return source.get("userId")


def bot_is_mentioned(message: dict) -> bool:
    mentionees = message.get("mention", {}).get("mentionees", [])
    return any(mentionee.get("isSelf") is True for mentionee in mentionees)


def without_bot_mention(message: dict) -> str:
    text = message.get("text", "")
    mentionee = next(
        (
            item
            for item in message.get("mention", {}).get("mentionees", [])
            if item.get("isSelf") is True and item.get("index") == 0
        ),
        None,
    )
    if not mentionee:
        return text
    return text[mentionee.get("length", 0) :].lstrip()


def verify_signature(raw_body: bytes, signature: str) -> bool:
    configured_secret = get_settings().line_channel_secret
    if not configured_secret:
        return False
    secret = configured_secret.encode("utf-8")
    digest = hmac.new(secret, raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


def text_message(text: str) -> dict:
    return {"type": "text", "text": text[:5000]}


def image_message(url: str) -> dict:
    return {"type": "image", "originalContentUrl": url, "previewImageUrl": url}


async def reply(reply_token: str, messages: list[dict]) -> None:
    await _post(
        "https://api.line.me/v2/bot/message/reply",
        {"replyToken": reply_token, "messages": messages[:5]},
    )


async def push(line_target_id: str, messages: list[dict]) -> None:
    await _post(
        "https://api.line.me/v2/bot/message/push",
        {"to": line_target_id, "messages": messages[:5]},
    )


async def _post(url: str, payload: dict) -> None:
    token = get_settings().line_channel_access_token
    if not token:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN 尚未設定")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
