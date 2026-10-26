"""All Claude calls live here: menu parsing and order parsing."""
import json
import os
import re
from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
_client = None

def _get_client():

    global _client

    if _client is None:

        _client = OpenAI()  # reads OPENAI_API_KEY from .env

    return _client

# MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
# _client = None


# def _get_client():
#     global _client
#     if _client is None:
#         from anthropic import Anthropic  # reads ANTHROPIC_API_KEY

#         _client = Anthropic()
#     return _client


def _ask_json(system, user):
    r = _get_client().messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text)


MENU_SYSTEM = """You convert a daily staff-lunch menu posted on WhatsApp into JSON.
The text may use fancy small-caps unicode letters, emojis and *asterisks*. Normalise
everything to plain, normal-case English (e.g. "Fried rice with fish").

Return ONLY JSON, no prose, in this shape:
{"items":[{"id":1,"name":"...","includes":["coleslaw","chilli sauce"],"options":[
   {"label":"protein","choices":["fish","grilled chicken"],"required":true}]}]}

Rules:
- ids are 1,2,3... in the order the dishes appear.
- "options" only when the menu offers alternatives to choose between, e.g.
  "fish/grilled chicken". The person must pick one.
- "includes" lists the other components that come with the dish (sides, sauces,
  pepper, omelette, sausage, coleslaw...). Things joined with "&" or "+" are included,
  not alternatives.
- Keep names short and clear. Do not invent dishes, components or choices.
- There are no prices; the company pays for lunch."""


def parse_menu(raw_text):
    return _ask_json(MENU_SYSTEM, raw_text)["items"]


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
