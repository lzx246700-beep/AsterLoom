"""R21 bounded, optional multi-provider identity checks in the SAME raw-receipt SQLite.

Europe PMC remains primary legal original acquisition; OpenAlex search is
metadata-only; Crossref DOI lookup gives independent metadata, NOT a license
or automatic paper retraction clearance.
"""
from __future__ import annotations
from . import repository as repo
from .provider_discovery import discover
def supplement(workspace,primary_response,query,max_openalex=5,max_crossref=2,
               use_openalex=True,use_crossref=True):
 home=__import__("pathlib").Path(workspace).resolve()
 c=repo.connect(home/"science.sqlite3")
 try:
  batches=primary_response.get("source_harvest",primary_response).get("page_runs",[])
  ids=[x.get("query_batch_id") for x in batches if x.get("query_batch_id")]
  dois=[];titles=[]
  for bid in ids:
   for (d,) in c.execute("SELECT doi FROM r15_paper_hit WHERE batch_id=? AND doi IS NOT NULL ORDER BY hit_id LIMIT 20",(bid,)):
    if d not in dois:dois.append(d)
   for (title,) in c.execute("SELECT title FROM r15_paper_hit WHERE batch_id=? AND pmcid IS NOT NULL AND title IS NOT NULL ORDER BY hit_id LIMIT 2",(bid,)):
    if title not in titles:titles.append(title)
 finally:c.close()
 reports=[]
 if use_openalex:
  # Europe PMC formal query operators are not portable to OpenAlex free text.
  # Derive a conservative bounded free-text query for identity corroboration.
  import re
  text=re.sub(r"\b(?:OPEN_ACCESS|FIRST_PDATE|PUB_TYPE):[^\s)]+","",query,flags=re.I)
  text=re.sub(r"\b(?:AND|OR|NOT)\b|[()]"," ",text,flags=re.I)
  free=" ".join(text.split())[:120]
  # For exact PMCID lookups, feed the actual Europe PMC paper title to
  # OpenAlex instead of an unsupported PMCID: field. Metadata is still
  # independently sourced and may produce zero honest matches.
  if re.search(r"\bPMCID:PMC\d+\b",query,re.I) and ids:
   c=repo.connect(home/"science.sqlite3")
   try:
    row=c.execute("SELECT title FROM r15_paper_hit WHERE batch_id=? AND title IS NOT NULL LIMIT 1",(ids[0],)).fetchone()
    if row and row[0]:free=str(row[0])[:120]
   finally:c.close()
  if free:
   try:
    obj=discover(home,home/"science.sqlite3",query=free,provider="OPENALEX",max_results=max_openalex)
    matching=[h for h in obj["hits"] if (h.get("doi") or "").lower() in {d.lower() for d in dois}]
    reports.append({"provider":"OPENALEX","primary_doi_matched":bool(matching),
      "identity_matches":len(matching),"state":obj["status"],
      "metadata_hits":obj["metadata_hits"],"raw_sha":obj["raw_sha"],
      "batch_id":obj["batch_id"],"role":"METADATA_ONLY_NOT_LICENSE"})
   except Exception as err:reports.append({"provider":"OPENALEX","state":"METADATA_ONLY_HOLD",
     "reason":type(err).__name__+":"+str(err)[:170]})
 if use_crossref:
  for doi in dois[:max_crossref]:
   try:
    obj=discover(home,home/"science.sqlite3",doi_list=doi,provider="CROSSREF",max_results=1)
    confirmed=any((hit.get("doi") or "").lower()==doi.lower() for hit in obj["hits"])
    reports.append({"provider":"CROSSREF","doi":doi,"primary_doi_matched":confirmed,
      "state":obj["status"],
     "metadata_hits":obj["metadata_hits"],"raw_sha":obj["raw_sha"],
     "batch_id":obj["batch_id"],"role":"DOI_METADATA_ONLY_NOT_LICENSE"})
   except Exception as err:reports.append({"provider":"CROSSREF","doi":doi,"state":"METADATA_HOLD",
     "reason":type(err).__name__+":"+str(err)[:170]})
 return {"status":"R21_BOUNDED_MULTIPROVIDER_METADATA_VERIFICATION",
  "primary_legal_source":"EUROPE_PMC","providers_checked":reports,
  "legal_access_not_inferred_from_metadata":True,"Gold":0}
