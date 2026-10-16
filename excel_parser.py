"""Normalize messy Excel sheets into clean tables an LLM can ground on.

Pipeline per sheet:
  1. Read cells (cached formula values, else a small formula evaluator), coerce text numbers,
     fill merged ranges, record indentation (alignment indent / leading spaces) and % formats.
  2. Split the sheet into column blocks separated by fully empty columns (side-by-side tables).
  3. Classify rows: blank / caption / header / label_only / data, and split each block into tables
     (a new header band after data starts a new table; a repeated header band is skipped).
  4. Build column paths from stacked header rows, label columns from the data rows, and a row
     context from: an "Indent Level" column, cell indentation, section rows, or BLS-style group
     rows ("Total, ...", "X, 16 years and over"). Extra label columns carry their own hierarchy.
"""
from __future__ import annotations

import ast
import datetime as dt
import math
import re
from dataclasses import dataclass, field

import openpyxl
import pandas as pd
from openpyxl.utils import column_index_from_string, get_column_letter

YEAR_RE = re.compile(r"^(19|20)\d{2}$")
FOOTNOTE_RE = re.compile(r"(\s*\(\d+\))+$|\s*\d+/$|\s*\*+$")
COMPOUND_PREFIXES = {"self", "non", "full", "part", "year", "well", "co", "ex", "half", "low", "high"}
GROUP_RE = re.compile(r"^(total|all)\b", re.I)   # generic: "Total ...", "All ..." rows open a group
CODE_RE = re.compile(r"^[A-Z]{0,4}\d{1,6}(\s*[-–]\s*[A-Z]{0,4}\d{1,6})?[A-Z]?$|^[A-Z]{2,4}[0-9][A-Z0-9]{0,4}$")
PERIOD_RE = re.compile(r"^((19|20)\d{2}(\s*(Q[1-4]|H[12]|[A-Z][a-z]{2,8}\.?))?|Q[1-4]|H[12])$")
TOTAL_RE = re.compile(r"\b(total|subtotal|sub-total|grand total|all items)\b", re.I)
MISSING = {"-", "–", "—", "n/a", "na", "nan", "(x)", "(na)", "..", "...", "(d)", "(z)", "null"}
NUM_RE = re.compile(r"^\(?[-+−]?[$€£¥₫]?\s*[-+−]?\d[\d,]*(\.\d+)?\s*%?\)?$")
MAX_FULL_ROWS = 150          # larger tables are rendered as a column profile + sample rows


# ----------------------------------------------------------------------------- value helpers
def clean_text(v) -> str:
    if v is None:
        return ""
    s = str(v)
    # Syllable breaks ("Con-\nstruction") are joined; real compounds ("Self-\nemployed") keep the hyphen.
    s = re.sub(r"(\w+)-\s*\n\s*(?=[a-z])",
               lambda m: m.group(1) + ("-" if m.group(1).lower() in COMPOUND_PREFIXES else ""), s)
    return re.sub(r"\s+", " ", s).strip()


def strip_footnote(s: str) -> str:
    return FOOTNOTE_RE.sub("", s).strip()


SYMBOLS = {"+", "++", "+++", "-", "−", "–", "--", "x", "X", "✓", "✔", "✗", "√"}


class Sym(str):
    """A cell of a +/- or checkbox matrix: a categorical value, not a label."""


def to_number(v, keep_symbols: bool = False):
    """'$1,234' -> 1234, '(5,000)' -> -5000, '12.5%' -> 12.5, 'n/a' -> None; other values unchanged.
    keep_symbols: in +/- or checkbox matrices a dash is a value (negative), not a missing marker."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if keep_symbols and s in SYMBOLS:
        return Sym(s)
    if s.lower() in MISSING or re.fullmatch(r"\(\s*-\s*\d+\s*-\s*\)|\([A-Z]{1,2}\)", s):
        return None
    if re.fullmatch(r"[-+−]?0,\d+|[-+−]?\d+,\d{1,2}", s):           # decimal comma: "96,1", "0,403"
        try:
            return float(s.replace("−", "-").replace(",", "."))
        except ValueError:
            return v
    if re.fullmatch(r"[-+]?\d{1,3}([ \u00a0\u202f]\d{3})+(\.\d+)?", s):     # "1 200", "12 345.5"
        s = re.sub(r"[ \u00a0\u202f]", "", s)
    if not NUM_RE.match(s) or (s.count(",") and not re.search(r"\d{1,3}(,\d{3})+", s)):
        return v
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[()$€£¥₫,%\s]", "", s).replace("−", "-")
    try:
        x = float(s)
    except ValueError:
        return v
    x = -x if neg else x
    return int(x) if x == int(x) and "." not in s else x


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and math.isnan(v))


def is_value(v) -> bool:
    return is_num(v) or isinstance(v, (dt.datetime, dt.date, dt.time))


def is_year(v) -> bool:
    return is_num(v) and float(v) == int(v) and bool(YEAR_RE.match(str(int(v))))


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


_INTERVAL_PATTERNS = [
    (re.compile(r"^(\d+(?:\.\d+)?)\s*(?:to|-|–)\s*(\d+(?:\.\d+)?)\b", re.I), lambda m: (float(m[1]), float(m[2]))),
    (re.compile(r"^(\d+(?:\.\d+)?)\s*\w*\s*(?:and|or) (?:over|older|more)\b", re.I), lambda m: (float(m[1]), math.inf)),
    (re.compile(r"^(?:under|less than)\s*(\d+(?:\.\d+)?)\b", re.I), lambda m: (-math.inf, float(m[1]) - 1e-9)),
    (re.compile(r"^(\d+(?:\.\d+)?)\s+[a-z]+$", re.I), lambda m: (float(m[1]), float(m[1]))),
]


def label_interval(label: str):
    """'16 to 19 years' -> (16, 19); '65 years and over' -> (65, inf); '40 hours' -> (40, 40)."""
    s = strip_footnote(label).replace(",", "")
    for rx, f in _INTERVAL_PATTERNS:
        m = rx.match(s)
        if m:
            return f(m)
    return None


def _nest_intervals(rows: list[dict]):
    """Within runs of rows sharing a context, make interval rows children of the enclosing interval row."""
    open_: list[tuple[tuple, dict]] = []
    last_ctx = object()
    for row in rows:
        base = row["context"]
        if base != last_ctx:
            open_ = []
        last_ctx = base
        iv = label_interval(row["labels"][-1]) if row["labels"] else None
        if iv is None:
            open_ = []
            continue
        while open_ and not (open_[-1][0][0] <= iv[0] and iv[1] <= open_[-1][0][1] and open_[-1][0] != iv):
            open_.pop()
        if open_:
            parent = open_[-1][1]
            parent["has_children"] = True
            row["context"] = " > ".join(x for x in (parent["context"], parent["labels"][-1]) if x)
        open_.append((iv, row))


def _nest_by_sums(rows: list[dict], cols: list[int], max_k: int = 8):
    """Rows whose values equal the sum of the next k rows (same context) on most numeric columns are parents of
    those rows. Recovers hierarchy lost with indentation (e.g. Manufacturing = Durable + Nondurable goods)."""
    i = 0
    while i < len(rows):
        p = rows[i]
        pv = {c: p["values"].get(c) for c in cols if is_num(p["values"].get(c))}
        if len(pv) >= 3 and any(v for v in pv.values()):
            for k in range(2, max_k + 1):
                kids = rows[i + 1:i + 1 + k]
                if len(kids) < k or any(r["context"] != p["context"] or not r["labels"] for r in kids):
                    break
                ok = n = 0
                for c, v in pv.items():
                    vals = [r["values"].get(c) for r in kids]
                    if not all(is_num(x) for x in vals):
                        continue
                    n += 1
                    ok += abs(sum(vals) - v) <= max(k * 1.0, abs(v) * 0.002)
                # >= 3 additive columns must match exactly; averages/rates columns are allowed to differ.
                if ok >= 3 and ok >= 0.6 * n and sum(abs(x) for x in pv.values()) > 0:
                    path = " > ".join(x for x in (p["context"], p["labels"][-1]) if x)
                    for r in kids:
                        r["context"] = path
                    p["has_children"] = True
                    break
        i += 1


def _mark_parents(rows: list[dict]):
    """A row has children if some row's context path runs through it (indent, Indent Level, groups, intervals)."""
    ctxs = [r["context"] for r in rows]
    for row in rows:
        if not row["labels"]:
            continue
        path = " > ".join(x for x in (row["context"], row["labels"][-1]) if x)
        if any(c == path or c.startswith(path + " > ") for c in ctxs):
            row["has_children"] = True


# ----------------------------------------------------------------------------- formulas
_SAFE_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Call, ast.Name, ast.Load, ast.Constant,
               ast.List, ast.Tuple, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd,
               ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _safe_formula(tree, allowed_names: set) -> bool:
    """Workbook formulas are untrusted: allow only numbers/strings, arithmetic, comparisons and calls to the
    whitelisted spreadsheet functions (no attributes, subscripts, comprehensions or lambdas)."""
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            return False
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            return False
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.keywords):
            return False
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str)):
            return False
    return True


def _flat(a):
    out = []
    for x in a:
        if isinstance(x, (list, tuple)):
            out += _flat(x)
        elif is_num(x):
            out.append(x)
    return out


class _FormulaEval:
    """Tiny evaluator for formulas without cached values (refs, ranges, SUM/AVERAGE/MIN/MAX/COUNT/ROUND)."""

    FUNCS = {"SUM": lambda *a: sum(_flat(a)),
             "AVERAGE": lambda *a: (sum(_flat(a)) / len(_flat(a))) if _flat(a) else None,
             "MIN": lambda *a: min(_flat(a)), "MAX": lambda *a: max(_flat(a)),
             "COUNT": lambda *a: len(_flat(a)), "ROUND": lambda x, n=0: round(x, int(n)), "ABS": abs}
    TOKEN = re.compile(r"(?:'(?P<q>[^']+)'|(?P<s>[A-Za-z_][\w.]*))?(?P<bang>!)?\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)"
                       r"(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?")

    def __init__(self, wb):
        self.wb, self.memo = wb, {}

    def cell(self, sheet, coord):
        key = (sheet, coord)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = None                      # cycle guard
        try:
            v = self.wb[sheet][coord].value
        except KeyError:
            return None
        if isinstance(v, str) and v.startswith("="):
            v = self.formula(sheet, v)
        v = to_number(v)
        self.memo[key] = v
        return v

    def formula(self, sheet, f):
        def sub(m):
            if not m.group("bang") and (m.group("q") or m.group("s")):
                return m.group(0)                  # a function/name followed by a ref-like token
            sh = (m.group("q") or m.group("s")) if m.group("bang") else sheet
            if m.group("c2"):
                return f"_rng({sh!r},{m.group('c1')!r},{m.group('r1')},{m.group('c2')!r},{m.group('r2')})"
            return f"_cell({sh!r},{m.group('c1') + m.group('r1')!r})"

        expr = self.TOKEN.sub(sub, f[1:]).replace("^", "**").replace("<>", "!=")
        env = dict(self.FUNCS)
        env["_cell"] = lambda sh, c: self.cell(sh, c) if is_num(self.cell(sh, c)) else 0
        env["_rng"] = lambda sh, c1, r1, c2, r2: [
            self.cell(sh, f"{get_column_letter(c)}{r}")
            for r in range(r1, r2 + 1)
            for c in range(column_index_from_string(c1), column_index_from_string(c2) + 1)]
        try:
            tree = ast.parse(expr, mode="eval")
            if not _safe_formula(tree, set(env)):
                return None                        # anything beyond arithmetic on cells is refused
            return eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, env)  # noqa: S307
        except Exception:  # noqa: BLE001
            return None


# ----------------------------------------------------------------------------- model
@dataclass
class Cell:
    v: object = None
    indent: float = 0.0
    bold: bool = False
    pct: bool = False


@dataclass
class Table:
    sheet: str
    caption: list[str]
    label_cols: list[int]          # absolute 0-based column indices holding row labels
    value_cols: list[int]          # absolute 0-based column indices holding values
    col_paths: dict[int, str]      # absolute column index -> "A > B > C"
    rows: list[dict] = field(default_factory=list)  # {r, context, labels, values, level, total}
    pct_cols: set = field(default_factory=set)
    # parsing state (local, block-relative indices)
    _lab: list = field(default_factory=list, repr=False)
    _vals: list = field(default_factory=list, repr=False)
    _indent_col: int | None = field(default=None, repr=False)
    _indented: bool = field(default=False, repr=False)
    _sig: list = field(default_factory=list, repr=False)

    def to_frame(self) -> pd.DataFrame:
        recs = []
        for row in self.rows:
            rec = {"row": row["r"], "context": row["context"] or None, "is_total": row["total"],
                   "has_children": row.get("has_children", False)}
            for i, _ in enumerate(self.label_cols):
                rec[f"L{i}"] = row["labels"][i]
            for c in self.value_cols:
                rec[f"C{c}"] = row["values"].get(c)
            recs.append(rec)
        df = pd.DataFrame(recs)
        return df.set_index("row") if len(df) else df


# ----------------------------------------------------------------------------- reading
def _read_sheet(ws, ws_cached, ev: _FormulaEval) -> list[list[Cell]]:
    hidden_rows = {i for i, d in ws.row_dimensions.items() if d.hidden}
    hidden_cols = set()
    for k, d in ws.column_dimensions.items():
        if d.hidden:
            idx = column_index_from_string(k)
            hidden_cols |= set(range(d.min or idx, (d.max or idx) + 1))
    plus = sum(1 for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.strip() in ("+", "++", "+++"))
    symbol_sheet = plus >= 3
    grid = []
    for row in ws.iter_rows():
        out = []
        for c in row:
            v = c.value
            if isinstance(v, str) and v.startswith("="):
                cached = ws_cached[c.coordinate].value if ws_cached is not None else None
                v = cached if cached is not None else ev.cell(ws.title, c.coordinate)
            elif type(v).__name__ in ("ArrayFormula", "DataTableFormula"):     # array formula: cached result only
                v = ws_cached[c.coordinate].value if ws_cached is not None else None
            if v is not None and not isinstance(v, (str, int, float, bool, dt.datetime, dt.date, dt.time)):
                v = str(v)                         # rich text, array formulas, errors
            indent = 0.0
            if isinstance(v, str):
                indent = (len(v) - len(v.lstrip("  "))) / 2
            if c.alignment is not None and c.alignment.indent:
                indent += float(c.alignment.indent)
            fmt = c.number_format or ""
            v = to_number(v, keep_symbols=symbol_sheet)
            out.append(Cell(v, indent, bool(c.font and c.font.b), "%" in fmt and is_num(v)))
        if row and row[0].row in hidden_rows:
            out = [Cell() for _ in out]
        for ci in hidden_cols:
            if 0 < ci <= len(out):
                out[ci - 1] = Cell()
        grid.append(out)
    for mr in ws.merged_cells.ranges:
        if mr.min_row - 1 >= len(grid):
            continue
        tl = grid[mr.min_row - 1][mr.min_col - 1] if mr.min_col - 1 < len(grid[mr.min_row - 1]) else Cell()
        for r in range(mr.min_row - 1, min(mr.max_row, len(grid))):
            for c in range(mr.min_col - 1, min(mr.max_col, len(grid[r]))):
                if (r, c) != (mr.min_row - 1, mr.min_col - 1):
                    grid[r][c] = Cell(tl.v, tl.indent, tl.bold, tl.pct)
    width = max((len(r) for r in grid), default=0)
    return [r + [Cell() for _ in range(width - len(r))] for r in grid]


def _column_blocks(grid) -> list[tuple[int, int]]:
    """Split a sheet into side-by-side regions on fully empty columns."""
    if not grid:
        return []
    width = len(grid[0])
    used = [any(not _empty(row[c].v) for row in grid) for c in range(width)]
    blocks, start = [], None
    for c in range(width + 1):
        if c < width and used[c]:
            start = c if start is None else start
        elif start is not None:
            blocks.append((start, c))
            start = None
    merged = []
    for b in blocks:
        if merged:
            a0, a1 = merged[-1]
            label_only = a1 - a0 == 1 and not any(is_value(row[a0].v) for row in grid)
            aligned = sum(1 for row in grid if not _empty(row[a0].v) and any(not _empty(x.v) for x in row[b[0]:b[1]]))
            texts = sum(1 for row in grid if not _empty(row[a0].v))
            if label_only and b[0] - a1 == 1 and texts and aligned >= 0.6 * texts:
                merged[-1] = (a0, b[1])
                continue
        merged.append(b)
    return merged


# ----------------------------------------------------------------------------- classification
def _classify(cells: list[Cell]) -> str:
    vals = [c.v for c in cells]
    filled = [v for v in vals if not _empty(v)]
    if not filled:
        return "blank"
    first = next(v for v in vals if not _empty(v))
    codes = [v for v in filled if isinstance(v, str) and CODE_RE.match(v.strip())]
    small_ints = [v for v in filled if is_num(v) and float(v) == int(v) and 0 <= v < 100]
    if len(codes) >= 3 and len(codes) + len(small_ints) >= 0.6 * len(filled) \
            and not any(is_value(v) for v in filled if v not in small_ints) and len(small_ints) <= len(codes) // 2:
        return "codes"                                     # identifier row (series ids, code ranges)
    if len(filled) == 2 and isinstance(first, str) and clean_text(first).endswith(":") and len(clean_text(first)) <= 40:
        return "kv"                                        # form field "Key: value"
    if len(vals) > 1 and len(filled) == len(vals) and len({clean_text(v) for v in filled}) == 1 \
            and not is_value(filled[0]):
        return "caption"                                   # one merged text across the whole block
    nums = [v for v in filled if is_value(v) or isinstance(v, Sym)]
    if len(nums) == 1 and is_num(nums[0]) and float(nums[0]) == int(nums[0]) and 0 <= nums[0] < 100 \
            and len(filled) <= 3 and any(isinstance(v, str) and len(clean_text(v)) > 20 for v in filled):
        return "caption"                                   # "2 | Output per job: ..." table-number + title
    if len(filled) == 1 and is_year(filled[0]) and not _empty(vals[0]):
        return "label_only"                                # a lone year in the label column: a section label
    if nums and all(is_year(v) for v in nums) and (
            _empty(vals[0]) or len(set(nums)) == 1 or len(set(nums)) >= 2):
        return "header"                                    # year banner / year column headers (also repeating cycles)
    if nums:
        return "data"
    if len(filled) == 1 and not _empty(vals[0]):
        return "label_only"
    if len(filled) == 1:
        return "header" if len(clean_text(filled[0])) < 60 else "caption"
    if all(c.bold for c in cells if not _empty(c.v)):
        return "bold_text"                                 # bold text row: a header in most real sheets
    return "text"


def _resolve_kinds(grid) -> list[str]:
    kinds = [_classify(r) for r in grid]
    n = len(kinds)
    # Runs of text-only rows: header band if data follows soon; otherwise a text-only table
    # (first row = header, rest = data).
    i = 0
    while i < n:
        if kinds[i] != "text":
            i += 1
            continue
        j = i
        while j < n and kinds[j] == "text":
            j += 1
        k = j
        while k < n and kinds[k] in ("blank", "label_only", "header", "codes"):
            k += 1
        followed_by_data = k < n and kinds[k] == "data" and (k - j) <= 3
        p = i - 1
        while p >= 0 and kinds[p] == "blank":
            p -= 1
        under_bold_header = p >= 0 and kinds[p] == "bold_text"
        inside_records = i > 0 and kinds[i - 1] == "data"      # non-bold text row glued to data rows: a record
        if under_bold_header or inside_records:                # with missing numbers, not a new header
            kinds[i:j] = ["data"] * (j - i)
        elif followed_by_data and j - i <= 4:
            kinds[i:j] = ["header"] * (j - i)
        else:
            kinds[i] = "header"
            kinds[i + 1:j] = ["data"] * (j - i - 1)
        i = j

    kinds = ["header" if k == "bold_text" else k for k in kinds]
    # Short "data" rows above the first header band are title lines (e.g. "2 | United Kingdom").
    first_hdr = next((i for i, k in enumerate(kinds) if k in ("header", "codes")), None)
    if first_hdr is not None:
        for i in range(first_hdr):
            if kinds[i] == "data" and sum(not _empty(c.v) for c in grid[i]) <= 2:
                kinds[i] = "caption"

    first_data = next((i for i, k in enumerate(kinds) if k == "data"), n)

    def next_kind(i):
        for j in range(i + 1, n):
            if kinds[j] != "blank":
                if kinds[j] == "codes" and j > first_data and _empty(grid[j][0].v):
                    return "data"                          # series-id row inside a table, not a header
                return kinds[j]
        return "blank"

    # Label-only rows followed by a header band (through other titles) are titles/notes.
    for i in range(n - 1, -1, -1):
        if kinds[i] == "label_only" and next_kind(i) in ("header", "caption", "codes"):
            kinds[i] = "caption"
    # Long text notes after data ("NOTE: ...", footnotes) are captions, not sections.
    for i, row in enumerate(grid):
        if kinds[i] == "label_only":
            t = clean_text(row[0].v)
            if len(t) > 80 or re.match(r"^(note|source|footnote|\(\d+\)|\d+/)", t, re.I):
                kinds[i] = "caption"
    return kinds


def _repeat_heads(grid, kinds, k: int = 3) -> set[int]:
    """Rows that open a block whose child labels repeat an earlier block (e.g. Total / Men / Women each followed
    by the same age breakdown). Such rows are sibling group headers even without blank lines or indentation.
    Signatures are computed only inside runs of consecutive data rows (never across sections/blank rows)."""
    def lab(r):
        return clean_text(next((c.v for c in grid[r] if isinstance(c.v, str) and c.v.strip()), "")).lower()
    runs, cur = [], []
    for r in range(len(grid)):
        if kinds[r] == "data":
            cur.append(r)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    first: dict[tuple, int] = {}
    heads = set()
    for run in runs:
        labels = [lab(r) for r in run]
        for i, r in enumerate(run):
            sig = tuple(labels[i + 1:i + 1 + k])
            if len(sig) < k or not all(sig):
                continue
            if sig in first and lab(first[sig]) != labels[i]:
                heads.update((r, first[sig]))
            first.setdefault(sig, r)
    return heads


def _parse_block(sheet: str, grid: list[list[Cell]], c0: int) -> list[Table]:
    kinds = _resolve_kinds(grid)
    heads = _repeat_heads(grid, kinds)
    tables: list[Table] = []
    captions: list[str] = []
    pending: list[int] = []
    cur: Table | None = None
    sections: list[str] = []
    group = ""
    stack: list[tuple[float, str]] = []
    ancestors: dict[int, str] = {}
    prev = prev_nb = "blank"

    kv_table = None
    sec_ind: dict[str, float] = {}
    last_period = ""
    for r, (row, kind) in enumerate(zip(grid, kinds)):
        vals = [c.v for c in row]
        if kind == "kv":
            idx = [i for i, v in enumerate(vals) if not _empty(v)]
            if kv_table is None or prev != "kv":
                kv_table = Table(sheet, list(captions), [c0 + idx[0]], [c0 + idx[1]],
                                 {c0 + idx[0]: "Field", c0 + idx[1]: "Value"})
                tables.append(kv_table)
                captions = []
            kv_table.rows.append({"r": r + 1, "context": "", "labels": [clean_text(vals[idx[0]]).rstrip(":").strip()],
                                  "values": {c0 + idx[1]: vals[idx[1]] if not isinstance(vals[idx[1]], str)
                                             else clean_text(vals[idx[1]])}, "level": 0.0, "total": False})
            prev = prev_nb = kind
            continue
        if kind == "blank":
            group = ""                                      # a blank row closes the current group block
        if kind == "caption":
            captions.append(" ".join(dict.fromkeys(_fmt(v) if is_value(v) else clean_text(v) for v in vals if not _empty(v))))
        elif kind == "header":
            sig = tuple(clean_text(v) for v in vals)
            if cur is not None and not pending and sig in cur._sig:
                kind = prev                                 # repeated header band (page break): skip
            else:
                if not pending:
                    sections, group, stack = [], "", []
                pending.append(r)
        elif kind == "codes":
            if cur is None or pending or not _empty(vals[0]):
                if not pending:
                    sections, group, stack = [], "", []
                pending.append(r)                          # part of a header band
            # after data, a code row with an empty label cell (series ids) does not start a new table
        elif kind == "label_only":
            label = clean_text(vals[0])
            ind = row[0].indent
            sec_ind[label] = ind
            if cur is not None and not pending and cur._indented:
                # A value-less heading parents the following rows at its own indent; a later heading at the
                # same indent replaces it (sibling), hence the -0.5 key.
                stack = [s for s in stack if s[0] < ind - 0.5] + [(ind - 0.5, label)]
            elif label.isupper() and len(label) > 1:
                sections = [label]
            elif prev_nb == "data" and sections:
                sections = sections[:-1] + [label]       # sibling of the previous section
            else:
                sections.append(label)
            group = ""
        if kind == "data":
            if cur is None or pending:
                cur = _new_table(sheet, grid, pending, r, captions, c0)
                tables.append(cur)
                pending, captions, ancestors = [], [], {}
                if cur._indented:
                    stack = [(sec_ind.get(s, 0.0) - 0.5 - 0.01 * (len(sections) - 1 - i), s)
                             for i, s in enumerate(sections)]
            labels = [_fmt(vals[c]) if is_num(vals[c]) else clean_text(vals[c]) for c in cur._lab]
            if labels:
                m = re.match(r"^((?:19|20)\d{2})\b", labels[-1])
                if m:
                    last_period = m.group(1)
                elif last_period and re.fullmatch(r"(Q[1-4]|H[12]|[A-Z][a-z]{2,8}\.?)", labels[-1]):
                    labels[-1] = f"{last_period} {labels[-1]}"
            values = {c + c0: (clean_text(vals[c]) if isinstance(vals[c], str) else vals[c]) for c in cur._vals}
            main = labels[-1] if labels else ""
            is_group = r in heads or (bool(GROUP_RE.match(strip_footnote(main))) and prev != "data")
            level = row[cur._lab[-1]].indent if cur._lab else 0.0
            if cur._indent_col is not None and is_num(vals[cur._indent_col]):
                lvl = int(vals[cur._indent_col])
                name = next((clean_text(v) for v in vals if isinstance(v, str)), "")
                context = " > ".join(ancestors[k] for k in sorted(ancestors) if k < lvl)
                ancestors = {k: v for k, v in ancestors.items() if k < lvl}
                ancestors[lvl] = name
            elif cur._indented:
                stack = [s for s in stack if s[0] < level]
                context = " > ".join(s[1] for s in stack)
                stack.append((level, main))
            else:
                context = " > ".join(sections + ([group] if group and not is_group else []))
            cur.rows.append({"r": r + 1, "context": context, "labels": labels, "values": values,
                             "level": level, "total": bool(TOTAL_RE.search(main))})
            if not cur._indented and not PERIOD_RE.match(main) and (is_group or (prev == "blank" and len(cur.rows) > 1)):
                group = main
        prev = kind
        if kind != "blank":
            prev_nb = kind
    return tables


def _new_table(sheet, grid, header_rows, first_data, captions, c0) -> Table:
    width = len(grid[first_data])
    data_rows = []
    for r in range(first_data, len(grid)):
        if _classify(grid[r]) in ("data", "text"):
            data_rows.append(grid[r])
        if len(data_rows) >= 60:
            break
    # Label columns: leading columns that are mostly text in the data rows (a leading numeric
    # "Indent Level"-style column is skipped and stays a value column).
    lab = []
    for c in range(width):
        col = [row[c].v for row in data_rows if not _empty(row[c].v)]
        if not col and not lab:
            continue
        head = " ".join(clean_text(grid[r][c].v) for r in header_rows).lower()
        if not lab and re.search(r"\b(indent|level)\b", head) and all(is_num(v) for v in col):
            continue
        periodish = col and sum(is_year(v) or (isinstance(v, str) and bool(PERIOD_RE.match(clean_text(v))))
                                for v in col) >= 0.8 * len(col)
        texts = sum(isinstance(v, str) and not isinstance(v, Sym) for v in col)
        if col and (texts >= 0.6 * len(col) or (not lab and periodish)):
            lab.append(c)
        else:
            break
    def in_run(r, c):            # part of a horizontal run of identical text (merged / repeated group header)
        t = clean_text(grid[r][c].v)
        return bool(t) and any(0 <= k < width and clean_text(grid[r][k].v) == t for k in (c - 1, c + 1))

    def continues(prev: str, cur: str) -> bool:
        """Is `cur` the next line of the same wrapped header text?"""
        return bool(re.search(r"([,&/\-]|\b(and|or|of|for|the|in|by|per|to))$", prev)
                    or re.match(r"^([a-z&(]|-)", cur))
    col_paths = {}
    for c in range(width):
        parts, prev_r = [], None
        for r in header_rows:
            v = grid[r][c].v
            t = _fmt(v) if is_value(v) else clean_text(v)
            if not t:
                continue
            if parts and parts[-1] == t:
                prev_r = r
                continue
            if parts and prev_r is not None and r == prev_r + 1 and not in_run(r, c) and not in_run(prev_r, c) \
                    and continues(parts[-1].split(" > ")[-1], t):
                parts[-1] = clean_text(parts[-1] + "\n" + t)      # wrapped text of one header cell
            else:
                parts.append(t)
            prev_r = r
        col_paths[c + c0] = " > ".join(parts)
    used = [c for c in range(width)
            if col_paths[c + c0] or any(not _empty(grid[r][c].v) for r in range(first_data, len(grid)))]
    vals = [c for c in used if c not in lab]
    t = Table(sheet, list(captions), [c + c0 for c in lab], [c + c0 for c in vals], col_paths)
    t._lab, t._vals = lab, vals
    t._indent_col = next((c for c in vals if "indent" in col_paths[c + c0].lower()), None)
    t._indented = bool(lab) and len({row[lab[-1]].indent for row in data_rows if not _empty(row[lab[-1]].v)}) > 1
    own = data_rows[:max(3, min(len(data_rows), 30))]
    t.pct_cols = {c + c0 for c in vals
                  if sum(row[c].pct for row in own) >= 0.5 * max(1, sum(is_num(row[c].v) for row in own))
                  and any(row[c].pct for row in own)}
    t._sig = [tuple(clean_text(c.v) for c in grid[h]) for h in header_rows]
    return t


def _strip_glued_footnotes(t, ids: set):
    """'Frozen4' -> 'Frozen' when the sheet has a footnote line starting with '4'."""
    if not ids:
        return
    rx = re.compile(r"(?<=[a-z)\]])(" + "|".join(sorted(ids, key=len, reverse=True)) + r")(?:,(\d{1,2}))*$")

    def fix(x):
        return rx.sub("", x) if isinstance(x, str) else x
    for r in t.rows:
        r["labels"] = [fix(x) for x in r["labels"]]
        if r["context"]:
            r["context"] = " > ".join(fix(p) for p in r["context"].split(" > "))
    t.col_paths = {k: " > ".join(fix(p) for p in v.split(" > ")) for k, v in t.col_paths.items()}


def parse_workbook(path: str) -> list[Table]:
    wb = openpyxl.load_workbook(path)
    try:
        wbc = openpyxl.load_workbook(path, data_only=True)
    except Exception:  # noqa: BLE001
        wbc = None
    ev = _FormulaEval(wb)
    tables, notes = [], {}
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        grid = _read_sheet(ws, wbc[ws.title] if wbc else None, ev)
        notes[ws.title] = {m.group(1) for row in grid for c in row if isinstance(c.v, str)
                           for m in [re.match(r"^\s*(\d{1,2})\s*[A-Z][a-z]", c.v)] if m}
        for b0, b1 in _column_blocks(grid):
            tables += _parse_block(ws.title, [row[b0:b1] for row in grid], b0)
    for t in tables:
        _strip_glued_footnotes(t, notes.get(t.sheet, set()))
        _nest_intervals(t.rows)
        if t._indent_col is None and not t._indented:
            _nest_by_sums(t.rows, [c for c in t.value_cols if c not in t.pct_cols])
        _mark_parents(t.rows)
    return tables


# ----------------------------------------------------------------------------- rendering
def _fmt(v, pct=False) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    if isinstance(v, dt.date):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v * 100:.10g}%" if pct else f"{v:.10g}"
    if isinstance(v, int):
        return f"{v * 100}%" if pct else str(v)
    return clean_text(v)


def _profile(t: Table) -> list[str]:
    df = t.to_frame()
    out = []
    for col in [f"L{i}" for i in range(len(t.label_cols))] + [f"C{c}" for c in t.value_cols]:
        s = df[col].dropna()
        nums = s[s.map(is_num)]
        dates = s[s.map(lambda v: isinstance(v, (dt.datetime, dt.date)))]
        if len(s) and len(nums) >= 0.8 * len(s):
            out.append(f"  {col}: numeric, {len(s)} non-empty, min={_fmt(nums.min())}, max={_fmt(nums.max())}")
        elif len(s) and len(dates) >= 0.8 * len(s):
            out.append(f"  {col}: date, min={_fmt(dates.min())}, max={_fmt(dates.max())}")
        else:
            vc = s.astype(str).value_counts()
            vals = ", ".join(repr(k) for k in vc.index[:25])
            more = f" ... (+{len(vc) - 25} more)" if len(vc) > 25 else ""
            out.append(f"  {col}: text, {vc.size} distinct: {vals}{more}")
    return out


def render(tables: list[Table], max_rows: int = MAX_FULL_ROWS) -> str:
    """Compact text view for the LLM: column legend + pipe table keyed by Excel row."""
    out = []
    for ti, t in enumerate(tables):
        out.append(f"### Table T{ti} (sheet '{t.sheet}', {len(t.rows)} data rows)")
        if t.caption:
            out.append("Caption: " + " | ".join(t.caption))
        out.append("Columns:")
        for i, c in enumerate(t.label_cols):
            out.append(f"  L{i} (row label) = {t.col_paths.get(c) or '-'}")
        for c in t.value_cols:
            pct = " [displayed as %, stored as fraction]" if c in t.pct_cols else ""
            out.append(f"  C{c} = {t.col_paths.get(c) or '(no header)'}{pct}")
        has_ctx = any(r["context"] for r in t.rows)
        hdr = ["row"] + (["context"] if has_ctx else []) + [f"L{i}" for i in range(len(t.label_cols))] \
            + [f"C{c}" for c in t.value_cols]
        rows = t.rows
        if len(rows) > max_rows:
            out.append("Table too large to show fully. Column profile:")
            out += _profile(t)
            out.append(f"First 10 and last 3 of {len(rows)} rows (compute over tables[{ti}] in code):")
            rows = rows[:10] + rows[-3:]
        out.append("|".join(hdr))
        for row in rows:
            out.append("|".join([str(row["r"])] + ([row["context"]] if has_ctx else []) + row["labels"]
                                + [_fmt(row["values"][c], c in t.pct_cols) for c in t.value_cols]))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(f"======== {p}")
        print(render(parse_workbook(p)))
