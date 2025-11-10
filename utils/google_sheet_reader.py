import requests

def fetch_channels_from_google_sheet(sheet_id, api_key):
   url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/%27api%20call%27!A2:Z1000?key={api_key}"
    response = requests.get(url)
    data = response.json()
    rows = data.get("values", [])
    
    print("[debug] rows returned:", len(rows))
    print("[debug] first 3 rows:", rows[:3])

    header = rows[0]
    name_idx = header.index("Name")
    link_idx = header.index("Link")

    channel_data = []
    for row in rows[1:]:
        if len(row) > max(name_idx, link_idx):
            channel_data.append({
                "channel_name": row[name_idx],
                "channel_link": row[link_idx]
            })

    return channel_data
