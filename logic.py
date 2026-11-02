import os
import re
from collections import Counter
from datetime import datetime, timezone

import ai
import db
from phone import normalize_phone


def _now():
    return datetime.now(timezone.utc)  # Ghana is on GMT (UTC+0) all year


def today():
    return _now().strftime("%Y-%m-%d")


# order window: orders close WINDOW_MIN minutes after HR sends the menu to staff 
WINDOW_MIN = int(os.getenv("ORDER_WINDOW_MIN", "45"))
REMINDER_BEFORE_MIN = int(os.getenv("REMINDER_BEFORE_MIN", "15"))  # one reminder this long before closing

def _ts():
    return int(_now().timestamp())


def fmt_time(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%H:%M")


def seconds_since(ts):
    return _ts() - int(ts)


def get_close(day=None):
    v = db.get_flag(f"close_at:{day or today()}")
    return int(v) if v else None


def orders_closed(day=None):
    c = get_close(day)
    return c is not None and _ts() >= c


def _close_note(day=None):
    c = get_close(day)
    return f"\n\nOrders close at {fmt_time(c)}." if c else ""


def mark_broadcast(day=None):
    """HR sent the menu to staff. The first time, start the clock; later sends never move it."""
    day = day or today()
    if db.get_flag(f"broadcast_done:{day}"):
        return get_close(day), False
    close = _ts() + WINDOW_MIN * 60
    db.set_flag(f"broadcast_done:{day}", "1")
    db.set_flag(f"close_at:{day}", str(close))
    return close, True


def extend_orders(minutes, day=None):
    """Reopen / extend orders by `minutes` and let the closing summary be sent again."""
    day = day or today()
    if not db.get_menu(day):
        return "There is no menu for today yet."
    minutes = max(1, min(int(minutes), 240))
    new_close = max(get_close(day) or 0, _ts()) + minutes * 60
    db.set_flag(f"close_at:{day}", str(new_close))
    db.set_flag(f"summary_sent:{day}", "")
    return f"Orders now close at {fmt_time(new_close)}."


MENU_WORDS = {"menu", "view menu", "hi", "hello", "hey", "todays menu", "today's menu", "what's for lunch", "wagwan wi di lunch",
              "what we dey chop", "wey chow dey"}
SKIP_WORDS = {"none", "not eating", "skip", "no lunch", "ano dey chop", "no lie", "echill chale"}


def format_menu(items):
    out = []
    for it in items:
        out.append(f"{it['id']}. {it['name']}")
        for opt in it.get("options") or []:
            out.append(f"    ({opt['label']}: {' / '.join(opt['choices'])})")
    return "\n".join(out)


def set_menu(raw_text):
    items = ai.parse_menu(raw_text)
    day = today()
    db.save_menu(day, raw_text, items)
    if not db.get_flag(f"broadcast_done:{day}"):
        # Provisional closing time (for staff who text "menu" themselves).
        # HR's "Send" restarts the clock from the moment staff are messaged.
        db.set_flag(f"close_at:{day}", str(_ts() + WINDOW_MIN * 60))
    return items


def _missing(item, choices):
    return [
        o for o in (item.get("options") or [])
        if o.get("required", True) and not choices.get(o["label"])
    ]


def _first_missing(lines, by_id):
    """First line (index, item, missing options) that still needs an answer."""
    for i, ln in enumerate(lines):
        item = by_id.get(ln.get("item_id"))
        if item:
            m = _missing(item, ln.get("choices") or {})
            if m:
                return i, item, m
    return None


def _question(item, missing):
    o = missing[0]
    return f"{item['name']}: which {o['label']}? {' / '.join(o['choices'])}"


def _line_label(ln, by_id):
    item = by_id.get(ln.get("item_id"))
    if not item:
        return ln.get("custom", "").strip()
    name = item["name"]
    choices = ln.get("choices") or {}
    for opt in item.get("options") or []:
        picked = choices.get(opt["label"])
        if not picked:
            continue
        # "fish/grilled chicken" inside the dish name becomes just the chosen one
        pattern = r"\s*/\s*".join(re.escape(c) for c in opt["choices"])
        new_name, n = re.subn(pattern, picked, name, count=1, flags=re.IGNORECASE)
        name = new_name if n else f"{name} + {picked}"
    return name


def _is_custom(ln, by_id):
    return ln.get("item_id") not in by_id


def _match_choice(item, text):
    t = text.strip().lower()
    for o in _missing(item, {}):
        for c in o["choices"]:
            if t == c.lower():
                return {o["label"]: c}
    return None


def handle_staff_reply(phone, name, text):
    day = today()
    menu = db.get_menu(day)
    if not menu:
        return "No menu has been posted yet today. Please check back soon."
    if orders_closed(day):
        return f"Sorry, orders closed at {fmt_time(get_close(day))}. Please speak to HR."

    items = menu["items"]
    by_id = {i["id"]: i for i in items}
    existing = db.get_order(day, phone)
    t = text.strip()
    parsed = None

    # Cheap fast paths so simple replies never need an AI call.
    if t.lower() in MENU_WORDS:
        return ("Today's menu:\n" + format_menu(items)
                + "\n\nReply with a number or the dish name. Reply NONE if you're not eating."
                + _close_note(day))
    if t.lower() in SKIP_WORDS:
        db.save_order(day, phone, name, [], "", "skipped")
        return "Noted, no lunch for you today."
    if t.isdigit() and int(t) in by_id:
        parsed = {"intent": "order", "notes": "",
                  "lines": [{"item_id": int(t), "choices": {}, "custom": ""}]}
    elif existing and existing["status"] == "pending":
        fm = _first_missing(existing["lines"], by_id)
        if fm:
            i, item, _ = fm
            match = _match_choice(item, t)
            if match:
                lines = [dict(l) for l in existing["lines"]]
                lines[i]["choices"] = {**(lines[i].get("choices") or {}), **match}
                parsed = {"intent": "order", "lines": lines, "notes": existing["notes"]}

    if parsed is None:
        parsed = ai.parse_order(items, existing, t)

    intent = parsed.get("intent")
    if intent == "cancel":
        db.delete_order(day, phone)
        return "Your order has been cancelled."
    if intent == "menu":
        return "Today's menu:\n" + format_menu(items) + "\n\nReply with a number or the dish name." + _close_note(day)

    not_on_menu = parsed.get("not_on_menu") or []
    if not_on_menu:
        return ("Sorry, " + ", ".join(not_on_menu) + " isn't on today's menu.\n\n"
                + format_menu(items) + "\n\nWhat would you like instead?")

    lines = []
    for ln in parsed.get("lines") or []:
        if ln.get("item_id") in by_id or (ln.get("custom") or "").strip():
            lines.append(ln)
    if intent != "order" or not lines:
        return "Sorry, I didn't catch that. Today's menu:\n" + format_menu(items) + "\n\nReply with a number."

    notes = parsed.get("notes") or ""
    fm = _first_missing(lines, by_id)
    if fm:
        _, item, missing = fm
        q = _question(item, missing)
        db.save_order(day, phone, name, lines, notes, "pending", q)
        return q

    db.save_order(day, phone, name, lines, notes, "complete")
    what = "; ".join(_line_label(l, by_id) for l in lines)
    msg = f"Order saved: {what}"
    if notes:
        msg += f" ({notes})"
    if any(_is_custom(l, by_id) for l in lines):
        msg += "\n(This is a custom mix, so HR will confirm it with the kitchen.)"
    return msg + "\nTo change it, just send a new choice."


def build_summary(day=None):
    day = day or today()
    menu = db.get_menu(day)
    if not menu:
        return "No menu was posted for this day."
    by_id = {i["id"]: i for i in menu["items"]}
    orders = db.get_orders(day)
    done = [o for o in orders if o["status"] == "complete"]
    skipped = [o for o in orders if o["status"] == "skipped"]
    pending = [o for o in orders if o["status"] == "pending"]

    standard, custom = Counter(), Counter()
    for o in done:
        for ln in o["lines"]:
            (custom if _is_custom(ln, by_id) else standard)[_line_label(ln, by_id)] += 1
    meals = sum(standard.values()) + sum(custom.values())

    title = datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b")
    people = f"{len(done)} person" if len(done) == 1 else f"{len(done)} people"
    lines = [f"Lunch orders – {title}: {people}, {meals} meal(s)", ""]
    for label, n in sorted(standard.items(), key=lambda x: -x[1]):
        lines.append(f"• {label}: {n}")
    if custom:
        lines += ["", "Custom mixes (confirm with the cook):"]
        for label, n in sorted(custom.items(), key=lambda x: -x[1]):
            lines.append(f"• {label}: {n}")

    lines += ["", "By person:"]
    for o in sorted(done, key=lambda o: (o["name"] or "").lower()):
        what = "; ".join(_line_label(l, by_id) for l in o["lines"])
        note = f" ({o['notes']})" if o["notes"] else ""
        lines.append(f"- {o['name'] or o['phone']}: {what}{note}")

    if pending:
        lines += ["", "Incomplete (still need an answer):"]
        lines += [f"- {o['name'] or o['phone']}: {o['question']}" for o in pending]
    if skipped:
        lines += ["", "Not eating: " + ", ".join(sorted(o["name"] or o["phone"] for o in skipped))]
    ordered = {o["phone"] for o in orders}
    silent = [s["name"] for s in db.get_staff() if s["phone"] not in ordered]
    if silent:
        lines += ["", "No reply yet: " + ", ".join(sorted(silent))]
    return "\n".join(lines)


def staff_command(text):
    """HR staff-list commands. Returns a reply, or None if the text is not one of them."""
    t = text.strip()
    low = t.lower()
    if low == "staff":
        rows = sorted(db.get_staff(), key=lambda s: (s["name"] or "").lower())
        if not rows:
            return "No staff yet. Add someone like this:\nAdd Ama 0241234567"
        return f"{len(rows)} staff:\n" + "\n".join(f"- {s['name']} ({s['phone']})" for s in rows)
    if low.startswith("add "):
        m = re.match(r"^(.*?)[\s:,]+(\+?\d[\d\s\-()]{7,})$", t[4:].strip())
        phone = normalize_phone(m.group(2)) if m else ""
        name = m.group(1).strip() if m else ""
        if not name or not (11 <= len(phone) <= 15):
            return "Use: Add Ama 0241234567 (name, then phone number)"
        db.upsert_staff(phone, name)
        return f"Added {name} ({phone})."
    if low.startswith("remove "):
        target = t[7:].strip()
        found = db.find_staff(target)
        if not found and normalize_phone(target):
            found = db.find_staff(normalize_phone(target))
        if not found:
            return f"I couldn't find '{target}'. Send 'Staff' to see the list."
        if len(found) > 1:
            return "More than one match, please use the phone number:\n" + "\n".join(
                f"- {s['name']} ({s['phone']})" for s in found)
        db.remove_staff(found[0]["phone"])
        return f"Removed {found[0]['name']} ({found[0]['phone']})."
    return None


def all_replied(day=None, exclude=()):
    """True when every staff member (except `exclude`, e.g. HR) has a finished order or said NONE."""
    day = day or today()
    staff = [s for s in db.get_staff() if s["phone"] not in exclude]
    if not staff or not db.get_menu(day):
        return False
    done = {o["phone"] for o in db.get_orders(day) if o["status"] in ("complete", "skipped")}
    return all(s["phone"] in done for s in staff)


def is_hr_command(text):
    """True only for exact HR commands, so HR can also order lunch like anyone else."""
    t = text.strip()
    low = t.lower()
    lines = [l for l in low.splitlines() if l.strip()]
    first = lines[0].strip() if lines else ""
    if first.startswith("menu:") or (first == "menu" and len(lines) > 1):
        return True
    if low in ("send", "summary", "staff"):
        return True
    if re.match(r"^extend\s+\d{1,3}$", low):
        return True
    if re.match(r"^add\s+.+?[\s:,]+\+?\d[\d\s\-()]{7,}$", low):
        return True
    if low.startswith("remove "):
        target = t[7:].strip()
        return bool(db.find_staff(target) or (normalize_phone(target) and db.find_staff(normalize_phone(target))))
    return False

def outstanding_staff(day=None):
    """Staff with no finished order and no NONE reply (includes people with a pending question)."""
    day = day or today()
    done = {o["phone"] for o in db.get_orders(day) if o["status"] in ("complete", "skipped")}
    return [s for s in db.get_staff() if s["phone"] not in done]
 
 
def reminder_due(day=None):
    """True once per day: HR has sent the menu, and closing is within REMINDER_BEFORE_MIN minutes."""
    day = day or today()
    close = get_close(day)
    if not close or not db.get_menu(day):
        return False
    if not db.get_flag(f"broadcast_done:{day}") or db.get_flag(f"reminder_sent:{day}"):
        return False
    sent_at = db.get_flag(f"broadcast_at:{day}")
    if sent_at and _ts() - int(sent_at) < 300:  # never remind within 5 minutes of the prompt itself
        return False
    left = close - _ts()
    return 0 < left <= REMINDER_BEFORE_MIN * 60