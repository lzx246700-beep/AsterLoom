"""R25 author-sourced assay/reaction near-miss gates for gap-driven mining.

Read-only. Evidence of an assay METHOD is not a measured numeric Y; metadata,
unverified license and physical loading do not imply covalent bioactive polymer.
"""
from __future__ import annotations
from pathlib import Path
import sqlite3,hashlib,re
def _sha(path):
 p=Path(path)
 return p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()
def triage(home,pmcid):
 root=Path(home).expanduser().resolve()
 if root.name=="state":root=root.parent
 db=root/"state/science.sqlite3"
 c=sqlite3.connect("file:"+str(db.resolve())+"?mode=ro",uri=True)
 c.row_factory=sqlite3.Row
 try:
  meta=c.execute("SELECT paper_key,doi FROM paper_ref WHERE paper_key=?",(pmcid,)).fetchone()
  raw=c.execute("SELECT COUNT(*) FROM r17_measurement WHERE paper_key=?",(pmcid,)).fetchone()[0]
  rates=c.execute("SELECT COUNT(*) FROM r17_measurement WHERE paper_key=? AND endpoint='ATP_GENERATION_RATE'",(pmcid,)).fetchone()[0]
  mods=list(c.execute("""SELECT k.heading_original,k.text_original,k.original_locator,a.original_sha,a.original_path
   FROM r16_source_method k JOIN r16_parsed_asset a ON a.parsed_asset_id=k.parsed_asset_id
   WHERE k.paper_key=? AND a.source_format IN ('.xml','.nxml')
   ORDER BY k.original_locator""",(pmcid,)))
  anchored=[]
  for h,t,loc,sha,path in mods:
   if _sha(path)!=sha:continue
   anchored.append({"heading":h or "","text":t or "","locator":loc,
    "source_sha256":sha})
  q=lambda pat:next((m for m in anchored if re.search(pat,m["heading"]+" "+m["text"],re.I)),None)
  atp=q(r"\bATP\s+(?:production|rate)\s+(?:rate\s+)?assay\b")
  if not atp:atp=q(r"seahorse\s+XF.{0,250}ATP\s+Production\s+Rate")
  ca_plasma=q(r"\bCAP\s+treatment\b|cold\s+atmospheric\s+plasma")
  phys=q(r"\bencapsulation\b|pomace\s+extract|extract.containing hydrogels")
  material=q(r"alginate.{0,35}inulin.{0,100}hydrogel")
  selected=[a for a in (atp,ca_plasma,phys,material) if a]
  items=[];seen=set()
  for a in selected:
   k=(a["locator"],a["source_sha256"])
   if k in seen:continue
   seen.add(k)
   items.append({"heading":a["heading"],"original_locator":a["locator"],
    "original_sha256":a["source_sha256"],
    "excerpt_proof_of_scope_only":a["text"][:360]})
  if not anchored:
   state="AUTHOR_NATIVE_METHODS_UNAVAILABLE_OR_NOT_YET_PARSED"
  elif atp and rates==0:
   state="AUTHOR_ATP_RATE_METHOD_PRESENT_BUT_NO_NATIVE_NUMERIC_Y"
  elif material and phys:
   state="EXTRACT_LOADED_ALGINATE_HYDROGEL_NOT_COVALENT_ACTIVE_MONOMER_PROVEN"
  elif rates:
   state="AUTHOR_RATE_NUMERIC_EXISTS_SEPARATE_X_QUALIFICATION"
  else:
   state="PRIMARY_AUTHOR_METHOD_INDEXED_NO_PROFILE_Y_YET"
  review={
   "paper_key":pmcid,"doi":meta["doi"] if meta else None,
   "original_author_method_paragraphs_SHA_verified":len(anchored),
   "original_author_measurement_count":raw,
   "actual_native_ATP_GENERATION_RATE_observations":rates,
   "author_seahorse_ATP_rate_method_present":bool(atp),
   "source_treatment_CAP_plasma":bool(ca_plasma),
   "source_encapsulation_or_extract_loading":bool(phys),
   "source_alginate_inulin_hydrogel":bool(material),
   "method_original_anchors":items,
   "profile_Y_qualification_state":state,
   "verifiable_covalent_active_incorporation":None,
   "independent_material_batch":None,
   "approved_training_y":None,
   "not_assay_method_as_numeric_Y":True,"Gold":0}
  return review
 finally:c.close()
