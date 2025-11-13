import os
import re
import html
import requests
import google.generativeai as genai

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API_BASE = "https://api.telegram.org"
MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024  # official caption limit

# Safe budgets under the hard limits (room for type tag + HTML tags)
SAFE_TEXT_BODY = 3600       # < 4096
SAFE_CAPTION_BODY = 850     # < 1024

# -------------------- Gemini config --------------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    print("⚠️ GEMINI_API_KEY not set. telegram_sender will fall back to naive trimming.")


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
        # For safety: some clients use resp.text, others resp.candidates[0].content.parts
        if hasattr(resp, "text") and resp.text:
            return resp.text.strip()
        # Fallback manual extraction if needed
        if getattr(resp, "candidates", None):
            parts = resp.candidates[0].content.parts
            txt = "".join(p.text for p in parts if hasattr(p, "text"))
            return txt.strip()
        return None
    except Exception as e:
        print(f"❌ Gemini call error in telegram_sender: {e}")
        return None


# -------------------- Markdown → HTML (safe subset) --------------------

# [label](https://url)
MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^)\s]+)\)')
# **bold** or __bold__
MD_BOLD_RE = re.compile(r'(\*\*|__)(.+?)\1', re.DOTALL)
# *italic* or _italic_ (but not **bold**)
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
        # Escape the literal segment before the token
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


# -------------------- Gemini helper to avoid awkward splits --------------------

def compress_with_gemini_if_needed(text: str, max_chars: int) -> str:
    """
    If 'text' is longer than max_chars, call Gemini to rewrite/compress it so that:
      - It stays under max_chars.
      - It keeps core meaning.
      - It ends on a natural sentence boundary.
    Falls back to naive trimming if Gemini is not available or fails.
    """
    if not text:
        return ""

    text = text.strip()
    if len(text) <= max_chars:
        return text

    prompt = f"""
You are an assistant that rewrites text to fit inside a strict character limit.

Requirements:
1. Rewrite the text in the SAME LANGUAGE as the input (do not translate).
2. Keep the key information and important points but feel free to compress or summarise.
3. The final answer MUST be at most {max_chars} characters long.
4. The text MUST end at a natural sentence boundary. Do NOT cut mid-sentence.
5. Do NOT add any explanations, meta comments, or headings. Output only the final rewritten text.

Text:
\"\"\"{text}\"\"\"
"""

    rewritten = _call_gemini(prompt)

    if not rewritten:
        # Fallback: naive trim + try to cut at last sentence end/space
        trimmed = text[:max_chars]
        for ender in [". ", "! ", "? "]:
            idx = trimmed.rfind(ender)
            if idx != -1 and idx > max_chars * 0.4:
                return trimmed[: idx + len(ender)].strip()
        # fallback to last space
        idx = trimmed.rfind(" ")
        if idx != -1 and idx > max_chars * 0.4:
            return trimmed[:idx].strip()
        return trimmed.strip()

    rewritten = rewritten.strip()
    if len(rewritten) > max_chars:
        rewritten = rewritten[:max_chars]

        # Try to make the cut cleaner
        for ender in [". ", "! ", "? "]:
            idx = rewritten.rfind(ender)
            if idx != -1 and idx > max_chars * 0.4:
                rewritten = rewritten[: idx + len(ender)]
                break

    return rewritten.strip()


# -------------------- Smarter splitter (backup) --------------------

def _split_for_telegram_raw(text: str, limit: int) -> list[str]:
    """
    Split RAW TEXT (not HTML) into <=limit chunks.
    Preference order:
      1. Double newline (\n\n)
      2. Single newline (\n)
      3. Sentence end (. / ! / ? + space)
      4. Space
      5. Hard cut at 'limit' if needed (e.g., very long URL)

    With compress_with_gemini_if_needed(), this should rarely
    produce more than 1 chunk, but it's a safety net.
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


# -------------------- Public send functions --------------------

def send_telegram_message_html(
    translated_text: str,
    exchange_name: str | None = None,
    referral_link: str | None = None,
    post_type: str | None = None,
):
    """
    Sends a (possibly long) message with Telegram HTML parse_mode.
      - First uses Gemini to compress to SAFE_TEXT_BODY.
      - Then, as safety, splits if still >4096.
      - Adds [<b>Type</b>] on its own line at the top of the FIRST chunk only.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return []

    # 1) Let Gemini compress to a safe size (this prevents awkward mid-sentence cuts)
    safe_text = compress_with_gemini_if_needed(
        translated_text or "",
        max_chars=SAFE_TEXT_BODY,
    )

    # 2) Split as a backup (usually will return only 1 chunk)
    raw_chunks = _split_for_telegram_raw(safe_text, MESSAGE_LIMIT)
    url = f"{API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    results = []

    for i, raw_chunk in enumerate(raw_chunks, 1):
        safe_html = render_html_with_basic_md(raw_chunk)

        # Insert type tag AFTER HTML conversion so <b> isn't escaped
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
      - Uses Gemini to compress the caption so it fits under SAFE_CAPTION_BODY.
      - Adds [<b>Type</b>] on its own line at the top of the caption.
      - If somehow still longer than Telegram's 1024-char caption limit, sends
        the remainder as follow-up text (without repeating the type tag).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return None

    # 1) Compress caption using Gemini for natural ending & fewer splits
    safe_caption = compress_with_gemini_if_needed(
        translated_caption or "",
        max_chars=SAFE_CAPTION_BODY,
    )

    # 2) Split caption into head (caption) + optional tail (follow-up)
    if len(safe_caption) <= CAPTION_LIMIT:
        head_raw = safe_caption
        tail_raw = ""
    else:
        head_raw = safe_caption[:CAPTION_LIMIT]
        tail_raw = safe_caption[CAPTION_LIMIT:]

    caption_head_html = render_html_with_basic_md(head_raw)

    # Insert type tag only once, at the top of the caption
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

        # If there's any leftover text, send it as normal text messages
        if tail_raw:
            print(
                f"[INFO] Sending caption remainder as text "
                f"(raw-len={len(tail_raw)})."
            )
            send_telegram_message_html(
                translated_text=tail_raw,
                exchange_name=exchange_name,
                referral_link=referral_link,
                post_type=None,  # don't repeat type
            )

        return r.json()
    except FileNotFoundError:
        print(f"❌ Image not found: {image_path}")
    except Exception as e:
        print(f"❌ Telegram photo send exception: {e}")

    return None
