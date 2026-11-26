"""R09 packaged native JATS/XLSX/CSV table reader (verified R02 scientific semantics).

Independent of benchmark PMCID and manual SCI answers. Never supplies
missing cells, estimates figures, or infers physical units.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib, json, re, csv
import xml.etree.ElementTree as ET
from .contracts import ExcelDependencyHold

VERSION = "p1b-source-tables-0.1"
def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def local(tag: str) -> str: return tag.rsplit("}", 1)[-1]
def plain(e) -> str:
    return " ".join("".join(e.itertext()).split()) if e is not None else ""
def kids(e, name):
    return [x for x in e if local(x.tag) == name]
def first(e, name):
    return next((x for x in e if local(x.tag) == name), None)

@dataclass
class Cell:
    row: int
    col: int
    raw: str
    region: str
    is_header: bool
    rowspan: int
    colspan: int
    source_anchor: str
    header_path: list[str]
    row_label: str | None = None

def _header_paths(cells: list[dict], region_by_row: list[str]) -> None:
    headers = [x for x in cells if x["is_header"]]
    for c in cells:
        if c["is_header"]:
            c["header_path"] = []
            continue
        cover = []
        for h in headers:
            if h["row"] >= c["row"]: continue
            if h["col"] <= c["col"] < h["col"] + h["colspan"]:
                cover.append((h["row"], h["col"], h["raw"]))
        c["header_path"] = list(dict.fromkeys(t[2] for t in sorted(cover) if t[2]))
        # Prefer the first source-row cell when it is textual; never infer a new label.
        left = [x for x in cells if x["row"] == c["row"] and x["col"] < c["col"] and x["raw"] and not x["is_header"]]
        c["row_label"] = left[0]["raw"] if left else None

def jats_tables(path: str | Path, expected_sha256: str | None = None) -> list[dict]:
    p = Path(path)
    raw = p.read_bytes()
    digest = sha(raw)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("SOURCE_SHA_MISMATCH")
    root = ET.fromstring(raw)
    result = []
    for n, wrap in enumerate((e for e in root.iter() if local(e.tag) == "table-wrap"), 1):
        table = first(wrap, "table")
        if table is None:
            continue
        tbid = wrap.attrib.get("id") or f"table-wrap-index-{n}"
        label, caption = plain(first(wrap, "label")), plain(first(wrap, "caption"))
        foot = plain(first(wrap, "table-wrap-foot"))
        rows: list[tuple[str, ET.Element]] = []
        for section in table:
            kind = local(section.tag)
            if kind in ("thead", "tbody", "tfoot"):
                rows += [(kind, tr) for tr in section if local(tr.tag) == "tr"]
            elif kind == "tr":
                rows.append(("body", section))
        cells = []
        occupied: dict[tuple[int, int], bool] = {}
        region_by_row = []
        for ridx, (region, tr) in enumerate(rows):
            region_by_row.append(region)
            cursor = 0
            for cidx, el in enumerate(tr):
                if local(el.tag) not in ("td", "th"): continue
                while (ridx, cursor) in occupied: cursor += 1
                rs = int(el.attrib.get("rowspan", "1"))
                cs = int(el.attrib.get("colspan", "1"))
                if not (1 <= rs <= 200 and 1 <= cs <= 200):
                    raise ValueError("INVALID_SPAN")
                for y in range(ridx, ridx + rs):
                    for x in range(cursor, cursor + cs):
                        if (y, x) in occupied: raise ValueError("OVERLAPPING_TABLE_CELLS")
                        occupied[(y, x)] = True
                cells.append(asdict(Cell(row=ridx, col=cursor, raw=plain(el),
                    region=region, is_header=(region == "thead" or local(el.tag) == "th"),
                    rowspan=rs, colspan=cs,
                    source_anchor=f"{tbid}/row-{ridx+1}/cell-{cidx+1}/grid-R{ridx+1}C{cursor+1}",
                    header_path=[])))
                cursor += cs
        _header_paths(cells, region_by_row)
        result.append({"source_path":str(p), "source_sha256":digest, "source_format":"JATS_XML",
                       "table_id":tbid, "table_label":label, "caption":caption,
                       "footnotes":foot, "table_index":n, "n_source_rows":len(rows),
                       "cells":cells, "parser_version":VERSION})
    return result

def excel_tables(path: str | Path, expected_sha256: str | None = None,
                 max_rows: int = 10000) -> list[dict]:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name=="openpyxl" or exc.name.startswith("openpyxl.")):
            raise ExcelDependencyHold("NEEDS_EXCEL_DEPENDENCY_SOURCE_HOLD: install r031-evidence[excel]") from None
        raise
    p = Path(path)
    raw = p.read_bytes()
    digest = sha(raw)
    if expected_sha256 and expected_sha256 != digest: raise ValueError("SOURCE_SHA_MISMATCH")
    wb = load_workbook(p, read_only=True, data_only=False)
    out=[]
    try:
        for ws in wb.worksheets:
            if ws.max_row is None or ws.max_row > max_rows:
                out.append({"source_path":str(p), "source_sha256":digest,
                            "source_format":"XLSX","table_id":ws.title,"cells":[],
                            "status":"SOURCE_HOLD_TOO_MANY_ROWS","row_limit":max_rows})
                continue
            cells=[]
            header = []
            for row in ws.iter_rows():
                for c in row:
                    if c.value is None: continue
                    literal = str(c.value).strip()
                    is_header = c.row==1
                    if c.data_type=="f": # Never pretend formulas have evaluated author values.
                        literal = "FORMULA_HOLD:"+literal
                    if is_header: header.append((c.column,literal))
                    cells.append(asdict(Cell(row=c.row-1,col=c.column-1,raw=literal,region="thead" if is_header else "tbody",
                        is_header=is_header,rowspan=1,colspan=1,source_anchor=f"{ws.title}!{c.coordinate}",
                        header_path=[] if is_header else [v for n,v in header if n==c.column],
                        row_label=None)))
            for c in cells:
                if not c["is_header"]:
                    left=next((x for x in cells if x["row"]==c["row"] and x["col"]==0),None)
                    c["row_label"]=left["raw"] if left and left["col"]!=c["col"] else None
            out.append({"source_path":str(p),"source_sha256":digest,
                "source_format":"XLSX","table_id":ws.title,"table_label":ws.title,
                "caption":"","footnotes":"","n_source_rows":ws.max_row,"cells":cells,
                "parser_version":VERSION})
    finally: wb.close()
    return out

def csv_tables(path: str | Path, expected_sha256: str | None = None, max_rows: int=10000) -> list[dict]:
    p=Path(path); raw=p.read_bytes();digest=sha(raw)
    if expected_sha256 and expected_sha256!=digest:raise ValueError("SOURCE_SHA_MISMATCH")
    data=list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    if len(data)>max_rows:raise ValueError("SOURCE_HOLD_TOO_MANY_ROWS")
    headers=data[0] if data else []
    cells=[]
    for ridx,row in enumerate(data):
        for cidx,val in enumerate(row):
            cells.append(asdict(Cell(ridx,cidx,val,"thead" if ridx==0 else "tbody",ridx==0,1,1,
                f"{p.name}!R{ridx+1}C{cidx+1}",[] if ridx==0 else [headers[cidx]] if cidx<len(headers) else [],
                row[0] if ridx>0 and cidx>0 and row else None)))
    return [{"source_path":str(p),"source_sha256":digest,"source_format":"CSV","table_id":p.name,
             "table_label":p.name,"caption":"","footnotes":"","n_source_rows":len(data),
             "cells":cells,"parser_version":VERSION}]
