
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
