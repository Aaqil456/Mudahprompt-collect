import os
import re
import html
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

API_BASE = "https://api.telegram.org"
MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024  # safe caption limit for captions' VISIBLE text

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

    # We do one pass with a combined regex so we can escape 'between' parts safely
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
        # link pieces
        link_label = m.group(2)
        link_href = m.group(3)
        # bold pieces
        bold_delim = m.group(4)
        bold_inner = m.group(5)
        # italic pieces (two alternatives)
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
            # Fallback (shouldn't happen): escape token
            out.append(html.escape(full))

        i = m.end()

    # Tail after the last token
    out.append(html.escape(text[i:]))
    return "".join(out)


# -------------------- Splitter (split raw text, then render) --------------------

def _split_for_telegram_raw(text: str, limit: int) -> list[str]:
    """
    Split RAW TEXT (not HTML) into <=limit chunks by paragraphs/lines/words.
    We split raw text so we don't cut inside an HTML tag later.
    """
    if text is None:
        return [""]
    if len(text) <= limit:
        return [text]

    parts, current = [], []
    cur_len = 0

    # Prefer paragraph boundaries
    for para in text.split("\n\n"):
        chunk = para + "\n\n"
        if cur_len + len(chunk) <= limit:
            current.append(chunk)
            cur_len += len(chunk)
        else:
            if current:
                parts.append("".join(current).rstrip())
                current, cur_len = [], 0
            # If a paragraph itself is too large, break by lines then words
            if len(chunk) > limit:
                for line in chunk.split("\n"):
                    line_n = line + "\n"
                    if len(line_n) > limit:
                        words = line_n.split(" ")
                        buf, L = [], 0
                        for w in words:
                            w2 = w + " "
                            if L + len(w2) <= limit:
                                buf.append(w2)
                                L += len(w2)
                            else:
                                parts.append("".join(buf).rstrip())
                                buf, L = [w2], len(w2)
                        if buf:
                            parts.append("".join(buf).rstrip())
                    else:
                        if cur_len + len(line_n) <= limit:
                            current.append(line_n)
                            cur_len += len(line_n)
                        else:
                            parts.append("".join(current).rstrip())
                            current, cur_len = [line_n], len(line_n)
            else:
                current, cur_len = [chunk], len(chunk)

    if current:
        parts.append("".join(current).rstrip())

    # Final hard cap just in case
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
      - Converts **bold**, *italic*, [label](url) to <b>, <i>, <a>
      - Escapes everything else
      - Splits RAW text into 4096-safe chunks, then converts each chunk
      - Optionally prefixes the message with [Type] from Google Sheet
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return []

    base_text = translated_text or ""

    # Prefix with type once at the very beginning (e.g. [Alpha] ...)
    if post_type:
        base_text = f"[<b>{post_type}</b>]\n\n{base_text}"

    raw_chunks = _split_for_telegram_raw(base_text, MESSAGE_LIMIT)
    url = f"{API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    results = []
    for i, raw_chunk in enumerate(raw_chunks, 1):
        # Convert each chunk to HTML AFTER splitting to avoid breaking tags across messages
        safe_html = render_html_with_basic_md(raw_chunk)
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": safe_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,  # set True if you don't want previews
        }
        try:
            r = requests.post(url, json=payload, timeout=20)
            results.append(r.json())
            if r.ok and r.json().get("ok"):
                print(
                    f"✅ Telegram message part {i}/{len(raw_chunks)} sent "
                    f"(len={len(raw_chunk)} raw)."
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
    Sends a photo with caption (<=1024 VISIBLE chars). If caption is longer,
    sends the remainder as follow-up 4096-safe text messages.
      - Uses the same Markdown→HTML conversion
      - Splits RAW caption first, then converts each part
      - Optionally prefixes the caption with [Type]
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in environment.")
        return None

    raw_caption = translated_caption or ""

    # Prefix the caption with the type (only once, for the first chunk)
    if post_type:
        raw_caption = f"[<b>{post_type}</b>]\n\n{raw_caption}"

    # Split RAW caption by visible-text limit (safer than counting HTML chars)
    if len(raw_caption) <= CAPTION_LIMIT:
        head_raw = raw_caption
        tail_raw = ""
    else:
        head_raw = raw_caption[:CAPTION_LIMIT]
        tail_raw = raw_caption[CAPTION_LIMIT:]

    # Convert the head to HTML for caption
    caption_head_html = render_html_with_basic_md(head_raw)

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

        # Remainder of caption as regular messages (split raw -> then convert)
        if tail_raw:
            print(
                f"[INFO] Sending caption remainder as text "
                f"(raw-len={len(tail_raw)})."
            )
            # IMPORTANT: we don't pass post_type again, so the type tag
            # only appears once at the beginning.
            send_telegram_message_html(
                tail_raw,
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
