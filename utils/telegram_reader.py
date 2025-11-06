from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto

def extract_channel_username(url):
    return '@' + url.strip().rstrip('/').split('/')[-1]

async def fetch_latest_messages(api_id, api_hash, channel_username, limit=1):
    client = TelegramClient("telegram_session", api_id, api_hash)
    await client.start()
    messages = []

    async for message in client.iter_messages(channel_username, limit=limit):
        has_photo = isinstance(message.media, MessageMediaPhoto)
        if message.text or has_photo:
            messages.append({
                "id": message.id,
                "text": message.text or "",
                "has_photo": has_photo,
                "photo": message.media if has_photo else None,
                "raw": message,
                "date": str(message.date)
            })

    await client.disconnect()
    return messages
