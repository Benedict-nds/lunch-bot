import json
import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "lunch.db")


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS menus (
                day TEXT PRIMARY KEY, raw TEXT, items_json TEXT);
            CREATE TABLE IF NOT EXISTS orders (
                day TEXT, phone TEXT, name TEXT, lines_json TEXT, notes TEXT,
                status TEXT, question TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (day, phone));
            CREATE TABLE IF NOT EXISTS staff (
                phone TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY, value TEXT);
            """
        )


def save_menu(day, raw, items):
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO menus (day, raw, items_json) VALUES (?,?,?)",
            (day, raw, json.dumps(items, ensure_ascii=False)),
        )


def get_menu(day):
    with conn() as c:
        r = c.execute("SELECT * FROM menus WHERE day=?", (day,)).fetchone()
    if not r:
        return None
    return {"raw": r["raw"], "items": json.loads(r["items_json"])}


def _order(r):
    if not r:
        return None
    d = dict(r)
    d["lines"] = json.loads(d.pop("lines_json") or "[]")
    return d


def get_order(day, phone):
    with conn() as c:
        return _order(
            c.execute("SELECT * FROM orders WHERE day=? AND phone=?", (day, phone)).fetchone()
        )


def get_orders(day):
    with conn() as c:
        rows = c.execute("SELECT * FROM orders WHERE day=?", (day,)).fetchall()
    return [_order(r) for r in rows]


def save_order(day, phone, name, lines, notes, status, question=""):
    with conn() as c:
        c.execute(
            """INSERT OR REPLACE INTO orders
               (day, phone, name, lines_json, notes, status, question)
               VALUES (?,?,?,?,?,?,?)""",
            (day, phone, name, json.dumps(lines, ensure_ascii=False), notes, status, question),
        )


def delete_order(day, phone):
    with conn() as c:
        c.execute("DELETE FROM orders WHERE day=? AND phone=?", (day, phone))


def upsert_staff(phone, name):
    if not name:
        return
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO staff (phone, name) VALUES (?,?)", (phone, name))


def get_staff():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM staff").fetchall()]


def ensure_staff(phone, name):
    """Add someone we have not seen before without overwriting a name from staff.csv."""
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO staff (phone, name) VALUES (?,?)", (phone, name))


def staff_name(phone):
    with conn() as c:
        r = c.execute("SELECT name FROM staff WHERE phone=?", (phone,)).fetchone()
    return r["name"] if r else None


def get_flag(key):
    with conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_flag(key, value):
    with conn() as c:
        c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
