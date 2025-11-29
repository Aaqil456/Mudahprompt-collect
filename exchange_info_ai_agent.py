import sys
import os
import asyncio

# Pastikan boleh import utils/* walaupun run dari GitHub Actions
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from telethon import TelegramClient

from utils.google_sheet_reader import fetch_channels_from_google_sheet
from utils.telegram_reader import extract_channel_username, fetch_latest_messages
from utils.ai_translator import translate_text_gemini
from utils.telegram_sender import (
    send_telegram_message_html,
    send_photo_to_telegram_channel,
    send_video_to_telegram_channel,
)
from utils.json_writer import save_results, load_posted_message_keys


async def main():
    # --- ENV VARS (pastikan semua ada) ---
    telegram_api_id = int(os.environ["TELEGRAM_API_ID"])
    telegram_api_hash = os.environ["TELEGRAM_API_HASH"]
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    google_sheet_api_key = os.environ["GOOGLE_SHEET_API_KEY"]

    # --- Dedupe: mesej yang dah pernah dipost ---
    # Kita guna message_key macam "@channel:12345"
    posted_messages = set(load_posted_message_keys() or [])
    result_output = []

    # channels_data expected daripada google_sheet_reader:
    # [
    #   {
    #       "channel_name": "...",
    #       "channel_link": "https://t.me/....",
    #       "channel_type": "Alpha" / "InfoFi" / "Dana Kripto" / etc (optional)
    #   },
    #   ...
    # ]
    channels_data = fetch_channels_from_google_sheet(sheet_id, google_sheet_api_key)

    for entry in channels_data:
        channel_link = entry["channel_link"]
        channel_type = entry.get("channel_type")  # e.g. "Alpha", "InfoFi", etc.
        channel_username = extract_channel_username(channel_link)

        print(f"\n📡 Processing channel: {channel_username} (Type: {channel_type})")

        # Ambil latest messages, kalau nak lebih/kurang boleh ubah limit
        messages = await fetch_latest_messages(
            telegram_api_id,
            telegram_api_hash,
            channel_username,
            limit=5,
        )

        for msg in messages:
            text = msg.get("text") or ""
            msg_id = msg["id"]
            # Unique key untuk setiap mesej dalam setiap channel
            msg_key = f"{channel_username}:{msg_id}"

            # --- DEDUPE BERDASARKAN MESSAGE ID (BUKAN TEXT) ---
            if msg_key in posted_messages:
                print(
                    f"⚠️ Skipping duplicate message ID {msg_id} "
                    f"from {channel_username} (key={msg_key})"
                )
                continue

            # --- Translate dengan Gemini (function kau sendiri) ---
            translated = translate_text_gemini(text)

            try:
                # === PRIORITY: VIDEO > PHOTO > TEXT ===

                if msg.get("has_video"):
                    # Download video dari channel sumber
                    video_path = f"video_{msg_id}.mp4"
                    async with TelegramClient(
                        "telegram_session", telegram_api_id, telegram_api_hash
                    ) as client:
                        await client.download_media(msg["raw"], video_path)

                    # Hantar ke channel kau dengan caption
                    send_video_to_telegram_channel(
                        video_path=video_path,
                        translated_caption=translated,
                        post_type=channel_type,  # [<b>Type</b>] tag dalam caption
                    )

                    # Cleanup file
                    if os.path.exists(video_path):
                        os.remove(video_path)

                elif msg.get("has_photo"):
                    # Download photo dari channel sumber
                    image_path = f"photo_{msg_id}.jpg"
                    async with TelegramClient(
                        "telegram_session", telegram_api_id, telegram_api_hash
                    ) as client:
                        await client.download_media(msg["raw"], image_path)

                    # Hantar photo + caption
                    send_photo_to_telegram_channel(
                        image_path=image_path,
                        translated_caption=translated,
                        post_type=channel_type,  # [<b>Type</b>] tag dalam caption
                    )

                    # Cleanup file
                    if os.path.exists(image_path):
                        os.remove(image_path)

                else:
                    # TEXT ONLY
                    send_telegram_message_html(
                        translated_text=translated,
                        post_type=channel_type,  # [<b>Type</b>] line atas
                    )

                # Mark mesej ni dah dipost (tak kira ada text atau tidak)
                posted_messages.add(msg_key)

                # Log dalam results.json (via json_writer.save_results)
                result_output.append(
                    {
                        "channel_link": channel_link,
                        "channel_type": channel_type,
                        "channel_username": channel_username,
                        "original_text": text,
                        "translated_text": translated,
                        "date": msg.get("date"),
                        "message_id": msg_id,
                        "message_key": msg_key,
                    }
                )

            except Exception as e:
                print(
                    f"❌ Error while processing message {msg_id} "
                    f"from {channel_username}: {e}"
                )

    # Simpan semua result dalam results.json (append style)
    if result_output:
        save_results(result_output)


if __name__ == "__main__":
    asyncio.run(main())
