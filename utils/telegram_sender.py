import os
import re
import html
import json
import requests
import google.generativeai as genai

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API_BASE = "https://api.telegram.org"
MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024  # official caption limit

# We'll keep some margin so type tag + HTML don't push us over hard limit
TEXT_SPLIT_LIMIT = MESSAGE_LIMIT - 200     # safe budget for text chunks
CAPTION_SPLIT_LIMIT = CAPTION_LIMIT - 50   # safe budget for caption chunk

# -------------------- Gemini config --------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ GEMINI_API_KEY not set. Splitter will fall back to local heuristic only.")


def _call_gemini(prompt: str) -> str | None:
    """
    Low-level Gemini wrapper.
    Returns response text or None on error.
    """
    if not GEMINI_API_KEY:
        return None

    try:
        model = genai.GenerativeModel(GEMINI_MODEL_NAME)
        resp = model.generate_content(prompt)
        if hasattr(resp, "text") and resp.text:
            return resp.text.strip()
        if getattr(resp, "candidates", None):
            parts = resp.candidates[0].content.parts
            txt = "".join(p.text for p in parts if hasattr(p, "text"))
            return txt.strip()
        return None
    except Exception as e:
        print(f"❌ Gemini call error in telegram_sender: {e}")
        return None


# -------------------- Markdown → HTML (safe subset) --------------------

MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
MD_BOLD_RE = re.compile(r'(\*\*|__)(.+?)\1', re.DOTALL)
MD_ITALIC_RE = re.compile(
    r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)',
    re.DOTALL
)


def render_html_with_basic_md(text: str) -> str:
    """
    Convert a minimal Markdown subset to Telegram-safe HTML:
      - [label](url)  -> <a href="url">label</a>
      - **bold**/__bold__ -> <b>bold</b>
      - *italic*/_italic_  -> <i>italic</i>
    Everything else is HTML-escaped. Labels/hrefs are escaped too.
    """
    if not text:
        return ""

    token_re = re.compile(
        r'(\[([^\]]+)\]\((https?://[^)\s]+)\)|'          # [label](url)
        r'(\*\*|__)(.+?)\4|'                             # **bold** or __bold__
        r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|'          # *italic*
        r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_))',               # _italic_
        re.DOTALL
    )

    out = []
    i = 0
    for m in token_re.finditer(text):
        out.append(html.escape(text[i:m.start()]))

        full = m.group(1)
        link_label = m.group(2)
        link_href = m.group(3)
        bold_delim = m.group(4)
        bold_inner = m.group(5)
        italic_star_inner = m.group(6)
        italic_underscore_inner = m.group(7)

        if link_label and link_href:
            out.append(
                f'<a href="{html.escape(link_href, quote=True)}">'
                f'{html.escape(link_label)}</a>'
            )
        elif bold_delim and bold_inner is not None:
            out.append(f'<b>{html.escape(bold_inner)}</b>')
        elif italic_star_inner is not None:
            out.append(f'<i>{html.escape(italic_star_inner)}</i>')
        elif italic_underscore_inner is not None:
            out.append(f'<i>{html.escape(italic_underscore_inner)}</i>')
        else:
            out.append(html.escape(full))

        i = m.end()

    out.append(html.escape(text[i:]))
    return "".join(out)


# -------------------- Local heuristic splitter (fallback) --------------------

def _split_for_telegram_raw(text: str, limit: int) -> list[str]:
    """
    Pure local splitter (no Gemini).
    Preference order:
      1. Double newline (\n\n)
      2. Single newline (\n)
      3. Sentence end (. / ! / ? + space)
      4. Space
      5. Hard cut at 'limit'
    """
    if text is None:
        return [""]
    text = text or ""
    if len(text) <= limit:
        return [text]

    parts = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break

        chunk = remaining[:limit]
        split_idx = -1

        # 1. Try double newline
        idx = chunk.rfind("\n\n")
        if idx != -1 and idx > limit * 0.4:
            split_idx = idx + 2

        # 2. Try single newline
        if split_idx == -1:
            idx = chunk.rfind("\n")
            if idx != -1 and idx > limit * 0.4:
                split_idx = idx + 1

        # 3. Try sentence end (. ! ? + space)
        if split_idx == -1:
            for ender in [". ", "! ", "? "]:
                idx = chunk.rfind(ender)
                if idx != -1 and idx > limit * 0.4:
                    split_idx = idx + len(ender)
                    break

        # 4. Try last space
        if split_idx == -1:
            idx = chunk.rfind(" ")
            if idx != -1 and idx > limit * 0.4:
                split_idx = idx + 1

        # 5. Fallback: hard cut
        if split_idx == -1:
            split_idx = limit

        part = remaining[:split_idx].rstrip()
        parts.append(part)
        remaining = remaining[split_idx:].lstrip()

    return [p[:limit] for p in parts]


# -------------------- Gemini-powered splitter (no rewriting) --------------------

def _split_with_gemini(text: str, limit: int) -> list[str] | None:
    """
    Ask Gemini to split the text into chunks WITHOUT changing any words.

    - Text MUST remain exactly the same when chunks are concatenated.
    - Gemini only decides where to break (sentence boundaries / newlines).
    - Each chunk must be <= limit characters.

    Returns:
      list of chunks on success,
      None if anything looks wrong (then we fallback to local splitter).
    """
    if not GEMINI_API_KEY:
        return None

    text = text or ""
    if len(text) <= limit:
        return [text]

    prompt = f"""
You are helping split a Telegram message.

You are given a text and a maximum character limit per segment: {limit}.

Your task:
1. Split the text into multiple segments.
2. Each segment MUST be a contiguous substring of the original text.
3. DO NOT change, rewrite, translate, or remove any words or characters.
   You are only allowed to INSERT SPLIT POINTS between characters.
4. Splits should happen ONLY:
   - at the end of sentences (right after '.', '!', or '?'), OR
   - at existing newline characters.
5. Each segment MUST have length <= {limit} characters.
6. When all segments are concatenated in order, they MUST reconstruct
   the original text exactly, character for character.

Output format (VERY IMPORTANT):
- Return ONLY a valid JSON array of strings.
- Example: ["segment 1", "segment 2", "segment 3"]
- Do NOT add comments, explanations, or any text outside the JSON.

Text to split:
\"\"\"{text}\"\"\"
"""

    raw = _call_gemini(prompt)
    if not raw:
        return None

    try:
        chunks = json.loads(raw)
        if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
            print("❌ Gemini splitter: response is not a list of strings, falling back.")
            return None

        # Check concatenation matches original (no words changed)
        joined = "".join(chunks)
        if joined != text:
            print("❌ Gemini splitter: concatenated chunks != original text, falling back.")
            return None

        # Check each segment length
        for c in chunks:
            if len(c) > limit:
                print("❌ Gemini splitter: a chunk exceeds limit, falling back.")
                return None

        return chunks

    except json.JSONDecodeError:
        print("❌ Gemini splitter: invalid JSON, falling back.")
        return None
    except Exception as e:
        print(f"❌ Gemini splitter: unexpected error {e}, falling back.")
        return None


def split_text_with_gemini_or_fallback(text: str, limit: int) -> list[str]:
    """
    Main splitter used by sender:
      - First try Gemini-based splitting (no rewriting).
      - If anything fails, use local heuristic splitter.
    """
    text = text or ""
    if len(text) <= limit:
        return [text]

    chunks = _split_with_gemini(text, limit)
    if chunks is not None:
        return chunks

    # Fallback if Gemini fails / missing
    return _split_for_telegram_raw(text, limit)


# -------------------- Public send functions --------------------

def send_telegram_message_html(
    translated_text: str,
    exchange_name: str | None = None,
    referral_link: str | None = None,
    post_type: str | None = None,
):
    """
    Sends a (possibly long) message with Telegram HTML parse_mode.

    Behaviour:
      - Uses Gemini to choose split points (so we don't cut mid-sentence),
        WITHOUT changing any words.
      - Falls back to a local splitter if Gemini fails or is missing.
      - Adds [<b>Type</b>] on its own line at the top of the FIRST chunk only.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return []

    raw_chunks = split_text_with_gemini_or_fallback(
        translated_text or "",
        TEXT_SPLIT_LIMIT,
    )
    url = f"{API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    results = []

    for i, raw_chunk in enumerate(raw_chunks, 1):
        safe_html = render_html_with_basic_md(raw_chunk)

        # Insert type tag AFTER conversion so <b> isn't escaped
        if post_type and i == 1:
            type_tag = f"[<b>{html.escape(post_type)}</b>]\n\n"
            safe_html = type_tag + safe_html

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": safe_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            r = requests.post(url, json=payload, timeout=20)
            results.append(r.json())
            if r.ok and r.json().get("ok"):
                print(
                    f"✅ Telegram message part {i}/{len(raw_chunks)} sent "
                    f"(raw-len={len(raw_chunk)})."
                )
            else:
                print(
                    f"❌ Telegram send error part {i}/{len(raw_chunks)}: {r.text}"
                )
        except Exception as e:
            print(f"❌ Telegram send exception part {i}/{len(raw_chunks)}: {e}")

    return results


def send_photo_to_telegram_channel(
    image_path: str,
    translated_caption: str,
    exchange_name: str | None = None,
    referral_link: str | None = None,
    post_type: str | None = None,
):
    """
    Sends a photo with caption.

    Behaviour:
      - Uses Gemini splitter (no rewriting) to get caption chunks within CAPTION_SPLIT_LIMIT.
      - First chunk is used as the photo caption.
      - Remaining chunks (if any) are sent as follow-up text messages (without repeating [Type]).
      - [<b>Type</b>] goes on its own line at the top of the caption only.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return None

    caption_text = translated_caption or ""

    # Split caption into chunks using Gemini or fallback
    caption_chunks = split_text_with_gemini_or_fallback(
        caption_text,
        CAPTION_SPLIT_LIMIT,
    )

    head_raw = caption_chunks[0]
    tail_chunks = caption_chunks[1:] if len(caption_chunks) > 1 else []

    # Extra safety: enforce hard caption limit
    if len(head_raw) > CAPTION_LIMIT:
        head_raw = head_raw[:CAPTION_LIMIT]

    caption_head_html = render_html_with_basic_md(head_raw)

    # Insert type tag only once, at top of the caption
    if post_type:
        type_tag = f"[<b>{html.escape(post_type)}</b>]\n\n"
        caption_head_html = type_tag + caption_head_html

    url = f"{API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    try:
        with open(image_path, "rb") as photo_file:
            files = {"photo": photo_file}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption_head_html,
                "parse_mode": "HTML",
            }
            r = requests.post(url, data=data, files=files, timeout=30)

        if r.ok and r.json().get("ok"):
            print(f"✅ Photo sent. Caption raw-len={len(head_raw)}.")
        else:
            print(f"❌ Failed to send photo: {r.text}")

        # Send any remaining caption chunks as normal messages (no type repeated)
        for chunk in tail_chunks:
            send_telegram_message_html(
                translated_text=chunk,
                exchange_name=exchange_name,
                referral_link=referral_link,
                post_type=None,
            )

        return r.json()
    except FileNotFoundError:
        print(f"❌ Image not found: {image_path}")
    except Exception as e:
        print(f"❌ Telegram photo send exception: {e}")

    return None


def send_video_to_telegram_channel(
    video_path: str,
    translated_caption: str,
    exchange_name: str | None = None,
    referral_link: str | None = None,
    post_type: str | None = None,
):
    """
    Sends a video with caption.

    Behaviour:
      - Same style as send_photo_to_telegram_channel:
        * Split caption with Gemini/fallback into CAPTION_SPLIT_LIMIT chunks.
        * First chunk as video caption.
        * Remaining chunks as follow-up text messages.
        * [<b>Type</b>] only once at the top of the caption.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return None

    if not os.path.exists(video_path):
        print(f"❌ Video not found: {video_path}")
        return None

    caption_text = translated_caption or ""

    # Split caption into chunks
    caption_chunks = split_text_with_gemini_or_fallback(
        caption_text,
        CAPTION_SPLIT_LIMIT,
    )

    head_raw = caption_chunks[0]
    tail_chunks = caption_chunks[1:] if len(caption_chunks) > 1 else []

    # Enforce official caption limit
    if len(head_raw) > CAPTION_LIMIT:
        head_raw = head_raw[:CAPTION_LIMIT]

    caption_head_html = render_html_with_basic_md(head_raw)

    # Add type tag once
    if post_type:
        type_tag = f"[<b>{html.escape(post_type)}</b>]\n\n"
        caption_head_html = type_tag + caption_head_html

    url = f"{API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendVideo"

    try:
        with open(video_path, "rb") as video_file:
            files = {"video": video_file}
            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption_head_html,
                "parse_mode": "HTML",
            }
            r = requests.post(url, data=data, files=files, timeout=60)

        if r.ok and r.json().get("ok"):
            print(f"✅ Video sent. Caption raw-len={len(head_raw)}.")
        else:
            print(f"❌ Failed to send video: {r.text}")

        # Remainder caption as separate text messages
        for chunk in tail_chunks:
            send_telegram_message_html(
                translated_text=chunk,
                exchange_name=exchange_name,
                referral_link=referral_link,
                post_type=None,
            )

        return r.json()
    except Exception as e:
        print(f"❌ Telegram video send exception: {e}")

    return None
