"""R18: rank REAL Europe PMC core metadata before legal acquisition.

Uses only official raw provider JSON already archived by provider_discovery;
metadata signals are research PRIORITY, not scientific/permission approval.
"""
from __future__ import annotations
import json,re,html,sqlite3
from pathlib import Path
from . import repository as repo

ASSAYS={
 "small_molecule":re.compile(r"(?:IC.?50|EC.?50|MIC\b|MBC\b|ATP\b|OCR\b|ROS\b|DPPH|inhibition zone|disc diffusion|disk diffusion|viability|antibacterial|antimicrobial|cytotoxicity|lactate|mitochondri|oxidative|dose.response)",re.I),
 "polymer_biomaterial":re.compile(r"(?:hydrogel|scaffold|nanopartic|bioactive polymer|chitosan|gelation|material formulation|swelling|rheolog|mechanical|modulus|particle size|zeta|encapsulation|cell viability|wound healing|antibacter|nanofiber|release)",re.I)}
COMPARISON=re.compile(r"(?:\bcontrol\b|untreated|vehicle|compared with|versus|vs\.?\b|in combination with|combined with|alone|treated|untreated|dose.response|negative control|positive control)",re.I)
MEASUREMENT=re.compile(r"(?:\bmeasured\b|quantified|determined|evaluated|\bmean\b|\bSD\b|±|\bmm\b|\bµM\b|\bmg/mL\b|\b%\b|IC.?50|MIC\b|at\s+\d+|values? of)",re.I)
REVIEW=re.compile(r"(?:\breview\b|systematic review|meta.analysis|scoping review|literature survey|perspective|bibliometric|editorial)",re.I)
def _raw_meta(workspace,batch_id):
 f=Path(workspace).resolve()/"r15_provider_raw"/(batch_id.replace(":","_")+".json")
 if not f.is_file():return {}
 data=json.loads(f.read_bytes())
 return {str(x.get("pmcid") or x.get("doi") or ""):x
    for x in data.get("resultList",{}).get("result",[])}

def score(item,domain,existing=False):
 title=html.unescape(item.get("title") or "")
 abstract=html.unescape(item.get("abstractText") or "")
 text=title+" "+re.sub(r"<[^>]+>"," ",abstract)
 types=item.get("pubTypeList",{}).get("pubType",[])
 types=" ".join(types if isinstance(types,list) else [str(types)])
 result=0
 factors=[]
 if "research-article" in types.lower():
  result+=90;factors.append("ORIGINAL_RESEARCH_METADATA_TYPE")
 elif "journal article" in types.lower():
  result+=20;factors.append("JOURNAL_ARTICLE_NOT_PRIMARY_CONFIRMED")
 if REVIEW.search(types+" "+title):
  result-=250;factors.append("REVIEW_DEPRIORITIZED")
 if item.get("inEPMC")=="Y":result+=15
 if item.get("isOpenAccess")=="Y":result+=12;factors.append("OA_METADATA_ONLY")
 if item.get("hasSuppl")=="Y":result+=28;factors.append("OFFICIAL_SUPPLEMENT_HINT")
 if item.get("hasData")=="Y":result+=5
 if item.get("hasPDF")=="Y":result+=4
 if item.get("license","").lower() in ("cc by","cc-by","cc-by-4.0"):
  result+=12;factors.append("PROVIDER_LICENSE_METADATA_HINT_ONLY")
 ass=ASSAYS.get(domain)
 if ass and ass.search(text):
  result+=35;factors.append("DOMAIN_EXPERIMENT_ENDPOINT")
 if COMPARISON.search(text):
  result+=27;factors.append("EXPLICIT_COMPARATOR_IN_ABSTRACT")
 if MEASUREMENT.search(text):
  result+=17;factors.append("AUTHOR_QUANTITATIVE_ABSTRACT_HINT")
 if re.search(r"\b(in vitro|in vivo|experiment|assay|microdilution|diffusion|tested|analyzed|synthesized)\b",text,re.I):
  result+=12
 if existing:result-=110;factors.append("ALREADY_PARSED_AVOID_DUPLICATE")
 return result,factors

def ranked_hits(workspace,db,batch_id,hits,domain):
 raw=_raw_meta(workspace,batch_id)
 c=repo.connect(db)
 try:existing={row[0] for row in c.execute("SELECT DISTINCT paper_key FROM r16_parsed_asset")}
 finally:c.close()
 scored=[]
 for hit in hits:
  pmcid,doi=hit[1],hit[2]
  meta=raw.get(str(pmcid)) or raw.get(str(doi)) or {"title":hit[3]}
  s,why=score(meta,domain,existing=pmcid in existing)
  scored.append((s,hit[0],hit,why))
 return [(hit,score_,reasons) for score_,_,hit,reasons in sorted(scored,key=lambda x:(-x[0],x[1]))]

def explain(workspace,db,batch_id,domain,limit=20):
 c=repo.connect(db)
 try:
  hits=c.execute("SELECT hit_id,pmcid,doi,title FROM r15_paper_hit WHERE batch_id=? ORDER BY hit_id",(batch_id,)).fetchall()
 finally:c.close()
 ranked=ranked_hits(workspace,db,batch_id,hits,domain)
 return [{"rank":i+1,"pmcid":h[1],"title":h[3],"score":score_,
          "provider_metadata_signals":why,"scientific_status":"METADATA_ONLY_SCI_HOLD"}
         for i,(h,score_,why) in enumerate(ranked[:limit])]
