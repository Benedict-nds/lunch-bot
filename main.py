import hashlib
import hmac
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response

load_dotenv()

VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]
# These arrive after the other person finishes the Meta setup, so the server
# must still start (and pass webhook verification) without them.
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")  # optional while testing
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v21.0")

app = FastAPI()


async def send_text(to: str, body: str) -> None:
    """Send a free-form text message (only works inside the 24h window,
    i.e. after the person has messaged the bot)."""
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("SEND skipped: WHATSAPP_TOKEN / PHONE_NUMBER_ID not set yet")
        return
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=payload, headers=headers)
    print("SEND", r.status_code, r.text)


def valid_signature(raw_body: bytes, header: Optional[str]) -> bool:
    if not APP_SECRET:
        return True  # skipped in early dev; set APP_SECRET before going live
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


@app.get("/webhook")
async def verify(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Meta calls this once when you save the webhook in the dashboard."""
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive(request: Request):
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Bad signature")

    data = await request.json()
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {
                c.get("wa_id"): c.get("profile", {}).get("name")
                for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                sender = msg["from"]  # phone number in international format
                name = names.get(sender)
                if msg.get("type") == "text":
                    text = msg["text"]["body"]
                    print(f"IN  {name} ({sender}): {text}")
                    # Step 1 only: echo back to prove the round trip works.
                    await send_text(sender, f"Got it: {text}")
                else:
                    print(f"IN  {name} ({sender}): unsupported type {msg.get('type')}")

    # Always return 200 quickly or Meta will retry.
    return {"status": "ok"}


@app.get("/")
async def health():
    return {"ok": True}
# import hashlib
# import hmac
# import os
# from typing import Optional

# import httpx
# from dotenv import load_dotenv
# from fastapi import FastAPI, HTTPException, Query, Request, Response

# load_dotenv()

# VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]
# ACCESS_TOKEN = os.environ["WHATSAPP_TOKEN"]
# PHONE_NUMBER_ID = os.environ["PHONE_NUMBER_ID"]
# APP_SECRET = os.getenv("APP_SECRET", "")  # optional while testing
# GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v21.0")

# app = FastAPI()


# async def send_text(to: str, body: str) -> None:
#     """Send a free-form text message (only works inside the 24h window,
#     i.e. after the person has messaged the bot)."""
#     url = f"https://graph.facebook.com/{GRAPH_VERSION}/{PHONE_NUMBER_ID}/messages"
#     payload = {
#         "messaging_product": "whatsapp",
#         "to": to,
#         "type": "text",
#         "text": {"body": body},
#     }
#     headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
#     async with httpx.AsyncClient(timeout=15) as client:
#         r = await client.post(url, json=payload, headers=headers)
#     print("SEND", r.status_code, r.text)


# def valid_signature(raw_body: bytes, header: Optional[str]) -> bool:
#     if not APP_SECRET:
#         return True  # skipped in early dev; set APP_SECRET before going live
#     if not header or not header.startswith("sha256="):
#         return False
#     expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
#     return hmac.compare_digest(expected, header.split("=", 1)[1])


# @app.get("/webhook")
# async def verify(
#     mode: Optional[str] = Query(None, alias="hub.mode"),
#     token: Optional[str] = Query(None, alias="hub.verify_token"),
#     challenge: Optional[str] = Query(None, alias="hub.challenge"),
# ):
#     """Meta calls this once when you save the webhook in the dashboard."""
#     if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
#         return Response(content=challenge, media_type="text/plain")
#     raise HTTPException(status_code=403, detail="Verification failed")


# @app.post("/webhook")
# async def receive(request: Request):
#     raw = await request.body()
#     if not valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
#         raise HTTPException(status_code=403, detail="Bad signature")

#     data = await request.json()
#     for entry in data.get("entry", []):
#         for change in entry.get("changes", []):
#             value = change.get("value", {})
#             names = {
#                 c.get("wa_id"): c.get("profile", {}).get("name")
#                 for c in value.get("contacts", [])
#             }
#             for msg in value.get("messages", []):
#                 sender = msg["from"]  # phone number in international format
#                 name = names.get(sender)
#                 if msg.get("type") == "text":
#                     text = msg["text"]["body"]
#                     print(f"IN  {name} ({sender}): {text}")
#                     # Step 1 only: echo back to prove the round trip works.
#                     await send_text(sender, f"Got it: {text}")
#                 else:
#                     print(f"IN  {name} ({sender}): unsupported type {msg.get('type')}")

#     # Always return 200 quickly or Meta will retry.
#     return {"status": "ok"}


# @app.get("/")
# async def health():
#     return {"ok": True}
