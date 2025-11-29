import json
import os
from datetime import datetime

def save_results(messages, file_path="results.json"):
    """
    Save new messages into results.json.
    Works whether the file already contains a dict with 'messages'
    or a top-level list of messages.
    """
    existing_messages = []

    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

        # Handle both shapes: dict-with-'messages' and list
        if isinstance(data, dict):
            existing_messages = data.get("messages", [])
        elif isinstance(data, list):
            existing_messages = data

    # Combine existing and new messages
    combined_messages = existing_messages + messages

    # Save back in a consistent dict format
    data = {"timestamp": datetime.now().isoformat(), "messages": combined_messages}
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def load_posted_messages(file_path="results.json"):
    """
    OLD BEHAVIOUR (kept for compatibility):
    Load all 'original_text' entries from results.json.
    Works safely whether results.json is a dict with 'messages'
    or a list of messages.
    """
    if not os.path.exists(file_path):
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []

    # Handle both shapes: dict-with-'messages' and list
    if isinstance(data, dict):
        items = data.get("messages", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []

    posted_messages = []
    for msg in items:
        if isinstance(msg, dict) and "original_text" in msg:
            posted_messages.append(msg["original_text"])

    return posted_messages


def load_posted_message_keys(file_path="results.json"):
    """
    NEW FUNCTION for this project:
    Load list of unique 'message_key' values that have already been posted.

    Expected structure in each message (new entries from exchange_info_ai_agent.py):
        {
            "channel_link": "...",
            "channel_type": "...",
            "channel_username": "@channel",
            "original_text": "...",
            "translated_text": "...",
            "date": "...",
            "message_id": 12345,
            "message_key": "@channel:12345"
        }

    Behaviour:
      - Prefer msg["message_key"] if present.
      - As a fallback, if (channel_username, message_id) exist, reconstruct
        "@channel:12345".
      - Old entries without these fields are simply ignored (won't be deduped).
    """
    if not os.path.exists(file_path):
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []

    # Handle both shapes: dict-with-'messages' and list
    if isinstance(data, dict):
        items = data.get("messages", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []

    keys = []
    for msg in items:
        if not isinstance(msg, dict):
            continue

        # Preferred: explicit message_key
        if "message_key" in msg and isinstance(msg["message_key"], str):
            keys.append(msg["message_key"])
            continue

        # Fallback: reconstruct from channel_username + message_id if available
        channel_username = msg.get("channel_username")
        message_id = msg.get("message_id")

        if isinstance(channel_username, str) and isinstance(message_id, int):
            keys.append(f"{channel_username}:{message_id}")

    return keys
