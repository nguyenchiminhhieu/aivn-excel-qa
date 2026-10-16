"""Block 1 output: a navigable map of a workbook (TableIR), built on top of the structure parser.

Every row has a path (section/parent segments + its own label), every column a header path, and every
value an A1 cell reference so answers can be traced back to the sheet.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl.utils import get_column_letter

from .excel_parser import parse_workbook

SUSPICIOUS = re.compile(r"\b(ignore|disregard|forget|override)\b|\b(assistant|system|user)\s*:|\byou are (now|an?)\b|"
                        r"system prompt|\banswer (is|with)\b|\b(respond|reply) with\b|return the (value|answer)|"
                        r"debug mode|jailbreak", re.I)


@dataclass
class Col:
    id: str            # "C5"
    idx: int           # 0-based sheet column
    path: list[str]
    pct: bool = False  # Excel percent format: stored as a fraction, displayed as %

    @property
    def letter(self) -> str:
        return get_column_letter(self.idx + 1)

    @property
    def label(self) -> str:
        return self.path[-1] if self.path else self.id


@dataclass
class Row:
    id: int            # Excel row number
    path: list[str]    # parents ... + own label
    labels: list[str]  # all label cells (record tables can have several)
    values: dict       # col id -> value
    is_summary: bool
    has_children: bool

    @property
    def label(self) -> str:
        return self.path[-1] if self.path else ""


@dataclass
class TableIR:
    id: str
    sheet: str
    caption: list[str]
    file: str = ""
    cols: list[Col] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    label_headers: list[str] = field(default_factory=list)

    def ref(self, row: Row, col: Col) -> str:
        return f"{self.sheet}!{col.letter}{row.id}"


def build_many(paths: list[str]) -> list[TableIR]:
    """One map over several workbooks (multi-file questions); table ids stay unique across files."""
    out = []
    for p in paths:
        for t in build(p, start=len(out)):
            out.append(t)
    return out


def build(path: str, start: int = 0) -> list[TableIR]:
    out = []
    for i, t in enumerate(parse_workbook(path), start=start):
        cols = [Col(f"C{c}", c, [p for p in t.col_paths.get(c, "").split(" > ") if p] or [f"C{c}"],
                    c in t.pct_cols) for c in t.value_cols]
        rows = []
        for r in t.rows:
            ctx = [p for p in (r["context"] or "").split(" > ") if p]
            labels = [x for x in r["labels"] if x]
            own = " · ".join(labels) if len(labels) > 1 else (labels[0] if labels else "")
            rows.append(Row(r["r"], ctx + ([own] if own else []), labels,
                            {f"C{c}": r["values"].get(c) for c in t.value_cols},
                            bool(r.get("total")), bool(r.get("has_children"))))
        out.append(TableIR(f"T{i}", t.sheet, t.caption, Path(path).name, cols, rows,
                           [t.col_paths.get(c, "") for c in t.label_cols]))
    return out
