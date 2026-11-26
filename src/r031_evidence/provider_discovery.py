"""R15 PaperHit discovery from official provider JSON with immutable raw receipts."""
from __future__ import annotations
import hashlib,json,urllib.parse
from pathlib import Path
from . import repository as repo
from .provider_client import read_official

def digest(x):return hashlib.sha256(x if isinstance(x,bytes) else x.encode()).hexdigest()
def _id(value,prefix):
    if not value:return None
    s=str(value).strip()
    if s.lower().startswith(prefix):return s[len(prefix):]
    return s
def _pmcid(x):
    raw=_id(x,"https://www.ncbi.nlm.nih.gov/pmc/articles/")
    if raw is None:return None
    raw=raw.strip("/")
    if raw.isdigit():return "PMC"+raw
    if raw.startswith("PMC") and raw[3:].isdigit():return raw
    return None
def _normalize(item,provider):
    if provider=="EUROPE_PMC":
        return {"pmcid":item.get("pmcid"),"doi":item.get("doi"),"pmid":item.get("id") if str(item.get("id") or "").isdigit() else None,
          "openalex_id":None,"title":item.get("title"),"year":item.get("pubYear")}
    if provider=="OPENALEX":
        ids=item.get("ids") or {}
        return {"pmcid":_pmcid(ids.get("pmcid")),
          "doi":_id(ids.get("doi") or item.get("doi"),"https://doi.org/"),
          "pmid":_id(ids.get("pmid"),"https://pubmed.ncbi.nlm.nih.gov/"),
          "openalex_id":item.get("id"),"title":item.get("title"),
          "year":item.get("publication_year")}
    return {"pmcid":None,"doi":item.get("DOI"),"pmid":None,
        "openalex_id":None,"title":item.get("title",[""])[0],"year":item.get("published",{}).get("date-parts",[[None]])[0][0]}
def discover(workspace,db,query=None,pmcid_list=None,doi_list=None,provider="EUROPE_PMC",max_results=25,reader=None,cursor_mark=None):
    """R30: persist ALL occurrences, identities/work and cursor atomically."""
    from .r30_ledger import connect,progress,record_page,normalize_doi
    from .r30_providers import get
    if sum(bool(x) for x in (query,pmcid_list,doi_list))!=1:raise ValueError("R15_DISCOVERY_EXACTLY_ONE_SEARCH_MODE")
    if not 1<=max_results<=100:raise ValueError("R18_DISCOVERY_BOUNDED_MAX_100")
    entries=[x.strip() for x in (pmcid_list or doi_list or "").split(",") if x.strip()]
    if entries and len(entries)>30:raise ValueError("R15_EXPLICIT_ID_LIST_TOO_LARGE")
    if pmcid_list:
        if provider!="EUROPE_PMC":raise ValueError("R15_PMCID_LOOKUP_EUROPE_PMC_ONLY")
        search=" OR ".join("EXT_ID:"+v for v in entries)
    elif doi_list:
        if provider!="CROSSREF" or len(entries)!=1:raise ValueError("R15_CROSSREF_EXACTLY_ONE_DOI_PER_LOOKUP")
        search=entries[0]
    else:search=query
    if len(search)>3000:raise ValueError("R30_SEARCH_QUERY_TOO_LONG")
    if Path(db).resolve()!=(Path(workspace).resolve()/"science.sqlite3"):raise ValueError("R30_DISCOVERY_DATABASE_BINDING_MISMATCH")
    initialized=connect(workspace);initialized.close()
    saved=progress(workspace,provider,search)
    if cursor_mark is None and saved['exhausted']:
        return dict(status="EXHAUSTED",metadata_hits=0,hits=[],next_cursor=None,provider=provider,Gold=0)
    cursor=cursor_mark if cursor_mark is not None else saved['cursor'] or '*'
    adapter=get(provider);response=adapter.search(workspace,search,cursor,max_results,reader)
    reply=response['response'];content=reply['body'];raw_sha=digest(content) if content else None
    if reply['status']!=200 or not content:
        return dict(status="METADATA_ONLY_PROVIDER_HOLD_"+str(reply['status']),metadata_hits=0,hits=[],next_cursor=cursor,provider=provider,reason=reply.get('reason'),retry_at=reply.get('retry_at'),Gold=0)
    hits=response['hits'];qsha=digest(provider+'|'+search)
    bid="r15:query:"+digest(qsha+'|'+raw_sha+'|200|R30_OCCURRENCES_V1')[:28]
    rawdir=Path(workspace)/"r15_provider_raw";rawdir.mkdir(parents=True,exist_ok=True)
    output=rawdir/(bid.replace(':','_')+'.json')
    if output.exists() and digest(output.read_bytes())!=raw_sha:raise RuntimeError("R15_PROVIDER_RAW_SHA_DRIFT")
    if not output.exists():output.write_bytes(content)
    repo.configure(Path(workspace).resolve())
    c=repo.connect(db)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            repo.put(c,"r15_discovery_batch",dict(batch_id=bid,query_sha=qsha,provider=provider,query_literal=search,raw_sha=raw_sha,result_state="DISCOVERED"))
            legacy=[];seen=set()
            for i,hit in enumerate(hits):
                key=(hit.get('doi') or hit.get('pmcid') or hit.get('openalex_id') or hit.get('pmid') or str(i)).lower()
                hitid="r15:hit:"+digest(bid+'|'+key)[:28];legacy.append(hitid)
                if key in seen:continue
                seen.add(key)
                row={k:hit.get(k) for k in ('pmcid','doi','pmid','openalex_id','title','year')}
                row=dict(hit_id=hitid,batch_id=bid,**row,metadata_sha=digest(json.dumps(hit['provider_record'],sort_keys=True,ensure_ascii=False)),access_state="METADATA_ONLY_LICENSE_NOT_VERIFIED")
                repo.put(c,"r15_paper_hit",row)
            page=record_page(c,provider=provider,query=search,cursor=cursor,next_cursor=response['next_cursor'],raw_sha=raw_sha,raw_path=output,hits=hits,legacy_ids=legacy)
            c.commit()
        except BaseException:c.rollback();raise
    finally:c.close()
    return dict(status="DISCOVERED",batch_id=bid,page_id=page,provider=provider,provider_url=response['url'],query_sha=qsha,raw_sha=raw_sha,metadata_hits=len(hits),hits=hits,next_cursor=response['next_cursor'],cursor_committed_with_all_hits=True,Gold=0)
