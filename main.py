import hashlib
import hmac
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

load_dotenv()

import db  # noqa: E402
import logic  # noqa: E402

VERIFY_TOKEN = os.environ["VERIFY_TOKEN"]
ACCESS_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")  # optional while testing
GRAPH_VERSION = os.getenv("GRAPH_VERSION", "v21.0")
# HR's WhatsApp number(s), international format without +, comma separated
HR_NUMBERS = {n.strip() for n in os.getenv("HR_NUMBERS", "").split(",") if n.strip()}

app = FastAPI()
db.init()
_seen_ids = set()  # Meta retries webhooks; don't process the same message twice


async def send_text(to: str, body: str) -> None:
    """Free-form text; only works inside the 24h window after the person messaged us."""
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print(f"SEND skipped (no credentials yet) -> {to}: {body}")
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
    """Meta calls this once when the webhook is saved in the dashboard."""
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


async def handle_hr(sender: str, text: str) -> str:
    lower = text.strip().lower()
    if lower.startswith("menu"):
        raw = text.strip()[4:].lstrip(" :\n")
        if not raw:
            return "Send it like this:\nMenu: <paste today's menu>"
        items = await run_in_threadpool(logic.set_menu, raw)
        return "Menu saved \n\n" + logic.format_menu(items) + "\n\nReply 'summary' any time to see orders so far."
    if lower.startswith("summary"):
        return await run_in_threadpool(logic.build_summary)
    return "HR commands:\n• Menu: <menu text>\n• Summary"


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
                if msg["id"] in _seen_ids:
                    continue
                _seen_ids.add(msg["id"])
                sender = msg["from"]
                name = names.get(sender) or sender
                if msg.get("type") != "text":
                    await send_text(sender, "Sorry, I can only read text messages. Please type your order.")
                    continue
                text = msg["text"]["body"]
                print(f"IN  {name} ({sender}): {text}")
                try:
                    if sender in HR_NUMBERS:
                        reply = await handle_hr(sender, text)
                    else:
                        await run_in_threadpool(db.upsert_staff, sender, name)
                        reply = await run_in_threadpool(logic.handle_staff_reply, sender, name, text)
                except Exception as e:  # never let one bad message crash the webhook
                    print("ERROR", repr(e))
                    reply = "Sorry, something went wrong on my side. Please try again or tell HR."
                await send_text(sender, reply)

    # Always return 200 quickly or Meta will retry.
    return {"status": "ok"}


@app.get("/")
async def health():
    return {"ok": True}
