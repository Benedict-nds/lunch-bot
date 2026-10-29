import os
import re
from collections import Counter
from datetime import datetime

import ai
import db
from phone import normalize_phone


def _now():
    return datetime.utcnow() 


def today():
    return _now().strftime("%Y-%m-%d")


def cutoff_text():
    return os.getenv("CUTOFF", "10:30")


def minutes_since_cutoff():
    hh, mm = cutoff_text().split(":")
    n = _now()
    return (n.hour * 60 + n.minute) - (int(hh) * 60 + int(mm))


def cutoff_passed():
    return minutes_since_cutoff() >= 0


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
    db.save_menu(today(), raw_text, items)
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
    if cutoff_passed():
        return f"Sorry, orders closed at {cutoff_text()}. Please speak to HR."

    items = menu["items"]
    by_id = {i["id"]: i for i in items}
    existing = db.get_order(day, phone)
    t = text.strip()
    parsed = None

    # Cheap fast paths so simple replies never need an AI call.
    if t.lower() in MENU_WORDS:
        return ("Today's menu:\n" + format_menu(items)
                + "\n\nReply with a number or the dish name. Reply NONE if you're not eating.")
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
        return "Today's menu:\n" + format_menu(items) + "\n\nReply with a number or the dish name."

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
            return "No staff yet. Add someone like this:\nAdd Tutu 0241234567"
        return f"{len(rows)} staff:\n" + "\n".join(f"- {s['name']} ({s['phone']})" for s in rows)
    if low.startswith("add "):
        m = re.match(r"^(.*?)[\s:,]+(\+?\d[\d\s\-()]{7,})$", t[4:].strip())
        phone = normalize_phone(m.group(2)) if m else ""
        name = m.group(1).strip() if m else ""
        if not name or not (11 <= len(phone) <= 15):
            return "Use: Add Godsway 0241234567 (name, then phone number)"
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
