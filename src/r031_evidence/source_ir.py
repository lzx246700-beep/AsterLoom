"""Typed, source-exact R15 IR for author-native JATS and SI XLSX/CSV.

PDF/OCR is never promoted without an independently tested page-coordinate adapter.
"""
from dataclasses import dataclass,asdict
from pathlib import Path
import hashlib
from .assay_graph import source_tables
from .source_io import local
from .contracts import SourceAsset

@dataclass(frozen=True)
class SourceAnchor:
    sha:str
    locator:str
    kind:str
    page:int|None=None
    bbox:tuple|None=None
@dataclass(frozen=True)
class CellIR:
    anchor:SourceAnchor
    author_literal:str
    row:int
    col:int
    header_literals:tuple[str,...]
    semantic_kind:str
@dataclass(frozen=True)
class TableIR:
    table_id:str
    caption:str
    footnotes:str
    cells:tuple[CellIR,...]
    format:str
@dataclass(frozen=True)
class MethodsIR:
    anchor:SourceAnchor
    original_text:str
    verification_state:str
@dataclass(frozen=True)
class PaperIR:
    source_sha:str
    source_kind:str
    tables:tuple[TableIR,...]
    methods:tuple[MethodsIR,...]
    extraction_state:str

def parse_author(asset:SourceAsset):
    p=Path(asset.source_path)
    if hashlib.sha256(p.read_bytes()).hexdigest()!=asset.sha256:
        raise ValueError("R15_AUTHOR_IR_ORIGINAL_SHA_MISMATCH")
    suffix=p.suffix.lower()
    if suffix==".pdf":
        return PaperIR(asset.sha256,"PDF",(),(),"PDF_PAGE_COORDINATE_ADAPTER_NOT_VALIDATED_HOLD")
    if suffix not in (".xml",".nxml",".xlsx",".csv"):
        return PaperIR(asset.sha256,suffix,(),(),"UNSUPPORTED_AUTHOR_NATIVE_FORMAT_HOLD")
    tables=[]
    for t in source_tables(p,asset.sha256):
        cells=tuple(CellIR(SourceAnchor(asset.sha256,c["source_anchor"],t["source_format"]),
          c["raw"],c["row"],c["col"],tuple(c["header_path"]),
          "AUTHOR_FORMULA_HOLD" if c["raw"].startswith("FORMULA_HOLD:") else
          "ORIGINAL_AUTHOR_CELL") for c in t["cells"])
        tables.append(TableIR(t["table_id"],t.get("caption",""),t.get("footnotes",""),cells,
                             t["source_format"]))
    methods=[]
    if suffix in (".xml",".nxml"):
        from xml.etree import ElementTree as ET
        root=ET.parse(p).getroot()
        for i,node in enumerate(x for x in root.iter() if local(x.tag)=="p"):
            raw=" ".join("".join(node.itertext()).split())
            if raw and i<250:
                methods.append(MethodsIR(SourceAnchor(asset.sha256,f"author_jats/p[{i}]","JATS_PARAGRAPH"),
                  raw,"SOURCE_PARAGRAPH_NOT_AUTO_VERIFIED_AS_METHODS"))
    return PaperIR(asset.sha256,"JATS" if suffix in (".xml",".nxml") else suffix,
                   tuple(tables),tuple(methods),"AUTHOR_NATIVE_SOURCE_ONLY")
