"""Exact-match scoring: python scripts/evaluate.py <submission.csv> <gold.csv | answers.json>

Gold can be a CSV with columns id,answer (e.g. BTC's sample_submission.csv) or a JSON list of {"id", "answer"}
(e.g. BTC's answers.json). Ids are compared as strings; answers after stripping surrounding whitespace, exactly.
"""
import csv
import json
import sys


def load(path):
    if path.endswith(".json"):
        with open(path, encoding="utf-8-sig") as fh:
            return {str(r["id"]): str(r["answer"]).strip() for r in json.load(fh)}
    with open(path, encoding="utf-8-sig") as fh:
        return {str(r["id"]): (r["answer"] or "").strip() for r in csv.DictReader(fh)}


if __name__ == "__main__":
    pred, gold = load(sys.argv[1]), load(sys.argv[2])
    ok = 0
    for k, g in gold.items():
        p = pred.get(k, "")
        ok += p == g
        if p != g:
            print(f"{k}: pred={p!r} gold={g!r}")
    print(f"EM = {ok}/{len(gold)} = {ok / max(len(gold), 1):.1%}")
