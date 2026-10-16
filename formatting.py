"""Deterministic answer formatting driven by the format hints inside the question (Exact Match scoring)."""
from __future__ import annotations

import datetime as dt
import math
import re
from decimal import ROUND_HALF_UP, Decimal

from excel_parser import strip_footnote

WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4}


def _decimals(q: str) -> int | None:
    m = re.search(r"(\w+)\s+decimal places?", q, re.I)
    if not m:
        return None
    w = m.group(1).lower()
    return int(w) if w.isdigit() else WORD_NUM.get(w)


def _num(x, q: str) -> str:
    x = float(x)
    if math.isnan(x):
        return ""
    d = _decimals(q)
    if d is not None:
        return str(Decimal(repr(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))
    if re.search(r"\binteger", q, re.I) or x == int(x):
        return str(int(Decimal(repr(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    return f"{x:.10g}"


def _candidates(q: str) -> list[str]:
    """Options the question enumerates, e.g. 'Rank the following sectors ...: A, B, and C.'"""
    m = re.search(r":\s*([^.:]+?)\.\s", q + " ")
    if not m:
        return []
    seg = m.group(1)
    if "?" in seg or "'" in seg or '"' in seg:
        return []
    parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", seg) if p.strip()]
    return parts if len(parts) >= 2 and all(len(p) <= 60 for p in parts) else []


def _text(s: str, q: str, cands: list[str]) -> str:
    s = strip_footnote(re.sub(r"\s+", " ", str(s)).strip()).strip(" '\"")
    hits = [c for c in cands if c.lower() in s.lower() or s.lower() in c.lower()]
    return hits[0] if len(hits) == 1 else s


def _one(v, q: str, cands: list[str]) -> str:
    if hasattr(v, "item"):                       # numpy scalar
        v = v.item()
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        return _num(v, q)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ T]00:00(:00)?(\.0+)?", s)
    if m:                                        # stringified midnight timestamp -> date
        return m.group(1)
    if re.fullmatch(r"-?[\d,]*\.?\d+", s):
        return _num(s.replace(",", ""), q)
    if s.lower() in ("yes", "no", "true", "false"):
        return "Yes" if s.lower() in ("yes", "true") else "No"
    return _text(s, q, cands)


def format_answer(v, q: str) -> str:
    cands = _candidates(q)
    if hasattr(v, "tolist"):                     # pandas Series / numpy array
        v = v.tolist()
    if isinstance(v, str) and cands and ", " in v:  # a list already joined into one string
        v = [x for x in v.split(", ") if x]
    if isinstance(v, (list, tuple)):
        if len(v) == 1:
            return _one(v[0], q, cands)
        return ", ".join(_one(x, q, cands) for x in v)
    return _one(v, q, cands)
