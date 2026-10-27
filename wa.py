"""Chatbots Africa WhatsApp API wrapper (outgoing messages only).
Docs: https://api.chatbotsafrica.com/developers/api-docs
Auth is an `apikey` field in the JSON body. Never log or commit the key."""
import os
import re

import httpx

BASE = os.getenv("CBA_BASE_URL", "https://api.chatbotsafrica.com/api/v1.0")
API_KEY = os.getenv("CBA_API_KEY", "")


def normalize_phone(raw):
    """International format, digits only, no '+'. Turns 0241234567 into 233241234567."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0") and len(d) == 10:
        d = "233" + d[1:]
    return d


async def _post(path, payload):
    if not API_KEY:
        print(f"SEND skipped (CBA_API_KEY not set) {path}: {payload}")
        return None
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{BASE}{path}", json={"apikey": API_KEY, **payload})
    print("CBA", path, r.status_code, r.text[:300])
    return r


async def send_text(to, body):
    """Free-form text. Only works inside the 24h window after the person messaged us."""
    return await _post("/send/message", {"destination": normalize_phone(to), "message": body})


async def send_template(to, template_id, placeholders):
    """Business-initiated message using a Meta-approved template."""
    return await _post(
        "/send/message/template",
        {
            "destination": normalize_phone(to),
            "template_id": int(template_id),
            "type": "text",
            "placeholder": list(placeholders),  # array, as the docs recommend
        },
    )
