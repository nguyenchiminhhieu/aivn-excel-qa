"""Solved-example retrieval: a few questions from OTHER workbooks with the same kind of wording, each with how the
answer key reads it.

Evidence: similar (not random) solved examples carry an answer key's conventions far better than rules alone
(DAIL-SQL, VLDB 2024: exact match 22.1 -> 71.9 with similar examples, 51.6 with random ones), and random
examples can hurt. So an example is shown only when it is similar enough; otherwise none is shown.
Examples never come from the workbook(s) of the question itself, so they teach reading, not values.
Similarity is computed on the question SKELETON: words that occur in the workbook's own labels (topic words such as
industries, products, places) are masked, as in DAIL-SQL's masked question similarity, so examples are matched by
how the question is asked ("second-oldest ... on the admission date", "directly under", "percentage points").
The bank (excelqa/examples_bank.json) is built by bench_btc/build_bank.py from the analysis split only.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

BANK = Path(__file__).resolve().parent / "examples_bank.json"
MIN_SIM = 0.40                  # measured: 59% of retrieved examples share a reasoning tag (random: 19%)
_STOP = set("""a an the of in on at to for from by with and or is are was were be what which who how many much does do
did has have had that this these those their its it as all each per any only answer number integer value give find
show name separated comma one space without units commas decimal decimals place places rounded round""".split())
# answer-format instructions are shared by most questions and say nothing about how to read them
_FORMAT = re.compile(r"(answer (with|as|in)|chỉ trả lời|trả lời bằng|réponds|répondez|답하십시오|只回答|保留).*$", re.I | re.S)


def _tokens(text: str) -> list[str]:
    core = _FORMAT.sub("", text)
    return [t for t in re.findall(r"[a-zà-ỹ]+", core.lower()) if t not in _STOP and len(t) > 2]


def skeleton(question: str, table_text: str) -> list[str]:
    """Question words that are not words of the workbook's labels (table_text: normalised label text)."""
    vocab = set(re.findall(r"[a-zà-ỹ]+", table_text.lower()))
    return [t for t in _tokens(question) if t not in vocab]


class Bank:
    def __init__(self, items: list[dict]):
        self.items = items
        self.vecs = [Counter(it.get("skeleton") or _tokens(it["question"])) for it in items]
        df = Counter(t for v in self.vecs for t in v)
        n = max(len(items), 1)
        self.idf = {t: math.log(1 + n / c) for t, c in df.items()}
        self.norms = [self._norm(v) for v in self.vecs]

    def _norm(self, v: Counter) -> float:
        return math.sqrt(sum((c * self.idf.get(t, 0.0)) ** 2 for t, c in v.items())) or 1.0

    def similar(self, question: str, files: list[str], k: int, table_text: str = "") -> list[tuple[float, dict]]:
        q = Counter(skeleton(question, table_text))
        qn = self._norm(q)
        own = {Path(f).name.lower() for f in files}
        scored = []
        for it, v, n in zip(self.items, self.vecs, self.norms):
            if own & {f.lower() for f in it["files"]}:
                continue                                   # never the question's own workbook
            dot = sum(c * v.get(t, 0) * self.idf.get(t, 0.0) ** 2 for t, c in q.items())
            scored.append((dot / (qn * n), it))
        scored.sort(key=lambda x: -x[0])
        out, seen = [], set()
        for s, it in scored:
            if s < MIN_SIM or len(out) >= k:
                break
            if it["pattern"] in seen:                      # one example per question pattern
                continue
            seen.add(it["pattern"])
            out.append((s, it))
        return out


_BANK: Bank | None = None


def block(question: str, files: list[str], k: int, table_text: str = "") -> str:
    """The SOLVED EXAMPLES text for a prompt ("" when k == 0, no bank, or nothing similar enough)."""
    global _BANK
    if k <= 0 or not BANK.exists():
        return ""
    if _BANK is None:
        _BANK = Bank(json.loads(BANK.read_text(encoding="utf-8")))
    hits = _BANK.similar(question, files, k, table_text)
    if not hits:
        return ""
    lines = ["SOLVED EXAMPLES from other workbooks (same answer key). Use them only for how a question is read; "
             "never copy their values."]
    for _, it in hits:
        lines += [f"Q: {it['question']}", f"How it is read: {it['reading']}", f"Answer: {it['answer']}", ""]
    return "\n".join(lines).rstrip()
