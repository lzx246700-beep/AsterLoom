"""R17 original research triage on existing real author JATS + Worker source tables.

Ranks rather than deletes; article-type, captions, real numeric cells, and
domain title/abstract are grounded in original author content.
"""
from pathlib import Path
import re,hashlib,sqlite3,xml.etree.ElementTree as ET,json
from . import repository as repo
from .r17_experiment_mining import author_article_type,numeric,endpoint,hid,norm
SMALL=re.compile(r"small.?molecule|natural product|flavonoid|morin|quercetin|luteolin|curcumin|phenol|metaboli|compound|medicinal plant|plant extract|phytochemical|drug|antioxidant|ginsenoside",re.I)
POLYMER=re.compile(r"polymer|hydrogel|biomaterial|scaffold|nanopartic|polysaccharide|chitosan|macromolec|drug delivery|microgel|nanogel|electrospin|nanofiber|polyethylene glycol|PEG|gelation|bioactive material",re.I)
def source_title_abstract(path):
 try:
  root=ET.parse(path).getroot()
  def get(tag):
   return next((norm("".join(x.itertext())) for x in root.iter() if x.tag.rsplit("}",1)[-1]==tag),"")
  return get("article-title"),get("abstract")
 except (ET.ParseError,OSError):return "",""
def rank(workspace,domain="any",limit=30,persist=True):
 if domain not in ("small_molecule","polymer_biomaterial","any"):raise ValueError("R17_DOMAIN_INVALID")
 if not 1<=limit<=250:raise ValueError("R17_RANK_LIMIT")
 home=Path(workspace).resolve();db=home/"science.sqlite3"
 repo.configure(home);c=repo.connect(db)
 try:
  repo.migrate(c)
  assets=c.execute("""SELECT parsed_asset_id,paper_key,original_sha,original_path
    FROM r16_parsed_asset WHERE source_format IN ('.xml','.nxml')
    ORDER BY paper_key""").fetchall()
  all_results=[]
  for aid,pm,sha,path in assets:
   p=Path(path)
   if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=sha:continue
   art=author_article_type(p)
   title,abstract=source_title_abstract(p)
   text=title+" "+abstract[:2500]
   regex=SMALL if domain=="small_molecule" else POLYMER if domain=="polymer_biomaterial" else None
   match=bool(regex.search(text)) if regex else True
   total_tables=0;numeric_tables=0;potential=0;primers=0
   for key,caption in c.execute("SELECT table_key,caption_original FROM r16_source_table WHERE parsed_asset_id=?",(aid,)):
    total_tables+=1
    if re.search(r"(?:primer sequences|PCR primer|key resources table)",caption or "",re.I):primers+=1;continue
    cells=c.execute("SELECT original_literal FROM r16_source_cell WHERE table_key=?",(key,)).fetchall()
    hits=sum(numeric(raw) is not None for (raw,) in cells)
    if hits>=2:numeric_tables+=1;potential+=hits
   research=art in ("research-article","brief-report","rapid-communication")
   state=("RESEARCH_PRIORITY" if research and match and numeric_tables else
     "RESEARCH_NO_NATIVE_NUMERIC_TABLE" if research and match else
     "RESEARCH_OUTSIDE_DOMAIN" if research else "REVIEW_OR_ARTICLE_TYPE_HOLD")
   score=(100 if research else 0)+(25 if match else 0)+min(30,numeric_tables*6)+min(20,potential//3)-primers*8
   result={"paper":pm,"parsed_asset_id":aid,"original_sha":sha,
      "article_type":art,"title":title,"domain":domain,
      "native_tables":total_tables,"numeric_tables":numeric_tables,
      "numeric_cell_candidates_not_measurements":potential,
      "ranking_score":score,"state":state,"domain_match":match,
      "review_reason":"ARTICLE_TYPE_AUTHOR_JATS_NOT_ORIGINAL_RESEARCH" if not research else None}
   all_results.append(result)
   if persist:
    record={"selection_id":"r17:selection:"+hid(aid,domain),
      "parsed_asset_id":aid,"paper_key":pm,"author_sha":sha,
      "article_type":art,"domain":domain,
      "numeric_table_count":numeric_tables,"eligible_cell_count":potential,
      "ranking_score":score,"selection_state":state,
      "reason_json":json.dumps({"title":title,"numeric_tables":numeric_tables,
        "source_type":art,"domain_match":match,"primer_only_tables":primers},ensure_ascii=False,sort_keys=True)}
    c.execute("BEGIN IMMEDIATE")
    try:repo.put(c,"r17_paper_selection",record);c.commit()
    except BaseException:c.rollback();raise
  ordered=sorted(all_results,key=lambda z:(z["ranking_score"],z["paper"]),reverse=True)
  return {"status":"R17_AUTHOR_ORIGINAL_RESEARCH_PRIORITY","domain":domain,
    "sources_examined":len(all_results),
    "original_research":sum(z["article_type"]=="research-article" for z in all_results),
    "review_or_nonoriginal":sum(z["state"]=="REVIEW_OR_ARTICLE_TYPE_HOLD" for z in all_results),
    "shortlist":ordered[:limit],"Gold":0}
 finally:c.close()
