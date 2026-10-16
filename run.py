"""Excel QA – command-line entry point.

    python run.py --data-dir <folder with xlsx_files/ and queries.json> --out submission.csv

Writes exactly one row per query id (an empty answer if a question cannot be solved), so the output file is
always valid. See README.md for configuration.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import os
import time
from collections import Counter
from pathlib import Path

from engine import v3
from llm import check_access, make_client


def _files(data: Path, q: dict) -> list[str]:
    """`file_name` may be a single name or a list of names (multi-file questions)."""
    names = q.get("file_names") or q.get("file_name") or []
    names = [names] if isinstance(names, str) else list(names)
    return [str(data / "xlsx_files" / n) for n in names]


def main():
    ap = argparse.ArgumentParser(description="Answer questions about Excel files (AI2026, topic 2).")
    ap.add_argument("--data-dir", required=True, help="folder containing xlsx_files/ and queries.json")
    ap.add_argument("--out", default="submission.csv", help="output CSV (id,answer)")
    ap.add_argument("--workers", type=int, default=6, help="questions processed in parallel")
    ap.add_argument("--ids", help="comma-separated subset of query ids (debugging)")
    ap.add_argument("--trace", help="optional JSONL file with per-question candidates and decisions")
    args = ap.parse_args()

    t0 = time.time()
    data = Path(args.data_dir)
    queries = json.loads((data / "queries.json").read_text(encoding="utf-8-sig"))
    if args.ids:
        keep = set(args.ids.split(","))
        queries = [q for q in queries if str(q["id"]) in keep]
    budget = float(os.environ.get("QA_TIME_BUDGET", 0.8 * 120 * max(len(queries), 1)))
    deadline = t0 + 0.85 * budget                     # after this, no escalation: answer with what we have

    for q in queries:                                 # parse each workbook once; forked executors inherit it
        try:
            v3.load(_files(data, q))
        except Exception as e:  # noqa: BLE001
            print(f"[{q['id']}] cannot read workbook: {e}")

    client = make_client()
    check_access(client)

    def one(q):
        usage, start = Counter(), time.time()
        paths = _files(data, q)
        try:
            ans, tr = v3.solve(client, paths, q["query"], usage, deadline)
        except Exception as e:  # noqa: BLE001
            ans, tr = "", {"error": repr(e)}
        if not ans and paths:                         # last resort: one careful direct reading
            try:
                c = v3.direct_candidate(client, v3.load(paths), q["query"], "low", usage)
                ans = v3.render_answer(c["answer"], q["query"], None) if c["answer"] else ""
                tr["fallback"] = True
            except Exception as e:  # noqa: BLE001
                tr["fallback_error"] = repr(e)
        tr.update({"latency": round(time.time() - start, 2), "calls": usage["calls"],
                   "prompt_tokens": usage["prompt"], "completion_tokens": usage["completion"]})
        return q["id"], ans, tr

    answers, traces = {}, {}
    with cf.ThreadPoolExecutor(args.workers) as ex:
        for qid, ans, tr in ex.map(one, queries):
            answers[qid], traces[qid] = ans, tr

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "answer"])
        for q in queries:
            w.writerow([q["id"], answers.get(q["id"], "")])
    if args.trace:
        with open(args.trace, "w", encoding="utf-8") as fh:
            for q in queries:
                fh.write(json.dumps({"id": q["id"], "query": q["query"], "answer": answers.get(q["id"]),
                                     **traces.get(q["id"], {})}, ensure_ascii=False, default=str) + "\n")
    empty = sum(1 for q in queries if not answers.get(q["id"]))
    if empty:
        print(f"warning: {empty}/{len(queries)} questions have an empty answer (use --trace to see why)")
    tok = sum(t.get("prompt_tokens", 0) + t.get("completion_tokens", 0) for t in traces.values())
    print(f"done {len(queries)} questions in {time.time() - t0:.1f}s; "
          f"tokens/question {tok / max(len(queries), 1):.0f} -> {args.out}")
    if queries and empty == len(queries):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
