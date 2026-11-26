import json
import re
from pathlib import Path
from urllib.parse import urlencode, quote, urlparse, unquote
from lxml import etree
from .ir import PaperHit, LicensedAuthorSource
from .transport import sha, asset

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
CLOUD = "https://pmc-oa-opendata.s3.amazonaws.com"

def pmcid_valid(pmcid):
    if not re.fullmatch(r"PMC[1-9][0-9]*", pmcid): raise ValueError("Invalid PMCID")
    return pmcid

def discover(transport, provider, query, cursor="*", limit=10):
    if not 1 <= limit <= 25: raise ValueError("bounded limit: 1..25")
    if provider == "openalex":
        url = "https://api.openalex.org/works?" + urlencode({"search":query,"filter":"is_oa:true","cursor":cursor,"per-page":limit})
    elif provider == "europepmc":
        url = EPMC + "/search?" + urlencode({"query":query,"format":"json","resultType":"core","pageSize":limit,"cursorMark":cursor})
    elif provider == "crossref":
        url = "https://api.crossref.org/works?" + urlencode({"query":query,"rows":limit,"cursor":cursor})
    else: raise ValueError("Unsupported provider")
    p, receipt = transport.get(url,"json")
    if receipt["state"] != "FETCHED": return {"hits":[],"receipt":receipt,"next_cursor":None}
    data = json.loads(p.read_bytes())
    hits = []
    records = data.get("results",[]) if provider == "openalex" else data.get("resultList",{}).get("result",[]) if provider == "europepmc" else data.get("message",{}).get("items",[])
    for row in records:
        ids = row.get("ids",{})
        doi = row.get("doi") or row.get("DOI") or ids.get("doi")
        if doi: doi = re.sub(r"^https?://(dx\.)?doi.org/","",doi).lower()
        pmcid = row.get("pmcid") or ids.get("pmcid")
        if pmcid:
            pmcid = pmcid.rsplit("/",1)[-1]
            if str(pmcid).isdigit(): pmcid = "PMC"+str(pmcid)
        pmid = row.get("pmid") or ids.get("pmid")
        if pmid: pmid = str(pmid).rstrip("/").rsplit("/",1)[-1]
        title = row.get("title") or row.get("display_name", "")
        if isinstance(title,list): title = " ".join(title)
        hits.append(PaperHit(provider,str(row.get("id") or doi),title,doi,pmcid,pmid,receipt["sha256"],sha(query.encode())))
    nxt = data.get("meta",{}).get("next_cursor") if provider == "openalex" else data.get("nextCursorMark") if provider == "europepmc" else data.get("message",{}).get("next-cursor")
    return {"hits":hits,"receipt":receipt,"next_cursor":nxt}

def crossref_doi(transport,doi):
    # Keep exact provider record including relation/update-to/license; absent means UNKNOWN.
    return transport.get("https://api.crossref.org/works/"+quote(doi,safe=""),"json")

def epmc_fulltext(transport,pmcid):
    pmcid_valid(pmcid)
    return transport.get(f"{EPMC}/{pmcid}/fullTextXML","xml")

def cloud_https(uri):
    u = urlparse(uri)
    if u.scheme != "s3" or u.netloc != "pmc-oa-opendata": raise ValueError("Invalid cloud object")
    if "/deprecated/" in u.path or ".." in unquote(u.path).split("/"): raise ValueError("Legacy/unsafe path")
    return CLOUD+u.path  # md5 query retained in metadata; integrity verified separately

def cloud_metadata(transport,pmcid,version=1):
    pmcid_valid(pmcid)
    if not isinstance(version,int) or version<1: raise ValueError("Invalid version")
    return transport.get(f"{CLOUD}/{pmcid}.{version}/{pmcid}.{version}.json","json")

def license_jats(path):
    root = etree.parse(str(path),etree.XMLParser(resolve_entities=False,no_network=True))
    nodes = root.xpath("//article-meta/permissions/license")
    literal = "\n".join("".join(n.itertext()) for n in nodes)
    hrefs = [v for n in nodes for k,v in n.attrib.items() if k.endswith('href')]
    return literal, hrefs

def acquire_cloud(transport,pmcid,destination,pdf=False,supplements=True,version=1):
    p, rec = cloud_metadata(transport,pmcid,version)
    result = {"paper_id":pmcid,"metadata_receipt":rec,"sources":[],"attachments":[],"holds":[]}
    if rec["state"] != "FETCHED":
        result["holds"].append("METADATA_ACCESS_HOLD"); return result
    meta = json.loads(p.read_bytes())
    result["metadata"] = meta
    # Explicit conservative allowlist; OA boolean alone never grants rights.
    license_code = meta.get("license_code")
    if license_code not in {"CC BY","CC BY-SA","CC0","CC BY-NC","CC BY-NC-SA","CC BY-NC-ND","CC BY-ND"}:
        result["holds"].append("LICENSE_HOLD"); return result
    destination = Path(destination); destination.mkdir(parents=True,exist_ok=True)
    uris = [("jats",meta.get("xml_url"))]
    if pdf: uris.append(("pdf",meta.get("pdf_url")))
    if supplements:
        uris += [("supplement",u) for u in meta.get("media_urls",[]) if urlparse(u).path.lower().endswith((".xlsx",".csv",".pdf"))]
    for kind,uri in uris:
        if not uri:
            result["holds"].append(f"{kind.upper()}_MISSING_HOLD"); continue
        from urllib.parse import parse_qs
        q = parse_qs(urlparse(uri).query)
        url = cloud_https(uri)
        raw,receipt = transport.get(url,"xml" if kind=="jats" else "pdf" if kind=="pdf" else None)
        item = {"kind":kind,"cloud_uri_literal":uri,"license_literal":license_code,"receipt":receipt}
        if receipt["state"] == "FETCHED":
            import hashlib
            if q.get("md5") and hashlib.md5(raw.read_bytes()).hexdigest() != q["md5"][0]:
                item["state"] = "PROVIDER_MD5_CONFLICT_HOLD"
            else:
                target = destination / Path(urlparse(url).path).name
                target.write_bytes(raw.read_bytes())
                item.update(asset=asset(target).__dict__,state="SOURCE_REGISTERED")
        (result["attachments"] if kind=="supplement" else result["sources"]).append(item)
    return result
