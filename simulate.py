"""Test the whole ordering flow in your terminal, no WhatsApp needed.

    export ANTHROPIC_API_KEY=sk-ant-...
    python simulate.py

Type messages as  name: message   e.g.   ama: 1     kofi: i want the fufu with goat
Commands: /summary   /menu   /quit
"""
import os

os.environ["DB_PATH"] = "sim.db"
os.environ.setdefault("CUTOFF", "23:59")  # don't let the real cutoff block testing
if os.path.exists("sim.db"):
    os.remove("sim.db")

import db  # noqa: E402
import logic  # noqa: E402

db.init()
raw = open("sample_menu.txt", encoding="utf-8").read()
items = logic.set_menu(raw)
print("Parsed menu:\n" + logic.format_menu(items) + "\n")

while True:
    line = input("name: message > ").strip()
    if line == "/quit":
        break
    if line == "/summary":
        print(logic.build_summary() + "\n")
        continue
    if line == "/menu":
        print(logic.format_menu(items) + "\n")
        continue
    name, _, text = line.partition(":")
    name, text = name.strip(), text.strip()
    if not name or not text:
        print("Use the format  name: message\n")
        continue
    db.upsert_staff(name.lower(), name)
    print("BOT ->", logic.handle_staff_reply(name.lower(), name, text), "\n")
