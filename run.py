"""Excel QA – command-line entry point (also the Docker ENTRYPOINT).

    python run.py --xlsx-dir <dir> --queries <queries.json> --output <csv> \
        --openrouter-key <key> --openrouter-model qwen/qwen3.7-flash --openrouter-base-url https://openrouter.ai/api/v1
    python run.py <input> [<output>]
    python run.py --data-dir <input> --out <output>

The first form is the one the grading system uses (six parameters).

<input>   folder with queries.json and the .xlsx files (directly or in xlsx_files/), or the queries.json file.
          Default: $DATA_DIR, else /data, else the current folder.
<output>  submission CSV path, or a folder (then <folder>/submission.csv is written).
          Default: $OUTPUT_PATH, else /output/submission.csv when /output exists, else <input>/submission.csv.

The CSV has exactly the columns id,answer and one row per distinct query id. It is written before the first
question is solved and rewritten after every answer, so a valid file exists even if the run is interrupted.
See README.md for configuration (API key via environment variable or .env, never in source code).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path


def _find_queries(inp: Path) -> Path:
    if inp.is_file():
        return inp
    if (inp / "queries.json").is_file():
        return inp / "queries.json"
    found = sorted(inp.rglob("queries.json"), key=lambda p: len(p.parts))
    if not found:
        raise SystemExit(f"queries.json not found under {inp}")
    return found[0]


class Files:
    """Resolve `file_name` against <base>/xlsx_files/, <base>/, then any .xlsx below <base> (case-insensitive)."""

    def __init__(self, base: Path):
        self.base = base
        self.index = {}
        for p in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if p.suffix.lower() in (".xlsx", ".xlsm") and p.is_file():
                self.index[p.name.lower()] = p

    def resolve(self, name: str) -> str:
        for p in (self.base / "xlsx_files" / name, self.base / name):
            if p.is_file():
                return str(p)
        return str(self.index.get(Path(name).name.lower(), self.base / "xlsx_files" / name))

    def of(self, q: dict) -> list[str]:
        """`file_name` may be a single name or a list of names (multi-file questions)."""
        names = q.get("file_names") or q.get("file_name") or []
        names = [names] if isinstance(names, str) else list(names)
        return [self.resolve(n) for n in names]


def _out_paths(arg: str | None, base: Path) -> list[Path]:
    if arg:
        p = Path(arg)
        return [p / "submission.csv" if (p.is_dir() or not p.suffix) else p]
    if os.environ.get("OUTPUT_PATH"):
        return _out_paths(os.environ["OUTPUT_PATH"], base)
    if Path("/output").is_dir():
        return [Path("/output/submission.csv")]
    return [base / "submission.csv"]


def _id_key(i):
    """Rows are sorted by id ascending (BTC FAQ, question 04): numerically when the id is a natural number."""
    s = str(i).strip()
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def _write_csv(paths: list[Path], ids: list, answers: dict):
    for path in paths:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["id", "answer"])
                for i in ids:
                    w.writerow([i, answers.get(i, "")])
            os.replace(tmp, path)
        except OSError as e:
            print(f"warning: cannot write {path}: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Answer questions about Excel files (AI2026, topic 2).")
    ap.add_argument("input", nargs="?", help="folder with queries.json and .xlsx files (or the queries.json)")
    ap.add_argument("output", nargs="?", help="output CSV path or folder")
    ap.add_argument("--data-dir", "--input", "-i", dest="data_dir", help="same as <input>")
    ap.add_argument("--out", "--output", "-o", dest="out", help="same as <output>")
    ap.add_argument("--xlsx-dir", help="folder with the .xlsx files (grading parameter)")
    ap.add_argument("--queries", help="path to queries.json (grading parameter)")
    ap.add_argument("--openrouter-key", "--api-key", dest="api_key",
                    help="OpenRouter API key (or the OPENROUTER_API_KEY environment variable)")
    ap.add_argument("--openrouter-model", help="model id, default qwen/qwen3.7-flash")
    ap.add_argument("--openrouter-base-url", help="default https://openrouter.ai/api/v1")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("QA_WORKERS", 20)),
                    help="questions processed in parallel (default 20; LLM calls are I/O bound)")
    ap.add_argument("--ids", help="comma-separated subset of query ids (debugging)")
    ap.add_argument("--trace", help="optional JSONL file with per-question candidates and decisions")
    args = ap.parse_args()
    t0 = time.time()
    for value, var in ((args.api_key, "OPENROUTER_API_KEY"), (args.openrouter_model, "QA_MODEL"),
                       (args.openrouter_base_url, "OPENROUTER_BASE_URL")):
        if value:
            os.environ[var] = value

    from excelqa import solver                       # imported after the key/model/url are in the environment
    from excelqa.llm import check_access, make_client

    inp = args.queries or args.data_dir or args.input or os.environ.get("DATA_DIR") or \
        ("/data" if Path("/data").is_dir() else ".")
    qpath = _find_queries(Path(inp))
    base = qpath.parent
    files = Files(Path(args.xlsx_dir) if args.xlsx_dir else base)
    queries = json.loads(qpath.read_text(encoding="utf-8-sig"))
    if args.ids:
        keep = set(args.ids.split(","))
        queries = [q for q in queries if str(q["id"]) in keep]
    seen, unique = set(), []
    for q in queries:                                 # one row per id, first occurrence wins
        if str(q["id"]) not in seen:
            seen.add(str(q["id"]))
            unique.append(q)
    queries = unique
    ids = sorted((q["id"] for q in queries), key=_id_key)
    outs = _out_paths(args.out or args.output, base)
    answers: dict = {}
    lock = threading.Lock()
    _write_csv(outs, ids, answers)                    # a valid file exists from the start

    # 108 s per question = 36 min per 20 questions: the limit is 40 min, the rest covers container start and network
    budget = float(os.environ.get("QA_TIME_BUDGET", 108 * max(len(queries), 1)))
    deadline = t0 + 0.85 * budget                     # after this, no escalation: answer with what we have

    def stop_at_budget():                             # hard stop: the limit is 40 min per 20 questions
        time.sleep(max(0.0, t0 + budget - time.time()))
        with lock:
            _write_csv(outs, ids, answers)
            print(f"time budget {budget:.0f}s reached: {len(answers)}/{len(ids)} answered, the rest left empty",
                  file=sys.stderr, flush=True)
            os._exit(0)
    threading.Thread(target=stop_at_budget, daemon=True).start()

    for q in queries:                                 # parse each workbook once; forked executors inherit it
        try:
            solver.load(files.of(q))
        except Exception as e:  # noqa: BLE001
            print(f"[{q['id']}] cannot read workbook: {e}", file=sys.stderr)

    client = make_client()
    check_access(client)

    def one(q):
        usage, start = Counter(), time.time()
        paths = files.of(q)
        try:
            ans, tr = solver.solve(client, paths, q["query"], usage, deadline)
        except Exception as e:  # noqa: BLE001
            ans, tr = "", {"error": repr(e)}
        if not ans and paths:                         # last resort: one careful direct reading
            try:
                c = solver.direct_candidate(client, solver.load(paths), q["query"], "low", usage)
                ans = solver.render_answer(c["answer"], q["query"], None) if c["answer"] else ""
                tr["fallback"] = True
            except Exception as e:  # noqa: BLE001
                tr["fallback_error"] = repr(e)
        ans = " ".join(str(ans).split()) if ans is not None else ""      # one line, final answer only
        tr.update({"latency": round(time.time() - start, 2), "calls": usage["calls"],
                   "prompt_tokens": usage["prompt"], "completion_tokens": usage["completion"]})
        return q["id"], ans, tr

    traces = {}
    with cf.ThreadPoolExecutor(max(1, min(args.workers, len(queries)))) as ex:
        for fut in cf.as_completed([ex.submit(one, q) for q in queries]):
            qid, ans, tr = fut.result()
            with lock:
                answers[qid], traces[qid] = ans, tr
                _write_csv(outs, ids, answers)

    if args.trace:
        with open(args.trace, "w", encoding="utf-8") as fh:
            for q in queries:
                fh.write(json.dumps({"id": q["id"], "query": q["query"], "answer": answers.get(q["id"]),
                                     **traces.get(q["id"], {})}, ensure_ascii=False, default=str) + "\n")
    empty = sum(1 for i in ids if not answers.get(i))
    if empty:
        print(f"warning: {empty}/{len(ids)} questions have an empty answer (use --trace to see why)", file=sys.stderr)
    tok = sum(t.get("prompt_tokens", 0) + t.get("completion_tokens", 0) for t in traces.values())
    print(f"done {len(ids)} questions in {time.time() - t0:.1f}s; tokens/question {tok / max(len(ids), 1):.0f} "
          f"-> {', '.join(map(str, outs))}")
    if ids and empty == len(ids):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
