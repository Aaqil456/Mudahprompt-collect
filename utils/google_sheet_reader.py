import requests
from requests.utils import quote

def fetch_channels_from_google_sheet(sheet_id: str, api_key: str):
    # Cover rows 1–1000, then find the header row that contains Name & Link
    range_name = "'api call'!A1:E1000"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(range_name, safe='')}"
    resp = requests.get(url, params={"key": api_key}, timeout=20)
    data = resp.json()
    rows = data.get("values") or []
    if not rows:
        return []

    header_idx = None
    for i, r in enumerate(rows):
        low = [c.strip().lower() for c in r]
        if "name" in low and "link" in low:
            header_idx = i
            header_low = low
            break
    if header_idx is None:
        return []

    name_idx = header_low.index("name")
    link_positions = [i for i,h in enumerate(header_low) if h == "link"]
    if not link_positions:
        return []
    link_idx = link_positions[0]   # use the first “Link” (column B)

    out = []
    for r in rows[header_idx+1:]:
        r = list(r) + [""] * (max(name_idx, link_idx) + 1)
        name = r[name_idx].strip()
        link = r[link_idx].strip()
        if name and link:
            out.append({"channel_name": name, "channel_link": link})
    return out
