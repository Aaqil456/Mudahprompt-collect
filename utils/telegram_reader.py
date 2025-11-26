from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument


def extract_channel_username(url: str) -> str:
    """
    Convert full t.me link to @username format.
    Example: https://t.me/MyChannel -> @MyChannel
    """
    return '@' + url.strip().rstrip('/').split('/')[-1]


async def fetch_latest_messages(api_id, api_hash, channel_username, limit: int = 1):
    """
    Fetch latest messages from a Telegram channel.

    Returns a list of dict:
    {
        "id": int,
        "text": str,
        "has_photo": bool,
        "photo": MessageMediaPhoto | None,
        "has_video": bool,
        "video": MessageMediaDocument | None,
        "media_group_id": int | None,
        "raw": telethon.tl.custom.message.Message,
        "date": ISO8601 string
    }

    - 'raw' is kept so main script can call client.download_media(raw, file=...)
    - 'media_group_id' lets you group multiple photos/videos into one batch
      (same concept as your FB script).
    """
    client = TelegramClient("telegram_session", api_id, api_hash)
    await client.start()

    messages = []

    async for message in client.iter_messages(channel_username, limit=limit):
        text = message.text or ""

        has_photo = isinstance(message.media, MessageMediaPhoto)
        has_video = False
        video_media = None

        # Detect video (document with video/* mime_type)
        if isinstance(message.media, MessageMediaDocument):
            mime = getattr(message.file, "mime_type", "") or ""
            if mime.startswith("video/") or "video" in mime:
                has_video = True
                video_media = message.media

        # Only keep messages that have something useful
        if text or has_photo or has_video:
            messages.append(
                {
                    "id": message.id,
                    "text": text,
                    "has_photo": has_photo,
                    "photo": message.media if has_photo else None,
                    "has_video": has_video,
                    "video": video_media,
                    "media_group_id": getattr(message, "media_group_id", None),
                    "raw": message,
                    "date": message.date.isoformat() if message.date else "",
                }
            )

    await client.disconnect()
    return messages
