import asyncio
import csv
import hashlib
import hmac
import json
import os
import re
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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # add ?secret=... to the callback URL
HR_NUMBERS = {wa.normalize_phone(n) for n in os.getenv("HR_NUMBERS", "").split(",") if n.strip()}
TEMPLATE_ID = os.getenv("TEMPLATE_ID", "")        
DEBUG = os.getenv("DEBUG_WEBHOOK", "1") == "1"    

app = FastAPI()
db.init()
_seen_ids = set()  # providers retry webhooks; don't process the same message twice


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
    day = logic.today()
    if not await run_in_threadpool(db.get_menu, day):
        return "Send today's menu first (Menu: ...), then reply Send."
    if not TEMPLATE_ID:
        return "The broadcast template isn't set up yet (TEMPLATE_ID). Staff can still text 'menu' to the bot."
    staff = await run_in_threadpool(db.get_staff)
    if not staff:
        return "No staff loaded yet. Add people with: Add Tutu 0241234567"
    for s in staff:
        await wa.send_template(s["phone"], TEMPLATE_ID, [s["name"] or "there"])
        await asyncio.sleep(0.2)
    close, first = await run_in_threadpool(logic.mark_broadcast, day)
    when = logic.fmt_time(close)
    if first:
        return f"Menu prompt sent to {len(staff)} staff. Orders close at {when}."
    return f"Menu prompt re-sent to {len(staff)} staff. Orders still close at {when}."


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


async def maybe_send_early_summary():
    """As soon as everyone on the staff list has replied (before closing), send HR the summary once."""
    day = logic.today()
    key = f"early_sent:{day}"
    if await run_in_threadpool(logic.orders_closed, day) or await run_in_threadpool(db.get_flag, key):
        return
    if not await run_in_threadpool(logic.all_replied, day):
        return
    text = await run_in_threadpool(logic.build_summary)
    await run_in_threadpool(db.set_flag, key, _digest(text))
    close = await run_in_threadpool(logic.get_close, day)
    until = f" until {logic.fmt_time(close)}" if close else ""
    for hr in HR_NUMBERS:
        await wa.send_text(hr, "Everyone has replied. Here is the summary:\n\n" + text
                           + f"\n\n(Staff can still change orders{until}.)")


async def handle_hr(text: str) -> str:
    lower = text.strip().lower()
    first = next((l.strip() for l in lower.splitlines() if l.strip()), "")
    if first.startswith("menu"):
        raw = text.strip()[4:].lstrip(" :\n")
        if not raw:
            return "Send it like this:\nMenu: paste today's menu"
        items = await run_in_threadpool(logic.set_menu, raw)
        return ("Menu saved\n\n" + logic.format_menu(items)
                + f"\n\nReply 'Send' to message all staff. Orders close {logic.WINDOW_MIN} minutes after you send it."
                + "\nReply 'Summary' any time to see orders so far.")
    if lower == "summary":
        return await run_in_threadpool(logic.build_summary)
    if lower == "send":
        return await broadcast_menu_prompt()
    m = re.match(r"^extend\s+(\d{1,3})$", lower)
    if m:
        return await run_in_threadpool(logic.extend_orders, int(m.group(1)))
    reply = await run_in_threadpool(logic.staff_command, text)
    if reply:
        return reply
    return ("Hi, Eunice, these are your commands:\n• Menu: menu text\n• Send (to broadcast to everyone)\n• Summary (to see everyone's order)\n"
            "• Staff (to see staff list) \n• Add say Tutu 0241234567 (adds to staff list) \n• Remove say Godsway (removes from staff list) \n• Extend 30 (extends order window by 30 minutes)")


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
        ordering = False
        sender = wa.normalize_phone(sender)
        if not text:
            await wa.send_text(sender, "Sorry, I can only read text messages. Please type your order.")
            continue
        print(f"IN  {profile_name} ({sender}): {text}")
        try:
            if sender in HR_NUMBERS and await run_in_threadpool(logic.is_hr_command, text):
                reply = await handle_hr(text)
            else:
                ordering = True  # HR can order lunch too: anything that is not an exact command
                if sender not in HR_NUMBERS:
                    await run_in_threadpool(db.ensure_staff, sender, profile_name or sender)
                name = await run_in_threadpool(db.staff_name, sender) or profile_name or sender
                reply = await run_in_threadpool(logic.handle_staff_reply, sender, name, text)
        except Exception as e:  # never let one bad message crash the webhook
            print("ERROR", repr(e))
            reply = "Erm, something went wrong on my side. Please try again or tell HR."
        await wa.send_text(sender, reply)
        if ordering:
            try:
                await maybe_send_early_summary()
            except Exception as e:
                print("EARLY SUMMARY ERROR", repr(e))

    return {"status": "ok"}  # always answer 200 quickly or the provider retries

async def maybe_send_reminders():
    """Once per day, shortly before closing, resend the menu prompt to staff who haven't ordered."""
    day = logic.today()
    if not TEMPLATE_ID or not await run_in_threadpool(logic.reminder_due, day):
        return
    await run_in_threadpool(db.set_flag, f"reminder_sent:{day}", "1")  # set first: never send twice
    todo = await run_in_threadpool(logic.outstanding_staff, day)
    for s in todo:
        await wa.send_template(s["phone"], TEMPLATE_ID, [s["name"] or "there"])
        await asyncio.sleep(0.2)
    if todo:
        close = await run_in_threadpool(logic.get_close, day)
        names = ", ".join(sorted(s["name"] or s["phone"] for s in todo))
        for hr in HR_NUMBERS:
            await wa.send_text(hr, f"Reminder sent to {len(todo)} staff who haven't ordered or declined yet: {names}. "
                                   f"Orders close at {logic.fmt_time(close)}.")

async def maybe_send_cutoff_summary():
    """When orders close, push the summary to HR (once per closing time)."""
    day = logic.today()
    key = f"summary_sent:{day}"
    close = await run_in_threadpool(logic.get_close, day)
    if not close or logic.seconds_since(close) < 0 or logic.seconds_since(close) > 3 * 3600:
        return
    if await run_in_threadpool(db.get_flag, key) or not await run_in_threadpool(db.get_menu, day):
        return
    await run_in_threadpool(db.set_flag, key, "1")
    text = await run_in_threadpool(logic.build_summary)
    early = await run_in_threadpool(db.get_flag, f"early_sent:{day}")
    when = logic.fmt_time(close)
    if early and early == _digest(text):
        text = f"Orders closed at {when}. No changes since the earlier summary, so that one is final."
    elif early:
        text = f"Orders closed at {when}. FINAL summary (some orders changed since the earlier one):\n\n" + text
    else:
        text = f"Orders closed at {when}. Final summary:\n\n" + text
    for hr in HR_NUMBERS:
        await wa.send_text(hr, text)


async def summary_scheduler():
    while True:
        await asyncio.sleep(30)
        try:
            await maybe_send_cutoff_summary()
        except Exception as e:
            print("SCHEDULER ERROR", repr(e))


@app.on_event("startup")
async def startup():
    if db.count_staff() == 0:  # first run only; after that HR manages the list by text
        print("Staff imported from staff.csv:", load_staff_csv())
    print("Storage:", "Postgres" if db.PG else "SQLite file", "| staff:", db.count_staff())
    asyncio.create_task(summary_scheduler())


@app.get("/")
async def health():
    return {"ok": True}
