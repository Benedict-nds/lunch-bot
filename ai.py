import difflib
import json
import os
import re

MODEL = os.getenv("AI_MODEL", "gpt-5-mini")
_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI  # reads OPENAI_API_KEY

        _client = OpenAI()
    return _client


def _ask_json(system, user):
    r = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        reasoning_effort="minimal",
        max_completion_tokens=4000,
    )
    text = (r.choices[0].message.content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


#menu: cleaning and splitting done in code 

_SMALL_CAPS = {
    "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ғ": "f", "ꜰ": "f", "ɢ": "g",
    "ʜ": "h", "ɪ": "i", "ᴊ": "j", "ᴋ": "k", "ʟ": "l", "ᴍ": "m", "ɴ": "n", "ᴏ": "o",
    "ᴘ": "p", "ǫ": "q", "ʀ": "r", "ꜱ": "s", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w",
    "ʏ": "y", "ᴢ": "z",
}
_SC_TABLE = str.maketrans(_SMALL_CAPS)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")


def dish_lines(raw_text):
    """Plain, lowercase dish lines: one per dish, title and separators removed."""
    text = raw_text.translate(_SC_TABLE)
    text = _EMOJI.sub("", text).replace("*", "").replace("_", " ")
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"(\w)\(", r"\1 (", ln)  # "sauce(beef" -> "sauce (beef"
        ln = re.sub(r"\s+", " ", ln).strip()
        if not ln or re.fullmatch(r"[-=~•. ]+", ln):
            continue
        lines.append(ln.lower())
    if lines and "menu" in lines[0]:  # title such as "unik menu for thursday"
        lines = lines[1:]
    return lines


MENU_SYSTEM = """You tidy a numbered list of lunch dishes for a staff-lunch bot.
Each input line is ONE dish. Return ONLY JSON with exactly one item per line, same ids:
{"items":[{"id":1,"name":"Plain rice with palava sauce (beef & egg)","includes":["beef","egg"],"options":[]}]}

Rules:
- name: the dish in normal capitalisation. Expand "lt soup" to "light soup". Keep it faithful;
  do not add or remove food items.
- includes: foods, sides and sauces that come with the dish (anything joined with "&" or "+").
- options: ONLY when the line itself offers alternatives separated by "/", e.g.
  "fish/chicken/beef" -> [{"label":"protein","choices":["fish","chicken","beef"],"required":true}].
  If the line has no "/", options MUST be an empty list. Never invent choices.
- Never merge, skip, reorder or add dishes."""


def parse_menu(raw_text):
    lines = dish_lines(raw_text)
    if not lines:
        return []
    numbered = "\n".join(f"{i}. {ln}" for i, ln in enumerate(lines, start=1))
    try:
        returned = _ask_json(MENU_SYSTEM, numbered).get("items") or []
    except Exception as e:  # model hiccup: fall back to plain dish lines
        print("MENU PARSE ERROR", repr(e))
        returned = []
    by_id = {}
    for it in returned:
        try:
            by_id[int(it.get("id"))] = it
        except (TypeError, ValueError, AttributeError):
            pass

    items = []
    for i, ln in enumerate(lines, start=1):
        it = by_id.get(i) or {}
        options = []
        if "/" in ln:  # only a slash in the dish line can create a choice
            for o in it.get("options") or []:
                choices = []
                for c in o.get("choices") or []:
                    c = str(c).strip()
                    if c and c.lower() in ln and c not in choices:
                        choices.append(c)
                if o.get("label") and len(choices) >= 2:
                    options.append({"label": str(o["label"]), "choices": choices, "required": True})
        base = re.sub(r"\blt soup\b", "light soup", ln)
        name = str(it.get("name") or "").strip()
        if not name or difflib.SequenceMatcher(None, name.lower(), base).ratio() < 0.75:
            name = base.capitalize()  # model name does not match the real line: trust the line
        items.append({
            "id": i,  # numbering always comes from code
            "name": name,
            "includes": [str(x) for x in (it.get("includes") or [])],
            "options": options,
        })
    return items


# staff replies

ORDER_SYSTEM = """You read one WhatsApp reply from a staff member ordering lunch.
You get today's menu (JSON), their current order if any, and their message.

Return ONLY JSON:
{"intent":"order|cancel|menu|unclear",
 "lines":[{"item_id":null,"choices":{},"custom":""}],
 "notes":"","not_on_menu":[]}

Rules:
- intent "order": they picked food. "lines" has one entry per meal they want.
- A line with a normal menu dish: set item_id (match by number or loosely by name) and
  put any option they stated in choices, e.g. {"protein":"fish"}, using exactly one of
  that option's listed choices. Leave out options they did not state.
- COMBINATIONS: staff sometimes mix parts of different dishes, e.g. "fried rice with
  omelette and sausage" where omelette and sausage come from another dish. Then use
  item_id null and write custom as a short plain description built ONLY from base
  foods, options and included components that appear on today's menu, like
  "Fried rice + omelette + sausage".
- Anything they ask for that is not on today's menu at all goes in not_on_menu
  (list of strings). Do not invent it into a line.
- notes: extras like "no pepper" or "extra sauce"; else "".
- intent "cancel": they want to cancel. intent "menu": they ask what is available.
  intent "unclear": you cannot tell.
- If an existing order is given and the message answers its open question or changes
  it, return the full updated set of lines (not just the change)."""


def parse_order(items, existing, text):
    payload = {
        "menu": items,
        "existing_order": (
            {"lines": existing["lines"], "notes": existing["notes"]} if existing else None
        ),
        "message": text,
    }
    return _ask_json(ORDER_SYSTEM, json.dumps(payload, ensure_ascii=False))

# import json
# import os
# import re
# from openai import OpenAI

# MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
# _client = None

# def _get_client():
#     global _client
#     if _client is None:
#         _client = OpenAI()  # reads OPENAI_API_KEY from .env

#     return _client

# # MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
# # _client = None


# # def _get_client():
# #     global _client
# #     if _client is None:
# #         from anthropic import Anthropic  # reads ANTHROPIC_API_KEY

# #         _client = Anthropic()
# #     return _client


# def _ask_json(system, user):
#     r = _get_client().messages.create(
#         model=MODEL,
#         max_tokens=1500,
#         system=system,
#         messages=[{"role": "user", "content": user}],
#     )
#     text = "".join(b.text for b in r.content if b.type == "text").strip()
#     text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
#     return json.loads(text)
####################
# import json
# import os
# import re

# MODEL = os.getenv("AI_MODEL", "gpt-5-nano")
# _client = None


# def _get_client():
#     global _client
#     if _client is None:
#         from openai import OpenAI  # reads OPENAI_API_KEY

#         _client = OpenAI()
#     return _client


# def _ask_json(system, user):
#     r = _get_client().chat.completions.create(
#         model=MODEL,
#         messages=[
#             {"role": "system", "content": system},
#             {"role": "user", "content": user},
#         ],
#         response_format={"type": "json_object"},
#         reasoning_effort="minimal",
#         max_completion_tokens=4000,
#     )
#     text = (r.choices[0].message.content or "").strip()
#     text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
#     return json.loads(text)

# MENU_SYSTEM = """You convert a daily staff-lunch menu posted on WhatsApp into JSON.
# The text may use fancy small-caps unicode letters, emojis and *asterisks*. Normalise
# everything to plain, normal-case English (e.g. "Fried rice with fish").

# Return ONLY JSON, no prose, in this shape:
# {"items":[{"id":1,"name":"...","includes":["coleslaw","chilli sauce"],"options":[
#    {"label":"protein","choices":["fish","grilled chicken"],"required":true}]}]}

# Rules:
# - ids are 1,2,3... in the order the dishes appear.
# - "options" only when the menu offers alternatives to choose between, e.g.
#   "fish/grilled chicken". The person must pick one.
# - "includes" lists the other components that come with the dish (sides, sauces,
#   pepper, omelette, sausage, coleslaw...). Things joined with "&" or "+" are included,
#   not alternatives.
# - Keep names short and clear. Do not invent dishes, components or choices.
# - There are no prices; the company pays for lunch."""


# def parse_menu(raw_text):
#     items = _ask_json(MENU_SYSTEM, raw_text).get("items") or []
#     clean = []
#     for n, it in enumerate(items, start=1):
#         if not isinstance(it, dict) or not it.get("name"):
#             continue
#         options = []
#         for o in it.get("options") or []:
#             choices = [str(c) for c in (o.get("choices") or []) if c]
#             if o.get("label") and choices:
#                 options.append({
#                     "label": str(o["label"]),
#                     "choices": choices,
#                     "required": o.get("required", True) is not False,
#                 })
#         clean.append({
#             "id": n,
#             "name": str(it["name"]),
#             "includes": [str(x) for x in (it.get("includes") or [])],
#             "options": options,
#         })
#     return clean


# ORDER_SYSTEM = """You read one WhatsApp reply from a staff member ordering lunch.
# You get today's menu (JSON), their current order if any, and their message.

# Return ONLY JSON:
# {"intent":"order|cancel|menu|unclear",
#  "lines":[{"item_id":null,"choices":{},"custom":""}],
#  "notes":"","not_on_menu":[]}

# Rules:
# - intent "order": they picked food. "lines" has one entry per meal they want.
# - A line with a normal menu dish: set item_id (match by number or loosely by name) and
#   put any option they stated in choices, e.g. {"protein":"fish"}, using exactly one of
#   that option's listed choices. Leave out options they did not state.
# - COMBINATIONS: staff sometimes mix parts of different dishes, e.g. "fried rice with
#   omelette and sausage" where omelette and sausage come from another dish. Then use
#   item_id null and write custom as a short plain description built ONLY from base
#   foods, options and included components that appear on today's menu, like
#   "Fried rice + omelette + sausage".
# - Anything they ask for that is not on today's menu at all goes in not_on_menu
#   (list of strings). Do not invent it into a line.
# - notes: extras like "no pepper" or "extra sauce"; else "".
# - intent "cancel": they want to cancel. intent "menu": they ask what is available.
#   intent "unclear": you cannot tell.
# - If an existing order is given and the message answers its open question or changes
#   it, return the full updated set of lines (not just the change)."""


# def parse_order(items, existing, text):
#     payload = {
#         "menu": items,
#         "existing_order": (
#             {"lines": existing["lines"], "notes": existing["notes"]} if existing else None
#         ),
#         "message": text,
#     }
#     return _ask_json(ORDER_SYSTEM, json.dumps(payload, ensure_ascii=False))
