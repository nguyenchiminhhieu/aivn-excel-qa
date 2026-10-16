"""Exact-match scoring of a submission against a reference CSV, with a per-question diff."""
import csv
import sys


def load(p):
    with open(p, encoding="utf-8-sig") as fh:
        return {r["id"]: r["answer"].strip() for r in csv.DictReader(fh)}


if __name__ == "__main__":
    pred, gold = load(sys.argv[1]), load(sys.argv[2])
    ok = 0
    for k, g in gold.items():
        p = pred.get(k, "")
        ok += p == g
        if p != g:
            print(f"{k}: pred={p!r} gold={g!r}")
    print(f"EM = {ok}/{len(gold)} = {ok / len(gold):.1%}")
