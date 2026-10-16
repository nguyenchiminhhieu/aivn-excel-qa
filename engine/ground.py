"""Question grounding: match question phrases to header paths and rows, then serialize the relevant cells.

Combines three techniques with published evidence on hierarchical-table QA:
  * phrase -> header-node alignment (SemiBonsai's uncertainty resolver, TreeThinker's keyword/header matching),
  * relevance-ranked context instead of a fixed sample (TabGR's question-guided ranking, SemiBonsai pruning),
  * cell serialization with the full row path, column path and A1 position (TabGR triples, SheetBrain positions).
Everything here is deterministic string matching: no LLM call.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from excel_parser import _fmt

STOP = set("""a an the of in on at to for from by with and or is are was were be been being what which who whom whose
how many much does do did has have had than then that this these those there their its it as into per each all any
only answer number integer value values table row rows column columns give find show list name exactly using use
decimal decimals place places separated comma one space without units commas total between among across within
during over under more less most least highest lowest largest smallest difference percentage percent rate share""".split())

_TOK = re.compile(r"[\w%]+", re.U)


def _tokens(s: str) -> list[str]:
    return [t for t in _TOK.findall(str(s).lower()) if t]


def _phrases(question: str, max_n: int = 4) -> list[str]:
    toks = _tokens(question)
    out = set()
    for n in range(1, max_n + 1):
        for i in range(len(toks) - n + 1):
            gram = toks[i:i + n]
            if all(g in STOP for g in gram) or (n == 1 and (len(gram[0]) < 3 and not gram[0].isdigit())):
                continue
            if gram[0] in STOP or gram[-1] in STOP:
                continue
            out.add(" ".join(gram))
    return sorted(out)


def _contains(text_tokens: list[str], phrase: str) -> bool:
    p = phrase.split()
    n = len(p)
    return any(text_tokens[i:i + n] == p for i in range(len(text_tokens) - n + 1))


def ground(ir, frames_names: dict, question: str, max_rows: int = 20, max_cols: int = 6, groups=None) -> str:
    """Return a FOCUS block (or "" when nothing in the question matches the sheet)."""
    groups = groups or {}
    phrases = _phrases(question)
    if not phrases:
        return ""
    items = []                                   # (kind, table, obj, tokens)
    for t in ir:
        for c in t.cols:
            items.append(("col", t, c, _tokens(" ".join(c.path))))
        for r in t.rows:
            items.append(("row", t, r, _tokens(" ".join(r.path + r.labels))))
    df = Counter()
    for _, _, _, toks in items:
        for p in phrases:
            if _contains(toks, p):
                df[p] += 1
    n_items = max(len(items), 1)
    idf = {p: math.log(1 + n_items / (1 + df[p])) for p in phrases if df[p]}
    if not idf:
        return ""
    # drop phrases subsumed by a longer matched phrase
    keep = [p for p in idf if not any(p != q and f" {p} " in f" {q} " for q in idf)]

    def score(toks):
        hit = [p for p in keep if _contains(toks, p)]
        return sum(len(p.split()) * idf[p] for p in hit), hit

    blocks = []
    for t in ir:
        names = frames_names[t.id]
        cols = sorted(((score(_tokens(" ".join(c.path))), c) for c in t.cols), key=lambda x: -x[0][0])
        cols = [(s, c) for s, c in cols if s[0] > 0][:max_cols]
        rows = sorted(((score(_tokens(" ".join(r.path + r.labels))), r) for r in t.rows), key=lambda x: -x[0][0])
        rows = [(s, r) for s, r in rows if s[0] > 0]
        if not rows and not cols:
            continue
        best = rows[0][0][0] if rows else 0
        rows = [(s, r) for s, r in rows if s[0] >= 0.34 * best][:max_rows]
        show_cols = [c for _, c in cols] or t.cols[:8]
        lines = [f"{t.id} (sheet '{t.sheet}'):"]
        if cols:
            lines.append("  matching columns: " + "; ".join(f"'{names[c.id]}' [{' > '.join(c.path)}]" for _, c in cols))
        matched = {c.id for _, c in cols}
        rel = [g for g in groups.get(t.id, []) if matched & set(g[2]) or score(_tokens(g[1]))[0] > 0]
        if rel:
            lines.append("  column groups (sums of sub-columns): " + "; ".join(f"'{g[0]}'" for g in rel[:6]))
        for (_, hit), r in sorted(rows, key=lambda x: x[1].id):
            flags = ("summary " if r.is_summary else "") + ("has-children" if r.has_children else "")
            cells = "; ".join(f"'{names[c.id]}'={_fmt(r.values.get(c.id))} ({t.ref(r, c).split('!')[-1]})" for c in show_cols)
            lines.append(f"  row {r.id} [{' > '.join(r.path)}]{' (' + flags.strip() + ')' if flags.strip() else ''}: {cells}")
        if len(lines) > 1:
            blocks.append("\n".join(lines))
    if not blocks:
        return ""
    return ("FOCUS — cells whose row/column headers match words of the question (string matching, may be incomplete "
            "or include distractors; always check against the MAP):\n" + "\n".join(blocks))


def expected_type(question: str) -> str | None:
    """Coarse answer type requested by the question: 'yesno', 'number', 'list' or None."""
    q = question.lower()
    if re.search(r"\byes or no\b|\byes/no\b|'yes' or 'no'|\"yes\" or \"no\"|có hoặc không|예 또는 아니오|是或否", q):
        return "yesno"
    if re.search(r"separated by (a )?comma|as two |as three |comma and one space|\blist\b|`[^`]*,[^`]*`", q):
        return "list"
    if re.search(r"\binteger\b|decimal place|\bhow many\b|chữ số thập phân|số nguyên|소수점|정수|小数|整数", q):
        return "number"
    return None


def type_ok(final: str, etype: str | None) -> bool:
    if not etype or final is None:
        return True
    f = str(final).strip()
    if etype == "yesno":
        return f in ("Yes", "No")
    if etype == "number":
        return bool(re.fullmatch(r"-?\d+(\.\d+)?", f))
    if etype == "list":
        return ", " in f or "," in f
    return True
