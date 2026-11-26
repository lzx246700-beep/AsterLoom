"""R12 bounded author source intake (no implicit browsing, no self-SCI release).

Explicit author original JATS path OR explicit one-PMCID opt-in HTTPS retrieval.
License verified from native author JATS; DOI and PMCID verified from original
article-id nodes. Acquired bytes are copied only into explicit workspace inbox
under stable SHA identity; owner receipt remains LOCAL_OPERATOR_SOURCE_HOLD.
"""
from pathlib import Path
import hashlib,json,os,re,urllib.request,urllib.error
from xml.etree import ElementTree as ET
from .contracts import AuthorizationHold,SourceDrift
from .registry import file_sha,stable
from .operator_local import issue,verify,ACK,LOCAL_ROLE
PMCID=re.compile(r"^PMC[0-9]{3,12}$")
LICENSE_CC_BY=re.compile(r"creativecommons\.org/licenses/by/(?:[234]\.0)?|creative commons attribution(?:\s+[234]\.0\s+international)?\s+license",re.I)
LICENSE_RESTRICTED=re.compile(r"(?:by[-/]nc|noncommercial|non-commercial|by[-/]nd|no derivatives|copyright all rights reserved)",re.I)
URL_ROOT="https://www.ebi.ac.uk/europepmc/webservices/rest/"
def J(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,indent=2)+"\n"
def native_license_literals(root):
 licenses=[]
 for el in root.iter():
  if el.tag.rsplit("}",1)[-1]!="license":continue
  txt=" ".join("".join(el.itertext()).split())
  attrs=" ".join(str(v) for node in el.iter() for k,v in node.attrib.items()
    if node is el or k.rsplit("}",1)[-1]=="href")
  licenses.append(" ".join((attrs,txt)))
 return licenses
def original_metadata(payload,paper=None,doi=None):
 try:root=ET.fromstring(payload)
 except ET.ParseError as exc:raise AuthorizationHold("JATS_ORIGINAL_XML_INVALID") from exc
 def local(t):return t.rsplit("}",1)[-1]
 ids={}
 for el in root.iter():
  if local(el.tag)=="article-id":
   typ=el.attrib.get("pub-id-type")
   if typ in ("pmcid","doi"):
    ids[typ]=" ".join("".join(el.itertext()).split())
 key=ids.get("pmcid")
 if key and not key.startswith("PMC") and key.isdigit():key="PMC"+key
 if key and not PMCID.fullmatch(key):raise AuthorizationHold("AUTHOR_JATS_PMCID_ABSENT_OR_INVALID")
 if not key:
  from .contracts import doi_paper_key
  key=doi_paper_key(ids.get("doi"))
  if not key or paper:raise AuthorizationHold("AUTHOR_JATS_PMCID_OR_DOI_ABSENT_OR_INVALID")
 if paper and paper!=key:raise AuthorizationHold("INPUT_PMCID_DOES_NOT_MATCH_NATIVE_AUTHOR_JATS")
 if doi and ids.get("doi","").lower()!=doi.lower().strip():raise AuthorizationHold("INPUT_DOI_DOES_NOT_MATCH_NATIVE_AUTHOR_JATS")
 # Read nested license links as well as license prose, never body CC links.
 licenses=native_license_literals(root)
 combined=" ".join(licenses)
 if not licenses:return {"paper":key,"doi":ids.get("doi"),"license":None,
    "license_status":"LICENSE_HOLD_NO_NATIVE_JATS_LICENSE"}
 if LICENSE_RESTRICTED.search(combined):
  return {"paper":key,"doi":ids.get("doi"),"license":combined[:500],
    "license_status":"LICENSE_HOLD_NON_CC_BY_OR_RESTRICTED"}
 if not LICENSE_CC_BY.search(combined):
  return {"paper":key,"doi":ids.get("doi"),"license":combined[:500],
    "license_status":"LICENSE_HOLD_UNVERIFIED_NATIVE_CC_BY"}
 return {"paper":key,"doi":ids.get("doi"),"license":combined[:500],
   "license_status":"AUTHOR_NATIVE_CC_BY_VERIFIED"}
def _hold(workspace,key,code,source=None):
 path=Path(workspace).resolve()/"inbox"/"holds"
 path.mkdir(parents=True,exist_ok=True)
 receipt={"paper_key":key,"status":code,"source":str(Path(source).resolve()) if source else None,
  "role":"SOURCE_UNAVAILABLE_OR_LICENSE_HOLD","SCI_approved":False,"Gold":0}
 f=path/(key+"_"+code+".json")
 if not f.exists():f.write_text(J(receipt))
 elif json.loads(f.read_text())!=receipt:raise SourceDrift("R12_SOURCE_HOLD_IDENTITY_CHANGED")
 return receipt
def _bounded_fetch(paper):
 if not PMCID.fullmatch(paper):raise AuthorizationHold("INVALID_EXPLICIT_PMCID")
 url=URL_ROOT+paper+"/fullTextXML"
 request=urllib.request.Request(url,headers={"User-Agent":"R031-Source-Intake/0.12.0 (licensed-author-JATS; single-PMCID)"})
 try:
  with urllib.request.urlopen(request,timeout=9) as res:
   if res.status!=200:raise AuthorizationHold("JATS_SOURCE_UNAVAILABLE_HTTP_"+str(res.status))
   if not res.url.startswith("https://www.ebi.ac.uk/europepmc/"):
    raise AuthorizationHold("JATS_SOURCE_REDIRECT_NOT_APPROVED")
   payload=res.read(6*1024*1024+1)
 except (OSError,urllib.error.URLError) as exc:raise AuthorizationHold("JATS_SOURCE_UNAVAILABLE_NETWORK") from exc
 if len(payload)>6*1024*1024:raise AuthorizationHold("JATS_SOURCE_SIZE_LIMIT_6MiB")
 return payload,url
def prepare(workspace,source=None,pmcid=None,doi=None,fetch_jats=False):
 if pmcid is not None and not PMCID.fullmatch(pmcid):
  raise AuthorizationHold("INVALID_EXPLICIT_PMCID")
 base=Path(workspace).resolve()
 if (source is None)==(not fetch_jats):
  if not (source is None and not fetch_jats and pmcid):
   raise AuthorizationHold("EXACTLY_ONE_EXPLICIT_SOURCE_OR_OPT_IN_FETCH_REQUIRED")
 if source is None and not fetch_jats:
  return _hold(base,pmcid,"SOURCE_UNAVAILABLE_EXPLICIT_FETCH_NOT_AUTHORIZED")
 if fetch_jats and (not pmcid or source):
  raise AuthorizationHold("EXPLICIT_SINGLE_PMCID_REQUIRED_FOR_FETCH")
 if source:
  src=Path(source).resolve()
  if not src.is_file() or src.suffix.lower() not in (".xml",".nxml"):
   raise AuthorizationHold("SOURCE_PREPARE_LOCAL_MUST_BE_EXPLICIT_AUTHOR_JATS")
  if src.stat().st_size>6*1024*1024:raise AuthorizationHold("SOURCE_TOO_LARGE")
  payload=src.read_bytes();url=None
 else:
  try:payload,url=_bounded_fetch(pmcid)
  except AuthorizationHold as exc:
   return _hold(base,pmcid,str(exc))
 info=original_metadata(payload,pmcid,doi)
 author_sha=hashlib.sha256(payload).hexdigest()
 if info["license_status"]!="AUTHOR_NATIVE_CC_BY_VERIFIED":
  out=_hold(base,info["paper"],info["license_status"],source)
  out["original_sha256"]=author_sha
  out["doi"]=info["doi"]
  return out
 dest=base/"inbox"/"author_sources";dest.mkdir(parents=True,exist_ok=True)
 f=dest/(info["paper"]+"_"+author_sha[:16]+".xml")
 if f.exists():
  if file_sha(f)!=author_sha:raise SourceDrift("PUBLISHER_REVISION_OR_INBOX_SHA_COLLISION")
 else:
  fd=os.open(f,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  try:
   with os.fdopen(fd,"wb") as writer:writer.write(payload)
  except BaseException:
   f.unlink(missing_ok=True);raise
 release_id="local-r12-"+info["paper"].lower().replace("_","")+"-"+author_sha[:12]
 receipt_path=base/"operator_releases"/(release_id+".json")
 if not receipt_path.exists():
  owner=issue(base,release_id,info["paper"],info["doi"],f,ACK)
 else:
  owner={"sha256":file_sha(receipt_path)}
 rows,receipt_sha=verify(base,release_id,[info["paper"]+"|author_JATS"])
 assert rows[0]["sha256"]==author_sha
 manifest={"batch_contract":"R031_R09_BATCH_V1","batch_id":"R12_SOURCE_"+info["paper"]+"_"+author_sha[:10],
  "release_id":release_id,"source_ids":[info["paper"]+"|author_JATS"],
  "science_Gold":0,"user_training_approved":False,"new_unseen_sci":False}
 path=base/"inbox"/"manifests";path.mkdir(parents=True,exist_ok=True)
 m=path/(manifest["batch_id"]+".json")
 if not m.exists():m.write_text(J(manifest))
 elif json.loads(m.read_text())!=manifest:raise SourceDrift("PREPARED_BATCH_ALREADY_DIFFERS")
 receipt={"status":"LOCAL_OPERATOR_SOURCE_HOLD_REGISTERED",
  "pmcid":info["paper"] if info["paper"].startswith("PMC") else None,"paper_key":info["paper"],"doi":info["doi"],
  "original_sha256":author_sha,"original_bytes":len(payload),
  "original_source":str(f),"license_status":info["license_status"],
  "license_excerpt":info["license"],"source_url":url,
  "source_role":LOCAL_ROLE,"release_id":release_id,"owner_receipt_sha256":receipt_sha,
  "manifest":str(m),"manifest_sha256":file_sha(m),
  "SCI_approved":False,"training_approved":False,"Gold":0}
 receiptfile=base/"inbox"/"receipts";receiptfile.mkdir(exist_ok=True)
 result=receiptfile/(info["paper"]+"_"+author_sha[:12]+".json")
 if not result.exists():result.write_text(J(receipt))
 elif json.loads(result.read_text())!=receipt:raise SourceDrift("PREPARED_SOURCE_RECEIPT_CHANGED")
 return receipt
