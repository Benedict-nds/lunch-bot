import asyncio
import csv
import hashlib
import hmac
import json
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

load_dotenv()

import db  # noqa: E402
import logic  # noqa: E402
import wa  # noqa: E402

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
APP_SECRET = os.getenv("APP_SECRET", "")          # only if the provider signs webhooks Meta-style
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  
HR_NUMBERS = {wa.normalize_phone(n) for n in os.getenv("HR_NUMBERS", "").split(",") if n.strip()}
TEMPLATE_ID = os.getenv("TEMPLATE_ID", "")        
DEBUG = os.getenv("DEBUG_WEBHOOK", "1") == "1"    

app = FastAPI()
db.init()
_seen_ids = set()  

def load_staff_csv():
    path = next((p for p in ("staff.csv", "/etc/secrets/staff.csv") if os.path.exists(p)), None)
    if not path:
        return 0
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            phone = wa.normalize_phone(row.get("phone"))
            if phone:
                db.upsert_staff(phone, (row.get("name") or "").strip())
                n += 1
    return n


def valid_signature(raw_body: bytes, header: Optional[str]) -> bool:
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def _text_of(msg):
    t = msg.get("type")
    if t == "text":
        return (msg.get("text") or {}).get("body")
    if t == "button":  # tap on a template quick-reply button
        b = msg.get("button") or {}
        return b.get("text") or b.get("payload")
    if t == "interactive":
        i = msg.get("interactive") or {}
        r = i.get("button_reply") or i.get("list_reply") or {}
        return r.get("title") or r.get("id")
    return None


def extract_messages(data):
    """Reads Meta-style webhook payloads (what WhatsApp providers usually forward)."""
    out = []
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                sender = msg.get("from", "")
                out.append((msg.get("id", ""), sender, names.get(sender), _text_of(msg)))
    return out


async def broadcast_menu_prompt() -> str:
    if not await run_in_threadpool(db.get_menu, logic.today()):
        return "Send today's menu first (Menu: ...), then reply Send."
    if not TEMPLATE_ID:
        return "The broadcast template isn't set up yet (TEMPLATE_ID). Staff can still text 'menu' to the bot."
    staff = await run_in_threadpool(db.get_staff)
    if not staff:
        return "No staff added yet."
    for s in staff:
        await wa.send_template(s["phone"], TEMPLATE_ID, [s["name"] or "there"])
        await asyncio.sleep(0.2)
    return f"Menu prompt sent to {len(staff)} staff."


async def handle_hr(text: str) -> str:
    lower = text.strip().lower()
    if lower.startswith("menu"):
        raw = text.strip()[4:].lstrip(" :\n")
        if not raw:
            return "Send it like this:\nMenu: paste today's menu"
        items = await run_in_threadpool(logic.set_menu, raw)
        return ("Menu saved \n\n" + logic.format_menu(items)
                + "\n\nReply 'Send' to send it to all staff, or 'Summary' to see orders so far.")
    if lower.startswith("summary"):
        return await run_in_threadpool(logic.build_summary)
    if lower.startswith("send"):
        return await broadcast_menu_prompt()
    reply = await run_in_threadpool(logic.staff_command, text)
    if reply:
        return reply
    return ("These are your commands:\n• Menu: <menu text>\n• Send\n• Summary\n"
            "• Staff\n• Add Ama 0241234567\n• Remove Ama")


@app.get("/webhook")
async def verify(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Meta-style verification handshake (kept in case the provider uses it)."""
    if mode == "subscribe" and VERIFY_TOKEN and token == VERIFY_TOKEN and challenge:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive(request: Request, secret: Optional[str] = Query(None)):
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Bad secret")
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Bad signature")
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return {"status": "ignored"}
    if DEBUG:
        print("RAW", json.dumps(data, ensure_ascii=False)[:1500])

    for msg_id, sender, profile_name, text in extract_messages(data):
        if msg_id and msg_id in _seen_ids:
            continue
        _seen_ids.add(msg_id)
        sender = wa.normalize_phone(sender)
        if not text:
            await wa.send_text(sender, "Sorry, but I can only read text messages. Please type your order.")
            continue
        print(f"IN  {profile_name} ({sender}): {text}")
        try:
            if sender in HR_NUMBERS:
                reply = await handle_hr(text)
            else:
                await run_in_threadpool(db.ensure_staff, sender, profile_name or sender)
                name = await run_in_threadpool(db.staff_name, sender) or profile_name or sender
                reply = await run_in_threadpool(logic.handle_staff_reply, sender, name, text)
        except Exception as e:  
            print("ERROR", repr(e))
            reply = "Erm, something seems off twin, having some trouble. Kindly try again or tell HR directly."
        await wa.send_text(sender, reply)

    return {"status": "ok"}  


async def summary_scheduler():
    """At the cutoff time, push the summary to HR (once per day)."""
    while True:
        await asyncio.sleep(30)
        try:
            day = logic.today()
            key = f"summary_sent:{day}"
            if 0 <= logic.minutes_since_cutoff() < 10 and not db.get_flag(key) and db.get_menu(day):
                db.set_flag(key, "1")
                text = await run_in_threadpool(logic.build_summary)
                for hr in HR_NUMBERS:
                    await wa.send_text(hr, text)
        except Exception as e:
            print("SCHEDULER ERROR", repr(e))


@app.on_event("startup")
async def startup():
    if db.count_staff() == 0:  
        print("Staff imported :", load_staff_csv())
    print("Storage:", "Postgres" if db.PG else "SQLite file", "| staff:", db.count_staff())
    asyncio.create_task(summary_scheduler())


@app.get("/")
async def health():
    return {"ok": True}
