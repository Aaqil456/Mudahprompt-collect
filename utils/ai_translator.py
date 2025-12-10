import os
import time
import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-2.5-flash"  # current, fast & cheap. Change to gemini-2.5-pro if you prefer.

def translate_text_gemini(text: str, model: str = GEMINI_MODEL) -> str:
    """
    Translates `text` to Malay using Google Gemini API (Developer API).
    Returns translated text or "" on failure.
    """
    if not text or not isinstance(text, str) or not text.strip():
        print(f"[Warning] Empty or invalid text received for translation: {text}")
        return ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,  # <-- use header, not ?key=
    }

    prompt = (
        "Translate this text into Malay.\n\n"
        "Only return the translated text without any explanation.\n"
        "Use natural, conversational, friendly Malaysian Malay — like how a friend shares info.\n"
        "Keep it simple, relaxed, and easy to understand.\n"
        "Avoid exaggerated slang or interjections (e.g., 'Eh', 'Korang', 'Woi', 'Wooohooo').\n"
        "No shouting words or unnecessary excitement.\n"
        "Keep it informative, approachable, and casual — but clean and neutral.\n"
        "Do not use emojis unless they appear in the original text.\n"
        "for spesific word which is Market Events translate it into Update Pasaran \n"
        "for spesific word which is Top Mindshare Gainers translate it into Projek Crypto Viral Hari Ini \n"
        "for spesific word which is ref translate it into sumber, for example ref0, ref1  ref2 is SUMBER: 0 1 2  \n"
        "which mean for spesific word which is ref translate it into sumber, the hyperlink is in each number after the word SUMBER  \n"
        "Do not translate brand names or product names.\n\n"
        f"Text:\n{text}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        # Optional: you can guide style further
        "generationConfig": {
            "temperature": 0.2
        }
    }

    # Retry with exponential backoff for transient errors
    retries = 5
    backoff = 2
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            # If you want to see the exact error body on non-2xx:
            if not resp.ok:
                # Helpful debug print
                try:
                    print(f"[Error] HTTP {resp.status_code}: {resp.text[:500]}")
                except Exception:
                    pass
                resp.raise_for_status()

            data = resp.json()

            # Robust parse: candidates[0].content.parts[*].text
            candidates = data.get("candidates", [])
            if not candidates:
                print(f"[Warning] No candidates returned on attempt {attempt}.")
            else:
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    t = p.get("text", "").strip()
                    if t:
                        print(f"[Success] Translation completed for: {text[:60]}...")
                        return t

            print(f"[Warning] Empty translation on attempt {attempt}. Retrying...")
        except requests.exceptions.RequestException as e:
            print(f"[Error] Attempt {attempt} - Translation failed: {e}")
        time.sleep(backoff ** attempt)

    print(f"[Error] All attempts failed to translate: {text[:60]}...")
    return ""
