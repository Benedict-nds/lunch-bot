"""Storage. Uses hosted Postgres when DATABASE_URL is set (survives Render restarts),
otherwise a local SQLite file (simulator and quick local tests)."""
import json
import os
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", "lunch.db")
PG = bool(DATABASE_URL)

if PG:
    import psycopg
    from psycopg.rows import dict_row


def _connect():
    if PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def run(sql, params=(), fetch=None):
    """Run one statement. fetch: None, 'one' or 'all'."""
    if PG:
        sql = sql.replace("?", "%s")
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        result = None
        if fetch == "one":
            result = cur.fetchone()
        elif fetch == "all":
            result = cur.fetchall()
        conn.commit()
        return result
    finally:
        conn.close()


SCHEMA = [
    "CREATE TABLE IF NOT EXISTS menus (day TEXT PRIMARY KEY, raw TEXT, items_json TEXT)",
    """CREATE TABLE IF NOT EXISTS orders (
        day TEXT, phone TEXT, name TEXT, lines_json TEXT, notes TEXT,
        status TEXT, question TEXT, PRIMARY KEY (day, phone))""",
    "CREATE TABLE IF NOT EXISTS staff (phone TEXT PRIMARY KEY, name TEXT)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
]


def init():
    for stmt in SCHEMA:
        run(stmt)


# ---- menus ----

def save_menu(day, raw, items):
    run(
        """INSERT INTO menus (day, raw, items_json) VALUES (?,?,?)
           ON CONFLICT (day) DO UPDATE SET raw=excluded.raw, items_json=excluded.items_json""",
        (day, raw, json.dumps(items, ensure_ascii=False)),
    )


def get_menu(day):
    r = run("SELECT raw, items_json FROM menus WHERE day=?", (day,), "one")
    if not r:
        return None
    return {"raw": r["raw"], "items": json.loads(r["items_json"])}


#orders 

def _order(r):
    if not r:
        return None
    d = dict(r)
    d["lines"] = json.loads(d.pop("lines_json") or "[]")
    return d


def get_order(day, phone):
    return _order(run("SELECT * FROM orders WHERE day=? AND phone=?", (day, phone), "one"))


def get_orders(day):
    return [_order(r) for r in run("SELECT * FROM orders WHERE day=?", (day,), "all")]


def save_order(day, phone, name, lines, notes, status, question=""):
    run(
        """INSERT INTO orders (day, phone, name, lines_json, notes, status, question)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT (day, phone) DO UPDATE SET name=excluded.name,
             lines_json=excluded.lines_json, notes=excluded.notes,
             status=excluded.status, question=excluded.question""",
        (day, phone, name, json.dumps(lines, ensure_ascii=False), notes, status, question),
    )


def delete_order(day, phone):
    run("DELETE FROM orders WHERE day=? AND phone=?", (day, phone))


# staff 

def upsert_staff(phone, name):
    if not name:
        return
    run(
        """INSERT INTO staff (phone, name) VALUES (?,?)
           ON CONFLICT (phone) DO UPDATE SET name=excluded.name""",
        (phone, name),
    )


def ensure_staff(phone, name):
    """Add someone we have not seen before without overwriting an existing name."""
    run("INSERT INTO staff (phone, name) VALUES (?,?) ON CONFLICT (phone) DO NOTHING", (phone, name))


def staff_name(phone):
    r = run("SELECT name FROM staff WHERE phone=?", (phone,), "one")
    return r["name"] if r else None


def get_staff():
    return [dict(r) for r in run("SELECT phone, name FROM staff", (), "all")]


def count_staff():
    return run("SELECT COUNT(*) AS n FROM staff", (), "one")["n"]


def find_staff(identifier):
    """Match by phone (digits) or by name (case-insensitive). Returns a list."""
    r = run("SELECT phone, name FROM staff WHERE phone=? OR lower(name)=lower(?)",
            (identifier, identifier), "all")
    return [dict(x) for x in r]


def remove_staff(phone):
    run("DELETE FROM staff WHERE phone=?", (phone,))


# small flags (e.g. "summary already sent today") 

def get_flag(key):
    r = run("SELECT value FROM meta WHERE key=?", (key,), "one")
    return r["value"] if r else None


def set_flag(key, value):
    run(
        """INSERT INTO meta (key, value) VALUES (?,?)
           ON CONFLICT (key) DO UPDATE SET value=excluded.value""",
        (key, value),
    )
