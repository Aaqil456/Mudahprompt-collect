import requests

def fetch_channels_from_google_sheet(sheet_id, api_key):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/'api call'!A1:Z1000?key={api_key}"
    response = requests.get(url)
    data = response.json()
    rows = data.get("values", [])

    if not rows:
        return []

    header = rows[0]
    name_idx = header.index("Name")
    link_idx = header.index("Link")
    type_idx = header.index("Type")   # 👈 new

    channel_data = []
    for row in rows[1:]:
        if len(row) > max(name_idx, link_idx, type_idx):
            channel_data.append({
                "channel_name": row[name_idx],
                "channel_link": row[link_idx],
                "channel_type": row[type_idx],   # 👈 save Type too
            })

    return channel_data
