"""R12 original author SI CSV/XLSX to main-paper JATS provenance gate.

An author SI file only becomes an eligible numeric contrast source when the
*same native JATS paper* explicitly links its original SI filename in
supplementary-material. Otherwise only source cells and source-only holds are
staged. This never asserts independent SCI approval or training permission.
"""
from pathlib import Path
import hashlib,json,os,stat
from urllib.parse import urlparse,unquote
from xml.etree import ElementTree as ET
from .contracts import AuthorizationHold,SourceDrift
from .registry import stable,file_sha
from .operator_local import ACK,LOCAL_ROLE,verify
from .source_intake import original_metadata,J
from .assay_graph import source_tables
from .numeric_builder import build
def _source_external_href(parent_jats,si_original_name):
 root=ET.parse(parent_jats).getroot()
 def local(tag):return tag.rsplit("}",1)[-1]
 matches=[]
 for el in root.iter():
  if local(el.tag) not in ("supplementary-material","ext-link","media"):continue
  for key,val in el.attrib.items():
   if local(key) not in ("href","filename","xlink:href"):continue
   value=unquote(str(val))
   if Path(urlparse(value).path).name==si_original_name:
    loc=el.attrib.get("id") or "unnamed-native-supplement"
    matches.append((loc,value))
 return matches
def _si_link_path(workspace,release_id):
 return Path(workspace).resolve()/"si_links"/(release_id+".json")
def si_prepare(workspace,paper,original_si,parent_jats,ack):
 if ack!=ACK:raise AuthorizationHold("SI_OWNER_REQUIRES_EXPLICIT_NO_SCI_NO_GOLD_ACK")
 path=Path(original_si).resolve();parent=Path(parent_jats).resolve()
 if not path.is_file() or path.suffix.lower() not in (".xlsx",".csv"):
  raise AuthorizationHold("SI_FORMAT_REQUIRES_NATIVE_AUTHOR_XLSX_OR_CSV")
 if not parent.is_file() or parent.suffix.lower() not in (".xml",".nxml"):
  raise AuthorizationHold("SI_PARENT_REQUIRES_AUTHOR_NATIVE_JATS")
 original_name=path.name
 parentbytes=parent.read_bytes()
 metadata=original_metadata(parentbytes,paper)
 if metadata["license_status"]!="AUTHOR_NATIVE_CC_BY_VERIFIED":
  return {"status":"LICENSE_HOLD","reason":metadata["license_status"],
    "SCI_approved":False,"Gold":0}
 links=_source_external_href(parent,original_name)
 if len(links)!=1:
  return {"status":"PARENT_JATS_OR_SUPPLEMENT_LINK_HOLD",
   "reason":"AUTHOR_JATS_DOES_NOT_UNAMBIGUOUSLY_LINK_SI_ORIGINAL_FILENAME",
   "source_si_sha":file_sha(path),"parent_jats_sha":file_sha(parent),
   "original_si_filename":original_name,"link_matches":len(links),
   "SCI_approved":False,"Gold":0}
 body=path.read_bytes()
 if len(body)>50*1024*1024:raise AuthorizationHold("SI_NATIVE_BYTES_TOO_LARGE")
 digest=hashlib.sha256(body).hexdigest()
 root=Path(workspace).resolve();files=root/"inbox"/"author_sources"
 files.mkdir(parents=True,exist_ok=True)
 dest=files/(paper+"_"+digest[:16]+path.suffix.lower())
 if dest.exists():
  if file_sha(dest)!=digest:raise SourceDrift("SI_ORIGINAL_WORKSPACE_COPY_SHA_CHANGED")
 else:
  fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  try:
   with os.fdopen(fd,"wb") as f:f.write(body)
  except BaseException:dest.unlink(missing_ok=True);raise
 key="author_SI_"+path.suffix.lower().lstrip(".").upper()
 release_id="local-r12-si-"+paper.lower()+"-"+digest[:12]
 receipt_path=root/"operator_releases"/(release_id+".json")
 row={"source_id":paper+"|"+key,"paper_key":paper,
  "doi":metadata["doi"],"asset_key":key,"source_path":str(dest),
  "sha256":digest,"license":metadata["license"],"role":LOCAL_ROLE,
  "authorization_basis":"NATIVE_PARENT_JATS_SUPPLEMENT_LINK+OWNER_RECEIPT_SOURCE_HOLD",
  "source_type":path.suffix.lower()}
 grant={"contract":"R031_LOCAL_OPERATOR_RELEASE_V1","release_id":release_id,
   "role":LOCAL_ROLE,"operator_os_uid":os.getuid(),
   "operator_acknowledged":ACK,"author_source":[row],
   "science_approved":False,"training_approved":False,"Gold":0}
 receipt_path.parent.mkdir(parents=True,exist_ok=True)
 if not receipt_path.exists():
  fd=os.open(receipt_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  try:
   with os.fdopen(fd,"w") as f:f.write(J(grant))
  except BaseException:receipt_path.unlink(missing_ok=True);raise
 elif json.loads(receipt_path.read_text())!=grant:
  raise SourceDrift("AUTHOR_SI_OPERATOR_RECEIPT_CHANGED")
 validated,receiptsha=verify(root,release_id,[row["source_id"]])
 assert len(validated)==1 and validated[0]["sha256"]==digest
 native_id,href=links[0]
 link={"contract":"R12_NATIVE_AUTHOR_SI_LINK_V1","release_id":release_id,
  "paper":paper,"doi":metadata["doi"],
  "original_si_name":original_name,"original_si_sha256":digest,
  "native_original_si_path":str(dest),
  "original_parent_jats_path":str(parent),
  "original_parent_jats_sha256":file_sha(parent),
  "native_supplement_id":native_id,"native_supplement_href":href,
  "operator_receipt_sha256":receiptsha,
  "link_state":"AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED",
  "SCI_approved":False,"training_approved":False,"Gold":0}
 f=_si_link_path(root,release_id);f.parent.mkdir(parents=True,exist_ok=True)
 if not f.exists():
  fd=os.open(f,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
  with os.fdopen(fd,"w") as o:o.write(J(link))
 elif json.loads(f.read_text())!=link:raise SourceDrift("NATIVE_SI_LINK_RECEIPT_DRIFT")
 batch={"batch_contract":"R031_R09_BATCH_V1",
  "batch_id":"R12_NATIVE_SI_"+paper+"_"+digest[:10],
  "release_id":release_id,"source_ids":[row["source_id"]],
  "science_Gold":0,"user_training_approved":False,"new_unseen_sci":False}
 folder=root/"inbox"/"manifests";folder.mkdir(parents=True,exist_ok=True)
 manifest=folder/(batch["batch_id"]+".json")
 if not manifest.exists():manifest.write_text(J(batch))
 elif json.loads(manifest.read_text())!=batch:raise SourceDrift("SOURCE_SI_MANIFEST_CHANGED")
 return {"status":"LOCAL_OPERATOR_SI_NATIVE_PARENT_LINK_VERIFIED_SOURCE_HOLD",
  "manifest":str(manifest),"source_original_sha":digest,
  "parent_original_SHA":file_sha(parent),"native_supplement_id":native_id,
  "native_supplement_href":href,"original_SI_filename":original_name,
  "source_type":path.suffix.lower(),"link_receipt":str(f),
  "link_sha256":file_sha(f),"SCI_approved":False,"Gold":0}
def inspect_si(asset,workspace=None):
 """True source provenance for a validated SourceAsset; no model-driven joins."""
 if asset.source_type not in (".csv",".xlsx"):
  return {"source_type":asset.source_type,"capability":"NATIVE_JATS",
    "linked":None}
 info={"source_type":asset.source_type,"capability":"SI_NATIVE_CELL_PARSE_ONLY",
   "source_sha256":asset.sha256,"link_status":"PARENT_JATS_OR_SUPPLEMENT_LINK_HOLD",
   "parent_jats_sha":None,"native_supplement_id":None,
   "native_supplement_href":None,"receipt_sha":None,"parent_jats_path":None}
 if workspace:
  src=Path(workspace).resolve()/"si_links"
  for f in sorted(src.glob("local-r12-si-"+asset.paper_key.lower()+"-*.json")):
   if not f.is_file():continue
   if stat.S_IMODE(f.stat().st_mode)&0o077:continue
   link=json.loads(f.read_text())
   if (link.get("contract")!="R12_NATIVE_AUTHOR_SI_LINK_V1" or
       link.get("original_si_sha256")!=asset.sha256 or
       link.get("paper")!=asset.paper_key or
       link.get("SCI_approved") is not False or link.get("Gold")!=0):continue
   parent=Path(link["original_parent_jats_path"])
   if not parent.is_file() or file_sha(parent)!=link["original_parent_jats_sha256"]:
    raise SourceDrift("SI_LINK_PARENT_JATS_BYTES_DRIFT")
   original_name=link["original_si_name"]
   matches=_source_external_href(parent,original_name)
   if len(matches)!=1 or matches[0]!=(link["native_supplement_id"],link["native_supplement_href"]):
    raise SourceDrift("SI_PARENT_NATIVE_SUPPLEMENT_LINK_CHANGED")
   info.update({"capability":"SI_NATIVE_NUMERIC_ALLOWED_SOURCE_HOLD",
     "link_status":"AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED",
     "parent_jats_sha":link["original_parent_jats_sha256"],
     "native_supplement_id":link["native_supplement_id"],
     "native_supplement_href":link["native_supplement_href"],
     "receipt_sha":file_sha(f),"parent_jats_path":str(parent)})
   break
 return info
def native_capability(asset,workspace=None):
 s=inspect_si(asset,workspace)
 if asset.source_type not in (".xlsx",".csv"):
  return {"source_type":asset.source_type,"capability":"JATS_NATIVE",
    "can_native_numeric":True,"can_source_pair":True,"hold":False}
 tables=source_tables(asset.source_path,asset.sha256)
 source=build(asset)
 count=sum(len(x["cells"]) for x in tables)
 return {"source_type":asset.source_type,"sheets_or_tables":len(tables),
   "native_nonempty_cells":count,"can_native_numeric":len(source["atoms"])>0,
   "can_source_pair":len(source["pairs"])>0 and
        s["link_status"]=="AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED",
   "candidate_numeric_atoms":len(source["atoms"]),
   "candidate_pairs_raw_unqualified":len(source["pairs"]),
   "parent_link_state":s["link_status"],
   "hold":s["link_status"]!="AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED",
   "reason":"NO_VERIFIABLE_PARENT_NATIVE_SI_LINK" if
    s["link_status"]!="AUTHOR_NATIVE_SUPPLEMENT_LINK_VERIFIED" else
    "SCI_REVIEW_PENDING_SOURCE_ONLY",
   "Gold":0}
