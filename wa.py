import os

import httpx

from phone import normalize_phone  

BASE = os.getenv("CBA_BASE_URL", "https://api.chatbotsafrica.com/api/v1.0")
API_KEY = os.getenv("CBA_API_KEY", "")


async def _post(path, payload):
    if not API_KEY:
        print(f"SEND skipped (CBA_API_KEY not set) {path}: {payload}")
        return None
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BASE}{path}", json={"apikey": API_KEY, **payload})
    print("CBA", path, r.status_code, r.text[:300])
    return r


async def send_text(to, body):
    #Free-form text. Only works inside the 24h window after the person messaged us
    return await _post("/send/message", {"destination": normalize_phone(to), "message": body})


async def send_template(to, template_id, placeholders):
    return await _post(
        "/send/message/template",
        {
            "destination": normalize_phone(to),
            "template_id": int(template_id),
            "type": "text",
            "placeholder": list(placeholders),  
        },
    )
