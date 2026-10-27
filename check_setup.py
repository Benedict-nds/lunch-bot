
import csv
import os
import sys

import httpx

import wa

KEY = os.environ["CBA_API_KEY"]


def get(path, **params):
    r = httpx.get(f"{wa.BASE}{path}", params={"apikey": KEY, **params}, timeout=20)
    print(r.status_code, r.text[:2000])


def post(path, payload):
    r = httpx.post(f"{wa.BASE}{path}", json={"apikey": KEY, **payload}, timeout=20)
    print(r.status_code, r.text[:1000])


cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "balance":
    get("/wallet/balance")
elif cmd == "templates":
    get("/send/template/list")
elif cmd == "send":
    post("/send/message", {"destination": wa.normalize_phone(sys.argv[2]), "message": sys.argv[3]})
elif cmd == "template":
    post("/send/message/template", {
        "destination": wa.normalize_phone(sys.argv[2]),
        "template_id": int(sys.argv[3]),
        "type": "text",
        "placeholder": [sys.argv[4]],
    })
elif cmd == "report":
    get("/delivery/report", message_id=sys.argv[2])
elif cmd == "optin":
    with open("staff.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            phone = wa.normalize_phone(row["phone"])
            print(row["name"], phone, end=" -> ")
            post("/send/opt/contact", {"destination": phone, "status": True})
else:
    print(__doc__)


"""Run these from your laptop to check the Chatbots Africa account.

    export CBA_API_KEY=...            (never paste the key into chats or code)
    python check_setup.py balance     # wallet credit
    python check_setup.py templates   # approved templates and their IDs
    python check_setup.py send 233XXXXXXXXX "hello"   # test message to a number
    python check_setup.py template 233XXXXXXXXX 1593173875316322 Ama   # send the daily template to one person
    python check_setup.py report "wamid...."   # delivery status of a sent message
    python check_setup.py optin       # opt in everyone in staff.csv (only after staff agreed)
"""
# """Run these from your laptop to check the Chatbots Africa account.

#     export CBA_API_KEY=...            (never paste the key into chats or code)
#     python check_setup.py balance     # wallet credit
#     python check_setup.py templates   # approved templates and their IDs
#     python check_setup.py send 233XXXXXXXXX "hello"   # test message to a number
#     python check_setup.py report "wamid...."   # delivery status of a sent message
#     python check_setup.py optin       # opt in everyone in staff.csv (only after staff agreed)
# """
# import csv
# import os
# import sys

# import httpx

# import wa

# KEY = os.environ["CBA_API_KEY"]


# def get(path, **params):
#     r = httpx.get(f"{wa.BASE}{path}", params={"apikey": KEY, **params}, timeout=20)
#     print(r.status_code, r.text[:2000])


# def post(path, payload):
#     r = httpx.post(f"{wa.BASE}{path}", json={"apikey": KEY, **payload}, timeout=20)
#     print(r.status_code, r.text[:1000])


# cmd = sys.argv[1] if len(sys.argv) > 1 else ""
# if cmd == "balance":
#     get("/wallet/balance")
# elif cmd == "templates":
#     get("/send/template/list")
# elif cmd == "send":
#     post("/send/message", {"destination": wa.normalize_phone(sys.argv[2]), "message": sys.argv[3]})
# elif cmd == "report":
#     get("/delivery/report", message_id=sys.argv[2])
# elif cmd == "optin":
#     with open("staff.csv", newline="", encoding="utf-8") as f:
#         for row in csv.DictReader(f):
#             phone = wa.normalize_phone(row["phone"])
#             print(row["name"], phone, end=" -> ")
#             post("/send/opt/contact", {"destination": phone, "status": True})
# else:
#     print(__doc__)
