import re


def normalize_phone(raw):
    """International format, digits only, no '+'. Turns 0241234567 into 233241234567."""
    d = re.sub(r"\D", "", raw or "")
    if d.startswith("0") and len(d) == 10:
        d = "233" + d[1:]
    return d
