"""R17 minimal original-byte CSV codec bridge for Worker inputs unavailable in UTF-8.

Does not replace Worker spreadsheet/PDF extraction: only preserves official
non-UTF8 author CSV source cells in exactly the existing PaperIR structure.
Original author byte SHA is used directly, with row/column CSV locators.
"""
from __future__ import annotations
import csv,io,hashlib
from pathlib import Path

def original_csv_ir(source,paper,original_sha):
 p=Path(source)
 raw=p.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=original_sha:
  raise ValueError("R17_CSV_ORIGINAL_BYTES_SHA_CHANGED")
 if len(raw)>1_500_000:raise ValueError("R17_ORIGINAL_CSV_SIZE_HOLD")
 encoding=None
 for codec in ("utf-8-sig","cp1252"):
  try:
   content=raw.decode(codec,errors="strict")
   encoding=codec
   break
  except UnicodeDecodeError:pass
 if encoding is None:raise ValueError("R17_CSV_ENCODING_UNRESOLVED")
 lines=content.splitlines()
 if not lines:raise ValueError("R17_CSV_EMPTY")
 sample="\n".join(lines[:12])
 dialect=csv.Sniffer().sniff(sample,delimiters=",;\t")
 rows=list(csv.reader(io.StringIO(content),dialect))
 if len(rows)>12000 or max(map(len,rows))>250:raise ValueError("R17_CSV_DIMENSIONS_HOLD")
 cells=[]
 for ri,line in enumerate(rows):
  for ci,txt in enumerate(line):
   if not txt:continue
   cells.append({"row":ri,"column":ci,"raw_text":txt,
      "anchor":{"asset_sha256":original_sha,
        "locator":f"csv/source-row-{ri+1}/column-{ci+1}",
        "page":None,"bbox":None,"coordinate_system":None},
      "rowspan":1,"colspan":1,"role":"header" if ri==0 else "data",
      "metadata":{"source_csv_encoding":encoding,"source_csv_delimiter":dialect.delimiter,
         "original_bytes_sha":original_sha}})
 if not cells:raise ValueError("R17_CSV_NO_AUTHOR_CELLS")
 table={"table_id":"Official original SI CSV / "+p.name,"cells":cells,
   "headers":[],"footnotes":[],
   "anchor":{"asset_sha256":original_sha,"locator":"csv/original",
     "page":None,"bbox":None,"coordinate_system":None},
   "caption":"","state":"SCI_REVIEW_HOLD"}
 return {"asset":{"sha256":original_sha,"path":str(p),"bytes":len(raw)},
   "paper_id":paper,"backend":"stdlib-csv","tables":[table],
   "methods":[],"supplements":[],"state":"SCI_REVIEW_HOLD"}
