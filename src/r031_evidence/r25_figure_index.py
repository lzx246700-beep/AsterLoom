"""R25.1 source-only author JATS Figure and subpanel evidence index.

Indexes original <fig> captions and native graphic hrefs, preserving original
source SHA and stable author id. ZERO plot digitization / synthetic numeric Y.
Read-only on one cumulative science SQLite, JSONL projection is not a new DB.
"""
from __future__ import annotations
from pathlib import Path
import hashlib,json,re,sqlite3,xml.etree.ElementTree as ET
from collections import Counter,defaultdict

SCHEMA="r031.author-figure-index/1"
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def basename(t):return t.rsplit("}",1)[-1].lower() if "}" in t else t.lower()
def labels(caption):
 t=" ".join(caption.split()).lower()
 result=[]
 def hit(pattern):return bool(re.search(pattern,t,re.I))
 if hit(r"\batp\s*(?:production|rate)\s*(?:rate|assay|test|measurement|quantification)?\b|mitoatp|glycoatp"):
  result.append("ATP_RATE_FIGURE_CANDIDATE_ONLY")
 elif hit(r"\b(atp\s+(?:content|level|concentration)|cellular\s+atp)\b"):
  result.append("ATP_POOL_FIGURE_CANDIDATE_ONLY")
 if hit(r"(hydrogel|gelma|polysaccharide|biomaterial|alginate|photocrosslink)") and hit(
    r"(viabilit|macrophag|cytokine|immunomodulat|osteogen|fibroblast|thp-1|mesenchymal|differentiation|cell)"):
  result.append("POLYMER_CELL_OR_IMMUNE_FIGURE_CANDIDATE_ONLY")
 if hit(r"(hydrogel|gelma|polysaccharide|biomaterial|alginate|photocrosslink)") and hit(
    r"(swelling|modulus|stiffness|rheolog|mechanic|crosslink|release|polymerization|structure.property)"):
  result.append("POLYMER_PROPERTY_FIGURE_CANDIDATE_ONLY")
 return result or ["AUTHOR_FIGURE_GENERAL_NOT_TARGETED"]
def figures_from_asset(paper,source,sha):
 if digest(source)!=sha:raise ValueError("R25_AUTHOR_JATS_BYTE_SHA_MISMATCH:"+paper)
 raw=Path(source).read_bytes()
 if len(raw)>13000000:raise ValueError("R25_JATS_MAX_13MB")
 root=ET.fromstring(raw)
 rows=[];used=set()
 for pos,node in enumerate((n for n in root.iter() if basename(n.tag)=="fig"),1):
  fid=node.get("id")
  if fid and fid in used:raise ValueError("R25_DUPLICATE_FIGURE_ID:"+paper+":"+fid)
  if fid:used.add(fid)
  capt=next((x for x in node if basename(x.tag)=="caption"),None)
  title=next((x for x in node if basename(x.tag)=="label"),None)
  literal=" ".join("".join(capt.itertext()).split()) if capt is not None else ""
  figurelabel=" ".join("".join(title.itertext()).split()) if title is not None else fid
  refs=[]
  for x in node.iter():
   if basename(x.tag) not in ("graphic","inline-graphic"):continue
   href=next((v for k,v in x.attrib.items() if basename(k)=="href"),None)
   if href and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+%-]{0,210}",href):
    refs.append(href)
  refs=list(dict.fromkeys(refs))
  anchor=("/article//@id="+fid) if fid else "/article/fig-ordinal["+str(pos)+"]"
  stable_id="r25:fig:"+hashlib.sha256((paper+"\x00"+sha+"\x00"+anchor).encode()).hexdigest()[:32]
  rows.append({"schema":SCHEMA,"figure_key":stable_id,"paper_key":paper,
   "source_original_sha256":sha,"source_original_path":str(source),
   "source_format":"JATS_AUTHOR_XML","author_figure_id":fid,
   "author_figure_label":figurelabel,"author_caption_original":literal,
   "author_locator":anchor,"author_graphic_href_literals":refs,
   "category_candidates_only":labels(literal),
   "source_verification":"LOCAL_ORIGINAL_JATS_BYTE_SHA_AND_EXACT_AUTHOR_FIG_ID",
   "numeric_Y_not_extracted":True,
   "plot_axes_not_validated":True,
   "independent_biological_n_verified":False,
   "training_decision":"SOURCE_ONLY_FIGURE_QUANTIFICATION_HOLD",
   "SCI_review_approved":False,"Gold":0})
 return rows
def run(home,output):
 root=Path(home).expanduser().resolve()
 if root.name=="state":root=root.parent
 db=root/"state/science.sqlite3"
 c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True)
 try:
  rows=c.execute("""SELECT paper_key,original_sha,original_path FROM r16_parsed_asset
   WHERE source_format IN ('.xml','.nxml') ORDER BY paper_key,original_sha""").fetchall()
 finally:c.close()
 output=Path(output).expanduser().absolute()
 if output.exists() or output.with_suffix(output.suffix+".manifest.json").exists():
  raise ValueError("R25_FIGURE_INDEX_OUTPUT_EXISTS_REFUSE_OVERWRITE")
 output.parent.mkdir(parents=True,exist_ok=True)
 pieces=[];missing=[];paper_count=0;dupes=set()
 for pm,sha,path in rows:
  if not Path(path).is_file() or digest(path)!=sha:
   missing.append({"paper":pm,"source_SHA":sha,"state":"ORIGINAL_JATS_SHA_MISSING_OR_CHANGED_HOLD"})
   continue
  paper_count+=1
  for v in figures_from_asset(pm,path,sha):
   if v["figure_key"] in dupes:raise ValueError("DUPLICATE_AUTHOR_FIGURE_ANCHOR")
   dupes.add(v["figure_key"])
   pieces.append(v)
 output.write_text("".join(json.dumps(v,ensure_ascii=False,sort_keys=True)+"\n" for v in pieces))
 category=Counter(t for row in pieces for t in row["category_candidates_only"])
 selected=defaultdict(list)
 for item in pieces:
  if any("ATP_RATE" in t for t in item["category_candidates_only"]) or any(
      "POLYMER_CELL" in t for t in item["category_candidates_only"]):
   selected[item["paper_key"]].append({"figure_key":item["figure_key"],
    "figure_id":item["author_figure_id"],"anchor":item["author_locator"],
    "source_sha":item["source_original_sha256"],
    "labels":item["category_candidates_only"]})
 manifest={"schema":SCHEMA,"status":"SOURCE_ONLY_UNREVIEWED_JATS_AUTHOR_FIGURES",
  "source_home":str(root),"author_original_JATS_assets_seen":len(rows),
  "author_original_JATS_assets_SHA_verified":paper_count,
  "missing_source_assets":missing,
  "figures_total":len(pieces),"category_candidate_totals":dict(category),
  "source_only_figures_by_paper":dict(selected),
  "jsonl":str(output),"jsonl_sha256":digest(output),
  "numeric_Y_extracted":0,"training_approved":0,"Gold":0}
 dest=output.with_suffix(output.suffix+".manifest.json")
 dest.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
 return {"status":manifest["status"],"figures_total":len(pieces),
  "original_JATS_SHA_verified":paper_count,
  "profile_candidate_figures":dict(category),
  "missing_source_assets":len(missing),
  "manifest":str(dest),"source_jsonl":str(output),"Gold":0}
