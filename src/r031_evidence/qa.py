"""Read-only source/science QA, operations, review exports."""
from __future__ import annotations
import csv,io,json,html,sqlite3
from pathlib import Path
from . import repository as repo
from .repository import connect,summary,content_fingerprint,ops_fingerprint,backup_verify,restore_verify
from .contracts import EvidenceError
def health(db):
 c=connect(db)
 try:
  s=summary(c);f=content_fingerprint(c)
  s["science_content_sha256"]=f["science_SHA256"]
  s["operations"]=ops_fingerprint(c)
  s["batches"]=[dict(batch_id=r[0],phase=r[1],success_assets=r[2],quarantine_assets=r[3])
   for r in c.execute("SELECT batch_id,phase,success_asset_count,quarantined_asset_count FROM r08_batch ORDER BY batch_id")]
  return s
 finally:c.close()
def inspect(db,paper):
 c=connect(db)
 try:
  rows=c.execute("""SELECT p.paper_key,p.doi,a.asset_id,a.original_sha,a.original_path,
      a.license_literal,a.role FROM paper_ref p JOIN asset_ref a ON a.paper_id=p.paper_id
      WHERE p.paper_key=? OR p.doi=? ORDER BY a.asset_id""",(paper,paper)).fetchall()
  result=[]
  for pm,doi,asset,h,path,license,role in rows:
   records=c.execute("""SELECT sp.locator,sp.source_literal,e.scientific_reason,
      e.endpoint,e.author_control_literal,e.author_experiment_literal
      FROM r08_science_evidence e JOIN source_span sp ON e.span_id=sp.span_id
      WHERE e.asset_id=? ORDER BY sp.locator""",(asset,)).fetchall()
   holds=c.execute("""SELECT scientific_reason,status FROM scientific_hold
     WHERE asset_id=?""",(asset,)).fetchall()
   result.append({"paper":pm,"doi":doi,"source_id":asset,"source_sha256":h,
    "original_author_path":path,"license":license,"source_role":role,
    "source_metric_records":records,"science_HOLDS":holds,
    "author_SCI_review_approved":False,"training_approved":False})
  return result
 finally:c.close()
def review_rows(db):
 c=connect(db)
 try:
  return c.execute("""SELECT p.paper_key,p.doi,a.role,sp.locator,
    e.source_metric_literal,e.author_control_literal,e.author_experiment_literal,
    e.scientific_reason,e.endpoint,a.original_sha
    FROM r08_science_evidence e JOIN source_span sp ON sp.span_id=e.span_id
    JOIN asset_ref a ON a.asset_id=e.asset_id JOIN paper_ref p ON p.paper_id=a.paper_id
    ORDER BY p.paper_key,e.native_row""").fetchall()
 finally:c.close()
def export(db, dest,fmt="csv"):
 dest=Path(dest)
 if repo.OUT is None or not dest.resolve().parent.is_relative_to(repo.OUT.resolve()):
  raise EvidenceError("REVIEW_OUTPUT_OUTSIDE_R09_LAB")
 if dest.exists():raise EvidenceError("REVIEW_DESTINATION_ALREADY_EXISTS")
 rows=review_rows(db)
 if fmt=="csv":
  stream=io.StringIO();w=csv.writer(stream)
  w.writerow(("paper","doi","source_role","author_cell_locator","original_metric",
    "original_control","original_treatment_or_equation","science_reason","endpoint","original_sha"))
  w.writerows(rows)
  dest.write_text(stream.getvalue())
 elif fmt=="html":
  table="".join("<tr>"+"".join("<td>"+html.escape(str(x or ""))+"</td>" for x in row)+"</tr>" for row in rows)
  dest.write_text("<!doctype html><html lang='zh'><meta charset='utf-8'><title>R031 source QA</title>"
   "<h2>来源证据审阅（未获SCI审核与模型训练许可）</h2>"
   "<p>仅已正式公开DEV；Gold0；原作者OCR或相对值不得推算绝对ATP主Y。</p>"
   "<table border='1'><tr>"+"".join("<th>"+x+"</th>" for x in
    ("论文","DOI","role","物理原格","metric","control","treatment","HOLD","endpoint","SHA"))+"</tr>"+table+"</table></html>")
 else:raise EvidenceError("UNKNOWN_REVIEW_FORMAT")
 return {"path":str(dest),"rows":len(rows),"science_approved":0,"Gold":0}
